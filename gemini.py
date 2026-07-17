import asyncio
import json
import re
from typing import Optional

from google import genai
from google.genai import types

import config

_client = genai.Client(api_key=config.GEMINI_API_KEY)
_MODEL  = config.GEMINI_MODEL


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


_RECEIPT_PROMPT = """\
このレシート画像から情報を抽出してください。
以下のJSON形式のみで返してください（説明文不要）:
{
  "store": "店名（不明ならnull）",
  "date": "YYYY-MM-DD形式（不明ならnull）",
  "items": [{"name": "品目名", "price": 税込価格（整数）}],
  "total": 合計金額（整数, 不明ならnull）
}"""

_INCOME_PROMPT = """\
以下のメッセージから収入情報を抽出してください。
メッセージ: {message}

以下のJSON形式のみで返してください（説明文不要）:
{{
  "date": "YYYY-MM-DD形式（不明ならnull）",
  "content": "収入の名目・内容（不明ならnull）",
  "amount": 金額（整数, 不明ならnull）
}}"""

# Gemini interprets the follow-up message and returns merged data + action.
# action: "confirm" = user is OK with current data
#         "cancel"  = user wants to cancel
#         "update"  = user provided additional/corrected info
_FOLLOWUP_PROMPT = """\
収支記録の入力補助をしています。種別は「{entry_type}」です。

現在判明している情報（nullは不明）:
- 日付: {date}
- 内容: {content}
- 金額: {amount}

ユーザーの追加メッセージ: 「{message}」

現在の情報をベースに、ユーザーのメッセージから補完・修正して以下のJSONのみ返してください:
{{
  "date": "YYYY-MM-DD形式（不明ならnull, 変更なければ現在の値を維持）",
  "content": "内容（不明ならnull, 変更なければ現在の値を維持）",
  "amount": 金額（整数, 不明ならnull, 変更なければ現在の値を維持）,
  "action": "confirm"（確定・はい・OKの意図）| "cancel"（キャンセル・やめる）| "update"（情報の追加・修正）
}}"""

# 記録済みエントリに対する事後修正。
# 収入テキストと同じチャンネルに来るため、まず intent の判定が要る。
# 合計金額の算術はPython側で行うので、ここでは品目と「明示指定された合計」だけを返させる。
_EDIT_PROMPT = """\
家計簿ボットです。直前に記録した「{entry_type}」のデータがあります。

直前の記録:
- 日付: {date}
- 店名/名目: {store}
- 品目: {items}
- 合計金額: {amount}

ユーザーの新しいメッセージ: 「{message}」

このメッセージが「直前の記録への修正指示」なのか「それとは無関係な新しい記録の依頼・雑談」なのかを
判定し、以下のJSONのみで返してください（説明文不要）:
{{
  "intent": "edit"（直前の記録を修正したい）| "new"（新しい記録の依頼・無関係な発言）,
  "date": "YYYY-MM-DD形式（変更がなければ現在の値をそのまま）",
  "store": "店名/名目（変更がなければ現在の値をそのまま）",
  "items": [{{"name": "品目名", "price": 税込価格（整数）}}],
  "total": 整数 または null
}}

判定の目安:
- 「いまの/さっきの内容を変更して」「◯◯→△△」「〜を修正」→ "edit"
- 「バイト代5000円もらった」のような新しい収支の申告 → "new"

修正時の注意:
- "items" には修正後の全品目を必ず列挙する（変更していない品目もそのまま含める）
- 品目名や価格を変えただけなら "total" は必ず null にする（合計はこちらで再計算する）
- 「合計を◯◯円に」のように合計額そのものを指定されたときだけ "total" に整数を入れる
- 品目が無い記録（収入など）で金額の変更を指示されたら "total" に整数を入れる
- intent が "new" のときは他のフィールドはnullでよい"""


async def parse_receipt(image_data: bytes, mime_type: str) -> dict | None:
    return await asyncio.get_event_loop().run_in_executor(
        None, _parse_receipt_sync, image_data, mime_type
    )


def _parse_receipt_sync(image_data: bytes, mime_type: str) -> dict | None:
    resp = _client.models.generate_content(
        model=_MODEL,
        contents=[
            types.Part.from_bytes(data=image_data, mime_type=mime_type),
            _RECEIPT_PROMPT,
        ],
    )
    print(f'[Gemini] receipt raw: {resp.text[:200]}', flush=True)
    result = _extract_json(resp.text)
    if result is None:
        print(f'[Gemini] JSON parse failed. full response: {resp.text}', flush=True)
    return result


async def parse_income_text(text: str) -> dict | None:
    return await asyncio.get_event_loop().run_in_executor(
        None, _parse_income_sync, text
    )


def _parse_income_sync(text: str) -> dict | None:
    try:
        resp = _client.models.generate_content(
            model=_MODEL,
            contents=_INCOME_PROMPT.format(message=text),
        )
        return _extract_json(resp.text)
    except Exception as e:
        print(f'[Gemini] parse_income error: {e}')
        return None


async def parse_followup(current_data: dict, user_message: str, entry_type: str) -> dict | None:
    return await asyncio.get_event_loop().run_in_executor(
        None, _parse_followup_sync, current_data, user_message, entry_type
    )


def _parse_followup_sync(current_data: dict, user_message: str, entry_type: str) -> dict | None:
    type_ja = '支出' if entry_type == 'expense' else '収入'
    prompt = _FOLLOWUP_PROMPT.format(
        entry_type=type_ja,
        date=current_data.get('date') or 'null',
        content=current_data.get('content') or 'null',
        amount=current_data.get('amount') or 'null',
        message=user_message,
    )
    try:
        resp = _client.models.generate_content(model=_MODEL, contents=prompt)
        return _extract_json(resp.text)
    except Exception as e:
        print(f'[Gemini] parse_followup error: {e}')
        return None


async def parse_edit(entry_type: str, date, store, items: list, amount,
                     user_message: str) -> Optional[dict]:
    """記録済みエントリへの修正指示かを判定し、修正後の内容を返す。"""
    return await asyncio.get_event_loop().run_in_executor(
        None, _parse_edit_sync, entry_type, date, store, items, amount, user_message
    )


def _parse_edit_sync(entry_type: str, date, store, items: list, amount,
                     user_message: str) -> Optional[dict]:
    prompt = _EDIT_PROMPT.format(
        entry_type='支出' if entry_type == 'expense' else '収入',
        date=date or 'null',
        store=store or 'null',
        items=json.dumps(items, ensure_ascii=False) if items else 'なし',
        amount=amount if amount is not None else 'null',
        message=user_message,
    )
    try:
        resp = _client.models.generate_content(model=_MODEL, contents=prompt)
        print(f'[Gemini] edit raw: {resp.text[:200]}', flush=True)
        return _extract_json(resp.text)
    except Exception as e:
        print(f'[Gemini] parse_edit error: {e}')
        return None
