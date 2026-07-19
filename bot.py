import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

import config
import gsheets
from gdrive import DriveClient
from gemini import parse_receipt, parse_income_text, parse_followup, parse_edit

PENDING_TTL  = 600   # 確定前の会話が10分で失効
RECORDED_TTL = 3600  # 記録済みエントリを修正できる猶予（1時間）

NOTE_NEW    = 'AI自動記録（Gemini）'
NOTE_EDITED = 'AI自動記録（Gemini）／修正あり'

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# channel_id → Profile / DriveClient（__main__ の setup で埋める）
_profiles: dict[int, config.Profile] = {}
_drives:   dict[int, DriveClient]    = {}
# 既に月シート一式を用意済みのスプレッドシートID
_initialized_sheets: set[str] = set()


@dataclass
class PendingEntry:
    entry_type: str             # 'expense' | 'income'
    data: dict                  # date, content, amount
    state: str                  # 'info' | 'confirm'
    ts: float = field(default_factory=time.time)
    image_data: Optional[bytes] = None
    mime_type:  Optional[str]   = None
    original_ext: Optional[str] = None  # jpg / png など
    store: Optional[str] = None         # data['content'] の組み立て元
    items: list = field(default_factory=list)


@dataclass
class RecordedEntry:
    """記録済みエントリ。あとから修正指示を受けて上書きするために保持する。"""
    entry_type: str
    data: dict                  # date, content, amount
    sheet_name: str             # 記録した月シート名（例: '７月'）
    row: int                    # そのシート上の行番号
    store: Optional[str] = None
    items: list = field(default_factory=list)
    drive_file_id: Optional[str] = None
    drive_link: str = ''
    filename: Optional[str] = None
    original_ext: Optional[str] = None
    ts: float = field(default_factory=time.time)


# (channel_id, user_id) → PendingEntry
_pending: dict[tuple[int, int], PendingEntry] = {}
# (channel_id, user_id) → 直近に記録した RecordedEntry
_recorded: dict[tuple[int, int], RecordedEntry] = {}


# ── ヘルパー ──────────────────────────────────────────────

def _kind_ja(entry_type: str) -> str:
    return gsheets.KIND_EXPENSE if entry_type == 'expense' else gsheets.KIND_INCOME


def _missing(data: dict) -> list[str]:
    labels = {'date': '日付', 'content': '内容', 'amount': '金額'}
    return [labels[k] for k in ('date', 'content', 'amount') if not data.get(k)]


def _fmt_amount(val) -> str:
    try:
        return f'¥{int(float(str(val))):,}'
    except (TypeError, ValueError):
        return str(val)


def _to_int(val) -> Optional[int]:
    try:
        return int(float(str(val)))
    except (TypeError, ValueError):
        return None


def _amount_cell(val):
    """シートに書く金額。数値化できれば数値、無理ならそのまま。"""
    n = _to_int(val)
    return n if n is not None else val


def _render_content(store, items: list) -> Optional[str]:
    """店名と品目から「内容」列の文字列を組み立てる。"""
    items_str = '、'.join(
        f"{i['name']}(¥{i['price']})" for i in (items or []) if i.get('name')
    )
    store = str(store) if store else ''
    return f'{store}（{items_str}）' if items_str else (store or None)


def _summary(entry_type: str, data: dict) -> str:
    kind = _kind_ja(entry_type)
    lines = [
        f'**【{kind}】**',
        f'📅 日付 : {data.get("date") or "❓ 不明"}',
        f'📝 内容 : {data.get("content") or "❓ 不明"}',
        f'💴 金額 : {_fmt_amount(data["amount"]) if data.get("amount") else "❓ 不明"}',
    ]
    return '\n'.join(lines)


def _build_message(entry: PendingEntry) -> str:
    miss = _missing(entry.data)
    base = _summary(entry.entry_type, entry.data)
    if miss:
        return (
            f'{base}\n\n'
            f'⚠️ **不足項目**: {" / ".join(miss)}\n'
            f'まとめて教えてもらってもOKです！\n'
            f'（例:「日付は昨日で金額は2800円です」）'
        )
    return (
        f'{base}\n\n'
        f'✅ この内容で記録しますか？\n'
        f'「はい」で確定 ／ 「いいえ」でキャンセル\n'
        f'修正があれば自然に教えてください'
    )


