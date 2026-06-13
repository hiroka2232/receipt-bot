import os
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from gemini import parse_receipt, parse_income_text, parse_followup
from gdrive import upload_receipt
from gsheets import append_expense, append_income

RECEIPT_CHANNEL_ID = int(os.getenv('RECEIPT_CHANNEL_ID', 0))
INCOME_CHANNEL_ID  = int(os.getenv('INCOME_CHANNEL_ID', 0))
PENDING_TTL        = 600  # 10分でタイムアウト

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)


@dataclass
class PendingEntry:
    entry_type: str             # 'expense' | 'income'
    data: dict                  # date, content, amount
    state: str                  # 'info' | 'confirm'
    ts: float = field(default_factory=time.time)
    image_data: Optional[bytes] = None
    mime_type:  Optional[str]   = None
    original_ext: Optional[str] = None  # jpg / png など


# (channel_id, user_id) → PendingEntry
_pending: dict[tuple[int, int], PendingEntry] = {}


# ── ヘルパー ──────────────────────────────────────────────

def _missing(data: dict) -> list[str]:
    labels = {'date': '日付', 'content': '内容', 'amount': '金額'}
    return [labels[k] for k in ('date', 'content', 'amount') if not data.get(k)]


def _fmt_amount(val) -> str:
    try:
        return f'¥{int(float(str(val))):,}'
    except (TypeError, ValueError):
        return str(val)


def _summary(entry: PendingEntry) -> str:
    d    = entry.data
    kind = '支出' if entry.entry_type == 'expense' else '収入'
    lines = [
        f'**【{kind}】**',
        f'📅 日付 : {d.get("date") or "❓ 不明"}',
        f'📝 内容 : {d.get("content") or "❓ 不明"}',
        f'💴 金額 : {_fmt_amount(d["amount"]) if d.get("amount") else "❓ 不明"}',
    ]
    return '\n'.join(lines)


def _build_message(entry: PendingEntry) -> str:
    miss = _missing(entry.data)
    base = _summary(entry)
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


def _make_filename(entry: PendingEntry) -> str:
    d         = entry.data
    date_raw  = str(d.get('date') or 'unknown').replace('-', '')
    # 内容の先頭部分（括弧の前まで）を店名として使う
    content   = str(d.get('content') or 'unknown')
    store_raw = content.split('（')[0].strip().replace(' ', '_').replace('/', '_')[:20]
    ext       = entry.original_ext or 'jpg'
    return f'{date_raw}_{store_raw}.{ext}'


# ── イベントハンドラ ──────────────────────────────────────

@bot.event
async def on_ready():
    print(f'起動: {bot.user}')
    print(f'  領収書ch: {RECEIPT_CHANNEL_ID}')
    print(f'  収入ch  : {INCOME_CHANNEL_ID}')


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    key = (message.channel.id, message.author.id)
    ch  = message.channel.id

    # ── 進行中の会話があれば優先処理 ──
    if key in _pending:
        entry = _pending[key]
        if time.time() - entry.ts > PENDING_TTL:
            del _pending[key]
            await message.reply('⏱ タイムアウトしました。最初からやり直してください。')
            return
        await _handle_followup(message, key, entry)
        return

    # ── 新規: レシート画像（支出） ──
    if ch == RECEIPT_CHANNEL_ID:
        imgs = [a for a in message.attachments
                if a.content_type and a.content_type.startswith('image/')]
        if imgs:
            await _start_expense(message, key, imgs[0])
            return

    # ── 新規: 収入テキスト ──
    if ch == INCOME_CHANNEL_ID and message.content.strip() and not message.attachments:
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

        items     = raw.get('items') or []
        items_str = '、'.join(f"{i['name']}(¥{i['price']})" for i in items if i.get('name'))
        store     = raw.get('store') or ''
        content   = f'{store}（{items_str}）' if items_str else (store or None)

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
        )
        _pending[key] = entry
        await status.edit(content=_build_message(entry))

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f'[bot] _start_expense error:\n{tb}', flush=True)
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
        await _finalize(message, entry)
        return

    # まだ不足があるか、updateアクション → 状態を更新して再表示
    entry.state = 'confirm' if not miss else 'info'
    await message.reply(_build_message(entry))


# ── 確定・記録 ────────────────────────────────────────────

async def _finalize(message: discord.Message, entry: PendingEntry):
    status = await message.reply('⏳ 記録中...')
    try:
        d = entry.data

        if entry.entry_type == 'expense':
            filename   = _make_filename(entry)
            drive_link = upload_receipt(entry.image_data, filename, entry.mime_type)
            append_expense(
                date=str(d['date']),
                content=str(d['content']),
                amount=str(d['amount']),
                drive_link=drive_link,
                note='AI自動記録（Gemini）',
            )
            await status.edit(content=(
                f'✅ **支出を記録しました**\n'
                f'{_summary(entry)}\n'
                f'🗂 ファイル: `{filename}`\n'
                f'🔗 Drive: {drive_link}'
            ))
        else:
            append_income(
                date=str(d['date']),
                content=str(d['content']),
                amount=str(d['amount']),
                note='AI自動記録（Gemini）',
            )
            await status.edit(content=f'✅ **収入を記録しました**\n{_summary(entry)}')

    except Exception as e:
        import traceback
        print(f'[bot] _finalize error:\n{traceback.format_exc()}', flush=True)
        await status.edit(content=f'❌ 記録中にエラー: {e}')


# ── ユーティリティコマンド ────────────────────────────────

@bot.command(name='キャンセル')
async def cancel_cmd(ctx: commands.Context):
    key = (ctx.channel.id, ctx.author.id)
    if key in _pending:
        del _pending[key]
        await ctx.reply('❌ キャンセルしました。')
    else:
        await ctx.reply('進行中の記録はありません。')


bot.run(os.getenv('DISCORD_TOKEN'))
