"""スプレッドシートへの記録（フラット2タブ型フォーマット）。

支出は「支出」タブ、収入は「収入」タブに1行ずつ追記する（種別＝タブ）。
月別の集計は「全体収支」タブが SUMIFS（日付の範囲で月を判定）で自動計算する。
会計年度は FISCAL_START_YEAR / FISCAL_START_MONTH（既定: 2026年2月始まり）。

シートIDは引数で受け取り、モジュール内でグローバル設定を読まない。
チャンネルごとに別アカウント・別スプレッドシートへ記録できるようにするため。

bot.py へ提供する公開インターフェース:
    KIND_EXPENSE / KIND_INCOME          種別（＝タブ名）
    init_workbook(sheet_id)             支出/収入/全体収支タブを用意（冪等）
    append_entry(sheet_id, date, kind, content, amount, receipt, note, recorder) -> (タブ名, 行)
    update_entry(sheet_id, sheet_name, row, date, kind, content, amount, receipt, note, recorder)
    receipt_cell(url, filename)         領収書等セル（Driveハイパーリンク）
"""
import re

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

import config

_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# タブ名。種別＝タブなので KIND_* がそのままタブ名になる。
KIND_EXPENSE   = '支出'
KIND_INCOME    = '収入'
OVERVIEW_SHEET = '全体収支'

# データタブの見出し（既存5列 + 記録者）
HEADERS = ['日付', '内容', '金額', '領収書等', '備考', '記録者']

# 会計年度（全体収支の月別集計はこの範囲で日付を月に振り分ける）
FISCAL_START_YEAR  = 2026
FISCAL_START_MONTH = 2   # 2月始まり


# ── セルの組み立て ────────────────────────────────────────

def receipt_cell(url: str, filename: str) -> str:
    """領収書等セル。ファイル名を表示しつつ Drive へリンクする。"""
    if not url:
        return filename or ''
    label = (filename or 'リンク').replace('"', '""')
    return f'=HYPERLINK("{url}","{label}")'


def _row_number(updated_range: str) -> int:
    """append の updatedRange（例: '支出'!A16:E16）から行番号 16 を取り出す。"""
    first_cell = updated_range.split('!')[-1].split(':')[0]
    return int(re.sub(r'\D', '', first_cell))


def _service():
    creds = Credentials.from_service_account_info(
        config.service_account_info(), scopes=_SCOPES,
    )
    return build('sheets', 'v4', credentials=creds)


def _sheet_ids(svc, sheet_id: str) -> dict:
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return {s['properties']['title']: s['properties']['sheetId'] for s in meta['sheets']}


# ── 全体収支の月別集計（SUMIFS）──────────────────────────

def _fiscal_months() -> list[tuple[int, int, int, int]]:
    """会計年度の12か月を (年, 月, 翌月の年, 翌月) で返す。"""
    out = []
    for i in range(12):
        idx = (FISCAL_START_MONTH - 1) + i
        y, m = FISCAL_START_YEAR + idx // 12, idx % 12 + 1
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((y, m, ny, nm))
    return out


def _sumifs(tab: str, y: int, m: int, ny: int, nm: int) -> str:
    """その月の金額合計を出す SUMIFS 数式。日付(A列)の範囲で月を判定する。"""
    return (f'=SUMIFS({tab}!$C:$C,{tab}!$A:$A,">="&DATE({y},{m},1),'
            f'{tab}!$A:$A,"<"&DATE({ny},{nm},1))')


def _overview_layout() -> list:
    months = _fiscal_months()
    labels = [f'{m}月' for _, m, _, _ in months]
    exp = [_sumifs(KIND_EXPENSE, *mo) for mo in months]
    inc = [_sumifs(KIND_INCOME, *mo) for mo in months]
    ov = OVERVIEW_SHEET
    return [
        {'range': f"'{ov}'!A1:F1", 'values': [[
            '支出合計', f'=SUM({KIND_EXPENSE}!C:C)',
            '収入合計', f'=SUM({KIND_INCOME}!C:C)',
            '収支', '=D1-B1']]},
        {'range': f"'{ov}'!A3", 'values': [['支出（月別）']]},
        {'range': f"'{ov}'!A4", 'values': [labels]},
        {'range': f"'{ov}'!A5", 'values': [exp]},
        {'range': f"'{ov}'!A7", 'values': [['収入（月別）']]},
        {'range': f"'{ov}'!A8", 'values': [labels]},
        {'range': f"'{ov}'!A9", 'values': [inc]},
    ]