def _make_filename(date, content, ext) -> str:
    date_raw  = str(date or 'unknown').replace('-', '')
    # 内容の先頭部分（括弧の前まで）を店名として使う
    content   = str(content or 'unknown')
    store_raw = content.split('（')[0].strip().replace(' ', '_').replace('/', '_')[:20]
    return f'{date_raw}_{store_raw}.{ext or "jpg"}'


def _ensure_workbook(sheet_id: str) -> None:
    """月シート一式を用意する（未初期化のスプレッドシートのみ・冪等）。"""
    if sheet_id in _initialized_sheets:
        return
    gsheets.init_workbook(sheet_id)
    _initialized_sheets.add(sheet_id)


# ── イベントハンドラ ──────────────────────────────────────

@bot.event
async def on_ready():
    print(f'起動: {bot.user}')
    for p in _profiles.values():
        print(f'  [{p.name}] ch={p.channel_id} mode={p.mode} sheet={p.sheet_id[:12]}…')


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    key  = (message.channel.id, message.author.id)
    prof = _profiles.get(message.channel.id)

    # ── 進行中の会話があれば優先処理 ──
    if key in _pending:
        entry = _pending[key]
        if time.time() - entry.ts > PENDING_TTL:
            del _pending[key]
            await message.reply('⏱ タイムアウトしました。最初からやり直してください。')
            return
        await _handle_followup(message, key, entry)
        return

    if prof is None:
        await bot.process_commands(message)
        return

    # ── 新規: レシート画像（支出） ──
    if prof.accepts_receipt:
        imgs = [a for a in message.attachments
                if a.content_type and a.content_type.startswith('image/')]
        if imgs:
            await _start_expense(message, key, imgs[0])
            return

    text = message.content.strip()
    if text and not message.attachments and not text.startswith(bot.command_prefix):
        # ── 記録済みエントリへの修正指示か？ ──
        # 収入テキストと同じチャンネルに来うるので、収入として扱う前に判定する。
        if await _try_edit(message, key, text):
            return

        # ── 新規: 収入テキスト ──
        if prof.accepts_income:
            await _start_income(message, key)
            return

    await bot.process_commands(message)


# ── フロー開始 ────────────────────────────────────────────

async def _start_expense(message: discord.Message, key: tuple, att: discord.Attachment):
    status = await message.reply('🔍 レシートを読み取り中...')
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(att.url) as r:
                img = await r.read()

        raw = await parse_receipt(img, att.content_type)
        if raw is None:
            await status.edit(content='❌ Geminiの応答をJSONに変換できませんでした。ログを確認してください。')
            return

        items   = raw.get('items') or []
        store   = raw.get('store') or ''
        content = _render_content(store, items)

        entry_data = {
            'date':    raw.get('date'),
            'content': content,
            'amount':  raw.get('total'),
        }
        ext = att.filename.rsplit('.', 1)[-1].lower() if '.' in att.filename else 'jpg'

        entry = PendingEntry(
            entry_type='expense',
            data=entry_data,
            state='confirm' if not _missing(entry_data) else 'info',
            image_data=img,
            mime_type=att.content_type,
            original_ext=ext,
            store=store,
            items=items,
        )
        _pending[key] = entry
        await status.edit(content=_build_message(entry))

    except Exception as e:
        import traceback
        print(f'[bot] _start_expense error:\n{traceback.format_exc()}', flush=True)
        await status.edit(content=f'❌ エラー: {e}')


async def _start_income(message: discord.Message, key: tuple):
    status = await message.reply('💭 解析中...')
    try:
        raw = await parse_income_text(message.content)
        if raw is None:
            await status.edit(content='❌ 収入情報を読み取れませんでした。日付・内容・金額を含めて入力してください。')
            return

        entry_data = {
            'date':    raw.get('date'),
            'content': raw.get('content'),
            'amount':  raw.get('amount'),
        }
        entry = PendingEntry(
            entry_type='income',
            data=entry_data,
            state='confirm' if not _missing(entry_data) else 'info',
        )
        _pending[key] = entry
        await status.edit(content=_build_message(entry))

    except Exception as e:
        await status.edit(content=f'❌ エラー: {e}')


# ── フォローアップ処理 ────────────────────────────────────

