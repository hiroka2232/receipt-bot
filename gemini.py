import asyncio
import json
import os
import re

from google import genai
from google.genai import types

_client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
_MODEL  = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')


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