# ── 装飾 ──────────────────────────────────────────────────

def _format_data_sheet(sid: int) -> list:
    return [
        {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
            'cell': {'userEnteredFormat': {
                'textFormat': {'bold': True},
                'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}}},
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
        {'updateSheetProperties': {
            'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': 1}},
            'fields': 'gridProperties.frozenRowCount'}},
        {'repeatCell': {  # 日付列（データ部）
            'range': {'sheetId': sid, 'startRowIndex': 1,
                      'startColumnIndex': 0, 'endColumnIndex': 1},
            'cell': {'userEnteredFormat': {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}}},
            'fields': 'userEnteredFormat.numberFormat'}},
        {'repeatCell': {  # 金額列（データ部）
            'range': {'sheetId': sid, 'startRowIndex': 1,
                      'startColumnIndex': 2, 'endColumnIndex': 3},
            'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}}},
            'fields': 'userEnteredFormat.numberFormat'}},
        {'updateDimensionProperties': {
            'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 6},
            'properties': {'pixelSize': 150}, 'fields': 'pixelSize'}},
    ]


def _format_overview(sid: int) -> list:
    # 1行目・4行目(支出見出し)・8行目(収入見出し)を太字に
    return [{'repeatCell': {
        'range': {'sheetId': sid, 'startRowIndex': r, 'endRowIndex': r + 1},
        'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
        'fields': 'userEnteredFormat.textFormat'}} for r in (0, 3, 7)]


# ── 初期化 ────────────────────────────────────────────────

def init_workbook(sheet_id: str) -> list:
    """支出/収入/全体収支タブを用意する。何度実行しても安全（冪等）。

    既存タブがあればデータ行には触れず、見出し(1行目)と全体収支の数式だけ
    書き直す。無いタブだけ新規作成する。作成したタブ名の一覧を返す。
    """
    svc      = _service()
    existing = _sheet_ids(svc, sheet_id)
    wanted   = [KIND_EXPENSE, KIND_INCOME, OVERVIEW_SHEET]

    created = [n for n in wanted if n not in existing]
    if created:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={'requests': [{'addSheet': {'properties': {'title': n}}}
                               for n in created]},
        ).execute()
        existing = _sheet_ids(svc, sheet_id)

    # 見出し（1行目のみ）と全体収支の数式を書く。データ行(2行目以降)は触らない。
    data = [
        {'range': f"'{KIND_EXPENSE}'!A1:F1", 'values': [HEADERS]},
        {'range': f"'{KIND_INCOME}'!A1:F1",  'values': [HEADERS]},
    ] + _overview_layout()
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={'valueInputOption': 'USER_ENTERED', 'data': data},
    ).execute()

    # 装飾
    reqs = (_format_data_sheet(existing[KIND_EXPENSE])
            + _format_data_sheet(existing[KIND_INCOME])
            + _format_overview(existing[OVERVIEW_SHEET]))
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id, body={'requests': reqs}).execute()
    return created


# ── 記録 ──────────────────────────────────────────────────

def append_entry(sheet_id: str, date: str, kind: str, content: str,
                 amount, receipt: str, note: str, recorder: str = '') -> tuple[str, int]:
    """種別のタブ（支出/収入）に1行追記し、(タブ名, 行番号) を返す。

    全体収支の集計は列全体を対象にした SUMIFS なので、末尾に追記しても
    範囲がずれず自動で反映される。
    """
    resp = _service().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{kind}'!A:F",
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body={'values': [[date, content, amount, receipt, note, recorder]]},
    ).execute()
    return kind, _row_number(resp['updates']['updatedRange'])


def update_entry(sheet_id: str, sheet_name: str, row: int, date: str, kind: str,
                 content: str, amount, receipt: str, note: str, recorder: str = ''):
    """記録済みの1行を上書きする（タブ＝種別なので kind は使わない）。"""
    _service().spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{sheet_name}'!A{row}:F{row}",
        valueInputOption='USER_ENTERED',
        body={'values': [[date, content, amount, receipt, note, recorder]]},
    ).execute()