async def _handle_followup(message: discord.Message, key: tuple, entry: PendingEntry):
    result = await parse_followup(entry.data, message.content, entry.entry_type)
    if result is None:
        await message.reply('😅 うまく読み取れませんでした。もう一度教えてください。')
        return

    action = result.get('action', 'update')

    # キャンセル
    if action == 'cancel':
        del _pending[key]
        await message.reply('❌ キャンセルしました。')
        return

    # データを更新（非nullの値のみ上書き）
    for f in ('date', 'content', 'amount'):
        if result.get(f) is not None:
            entry.data[f] = result[f]
    entry.ts = time.time()

    miss = _missing(entry.data)

    # 確定: 全項目あり + confirmアクション
    if action == 'confirm' and not miss:
        del _pending[key]
        await _finalize(message, key, entry)
        return

    # まだ不足があるか、updateアクション → 状態を更新して再表示
    entry.state = 'confirm' if not miss else 'info'
    await message.reply(_build_message(entry))


# ── 確定・記録 ────────────────────────────────────────────

async def _finalize(message: discord.Message, key: tuple, entry: PendingEntry):
    prof = _profiles.get(message.channel.id)
    if prof is None:
        await message.reply('❌ このチャンネルには記録先が設定されていません。')
        return

    status = await message.reply('⏳ 記録中...')
    try:
        d = entry.data
        _ensure_workbook(prof.sheet_id)

        # 確定前のやり取りで content が直接書き換えられていると store/items と対応しない。
        # その場合は構造を捨て、内容を1つの文字列として扱う（合計の再計算は諦める）。
        if _render_content(entry.store, entry.items) == d['content']:
            store, items = entry.store, entry.items
        else:
            store, items = d['content'], []

        if entry.entry_type == 'expense':
            filename      = _make_filename(d['date'], d['content'], entry.original_ext)
            file_id, link = _drives[prof.channel_id].upload_receipt(
                entry.image_data, filename, entry.mime_type)
            receipt = gsheets.receipt_cell(link, filename)
            sheet_name, row = gsheets.append_entry(
                prof.sheet_id, str(d['date']), gsheets.KIND_EXPENSE,
                str(d['content']), _amount_cell(d['amount']), receipt, NOTE_NEW)
            _recorded[key] = RecordedEntry(
                entry_type='expense', data=dict(d), sheet_name=sheet_name, row=row,
                store=store, items=items,
                drive_file_id=file_id, drive_link=link, filename=filename,
                original_ext=entry.original_ext,
            )
            await status.edit(content=(
                f'✅ **支出を記録しました**（{prof.name}／{sheet_name}）\n'
                f'{_summary(entry.entry_type, d)}\n'
                f'🗂 ファイル: `{filename}`\n'
                f'🔗 Drive: {link}'
            ))
        else:
            sheet_name, row = gsheets.append_entry(
                prof.sheet_id, str(d['date']), gsheets.KIND_INCOME,
                str(d['content']), _amount_cell(d['amount']), '', NOTE_NEW)
            _recorded[key] = RecordedEntry(
                entry_type='income', data=dict(d), sheet_name=sheet_name, row=row,
                store=d['content'], items=[],
            )
            await status.edit(content=(
                f'✅ **収入を記録しました**（{prof.name}／{sheet_name}）\n'
                f'{_summary(entry.entry_type, d)}'))

    except Exception as e:
        import traceback
        print(f'[bot] _finalize error:\n{traceback.format_exc()}', flush=True)
        await status.edit(content=f'❌ 記録中にエラー: {e}')


# ── 記録済みエントリの修正 ────────────────────────────────

def _sum_prices(items: list) -> Optional[int]:
    """品目価格の合計。1つでも数値化できなければ None。"""
    total = 0
    for i in items or []:
        price = _to_int(i.get('price'))
        if price is None:
            return None
        total += price
    return total


def _edited_amount(rec: RecordedEntry, items: list, total):
    """修正後の合計金額を決める。items は解決済み（rec.items へのフォールバック後）。

    レシートの合計は税や値引きを含むため「品目の総和」とは限らない。
    そこで品目の増減分だけを元の合計に加算し、税分などを保つ。

    品目の価格が動いたときは、Geminiが total を返してきても採用しない。
    合計の算術はここで行う方が確実なため（Geminiは指示に反して
    変更前の total をそのまま返してくることがある）。
    """
    old_sum = _sum_prices(rec.items)
    new_sum = _sum_prices(items)
    old_amt = _to_int(rec.data.get('amount'))
    if None not in (old_sum, new_sum, old_amt) and new_sum != old_sum:
        return old_amt + (new_sum - old_sum)

    # 品目の価格が変わっていない → 合計の明示指定だけを反映する
    explicit = _to_int(total)
    if explicit is not None:
        return explicit
    return rec.data.get('amount')


async def _try_edit(message: discord.Message, key: tuple, text: str) -> bool:
    """修正指示なら適用して True。そうでなければ False（呼び出し側で通常処理）。"""
    rec = _recorded.get(key)
    if rec is None:
        return False
    if time.time() - rec.ts > RECORDED_TTL:
        del _recorded[key]
        return False

    result = await parse_edit(
        entry_type=rec.entry_type,
        date=rec.data.get('date'),
        store=rec.store,
        items=rec.items,
        amount=rec.data.get('amount'),
        user_message=text,
    )
    if result is None or result.get('intent') != 'edit':
        return False

    await _apply_edit(message, rec, result)
    return True


async def _apply_edit(message: discord.Message, rec: RecordedEntry, result: dict):
    prof = _profiles.get(message.channel.id)
    if prof is None:
        return
    status = await message.reply('✏️ 記録を修正中...')
    try:
        date   = result.get('date')  or rec.data.get('date')
        store  = result.get('store') or rec.store
        items  = result.get('items') if result.get('items') is not None else rec.items
        amount = _edited_amount(rec, items, result.get('total'))
        kind   = _kind_ja(rec.entry_type)

        if rec.entry_type == 'expense':
            content  = _render_content(store, items)
            filename = _make_filename(date, content, rec.original_ext)
            if rec.drive_file_id and filename != rec.filename:
                _drives[prof.channel_id].rename_file(rec.drive_file_id, filename)
                rec.filename = filename
            receipt = gsheets.receipt_cell(rec.drive_link, rec.filename)
        else:
            content, receipt = store, ''

        # 日付の月が変わると記録先シートも変わる。元シートの行を消して
        # 新しい月シートへ入れ直す（更新だけだと前月に残ってしまう）。
        new_sheet = gsheets.sheet_name_for_date(date)
        if new_sheet != rec.sheet_name:
            gsheets.update_entry(prof.sheet_id, rec.sheet_name, rec.row,
                                 '', '', '', '', '', '')  # 元の行を空にする
            rec.sheet_name, rec.row = gsheets.append_entry(
                prof.sheet_id, str(date), kind, str(content),
                _amount_cell(amount), receipt, NOTE_EDITED)
        else:
            gsheets.update_entry(prof.sheet_id, rec.sheet_name, rec.row,
                                 str(date), kind, str(content),
                                 _amount_cell(amount), receipt, NOTE_EDITED)

        rec.data  = {'date': date, 'content': content, 'amount': amount}
        rec.store = store
        rec.items = items
        rec.ts    = time.time()

        lines = ['✏️ **記録を修正しました**', _summary(rec.entry_type, rec.data)]
        if rec.entry_type == 'expense':
            lines.append(f'🗂 ファイル: `{rec.filename}`')
            lines.append(f'🔗 Drive: {rec.drive_link}')
        await status.edit(content='\n'.join(lines))

    except Exception as e:
        import traceback
        print(f'[bot] _apply_edit error:\n{traceback.format_exc()}', flush=True)
        await status.edit(content=f'❌ 修正中にエラー: {e}')


# ── ユーティリティコマンド ────────────────────────────────

@bot.command(name='キャンセル')
async def cancel_cmd(ctx: commands.Context):
    key = (ctx.channel.id, ctx.author.id)
    if key in _pending:
        del _pending[key]
        await ctx.reply('❌ キャンセルしました。')
    else:
        await ctx.reply('進行中の記録はありません。')


@bot.command(name='初期化')
async def init_cmd(ctx: commands.Context):
    """このチャンネルのスプレッドシートに月シート一式を用意する。"""
    prof = _profiles.get(ctx.channel.id)
    if prof is None:
        await ctx.reply('このチャンネルには記録先が設定されていません。')
        return
    status = await ctx.reply(f'🛠 「{prof.name}」のシートを初期化中...')
    try:
        created = gsheets.init_workbook(prof.sheet_id)
        _initialized_sheets.add(prof.sheet_id)
        msg = f'✅ 初期化しました（新規作成: {len(created)}シート）' if created \
            else '✅ 既に初期化済みでした'
        await status.edit(content=msg)
    except Exception as e:
        await status.edit(content=f'❌ 初期化に失敗: {e}')


def setup() -> None:
    profiles = config.validate()
    for p in profiles:
        _profiles[p.channel_id] = p
        _drives[p.channel_id] = DriveClient(
            p.drive_token, p.drive_folder_id, p.drive_token_path)


if __name__ == '__main__':
    setup()
    bot.run(config.DISCORD_TOKEN)
