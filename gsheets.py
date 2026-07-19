"""スプレッドシートへの記録。

月ごとに1シート（年度は2月始まり＝既存の収支報告に合わせる）＋「全体収支」の集計シート。
支出と収入は「種別」列で区別して1つの表に並べる。合計は列全体を対象にした
SUMIF なので、1か月あたりの行数に上限が無い。

シートIDは引数で受け取り、モジュール内でグローバル設定を読まない。
チャンネルごとに別アカウント・別スプレッドシートへ記録できるようにするため。
"""
import re

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

import config

_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

HEADERS        = ['日付', '種別', '内容', '金額', '領収書等', '備考']
HEADER_ROW     = 5   # 見出し行
FIRST_DATA_ROW = 6   # データはここから下（合計式もこの行以降だけを見る）
OVERVIEW_SHEET = '全体収支'

KIND_EXPENSE = '支出'
KIND_INCOME  = '収入'

# 年度は2月始まり。全体収支シートの並び順もこれに従う。
FISCAL_MONTHS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1]

_DATE_FMT  = {'numberFormat': {'type': 'DATE',   'pattern': 'd'}}
_MONEY_FMT = {'numberFormat': {'type': 'NUMBER', 'pattern': '[$¥-411]#,##0'}}

_FULLWIDTH = str.maketrans('0123456789', '０１２３４５６７８９')


# ── 名前とセルの組み立て ──────────────────────────────────

def month_sheet_name(month: int) -> str:
    """7 → '７月'（既存の収支報告に合わせて全角）。"""
    return str(month).translate(_FULLWIDTH) + '月'


def sheet_name_for_date(value) -> str:
    """'2026-07-17' → '７月'。"""
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', str(value).strip())
    if not m:
        raise ValueError(f'日付を YYYY-MM-DD として解釈できません: {value!r}')
    return month_sheet_name(int(m.group(2)))


def receipt_cell(url: str, filename: str) -> str:
    """領収書等セル。ファイル名を表示しつつDriveへリンクする。"""
    if not url:
        return filename or ''
    label = (filename or 'リンク').replace('"', '""')
    return f'=HYPERLINK("{url}","{label}")'


def _row_number(updated_range: str) -> int:
    """append の updatedRange（例: '７月'!A6:F6）から行番号 6 を取り出す。"""
    first_cell = updated_range.split('!')[-1].split(':')[0]
    return int(re.sub(r'\D', '', first_cell))


def _service():
    creds = Credentials.from_service_account_info(
        config.service_account_info(),
        scopes=_SCOPES,
    )
    return build('sheets', 'v4', credentials=creds)


def _sheet_ids(svc, sheet_id: str) -> dict:
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return {s['properties']['title']: s['properties']['sheetId'] for s in meta['sheets']}


# ── 初期化 ────────────────────────────────────────────────

def _month_layout(name: str) -> list:
    """月シートの固定部分（合計欄と見出し）。

    合計式の範囲を6行目以降に限定しているのは、B列全体を対象にすると
    式自身（B1〜B3）を含んでしまい循環参照になるため。開始行を固定した
    開放範囲（B6:B）なので行数の上限は無い。
    """
    return [
        {'range': f"'{name}'!A1:B3", 'values': [
            ['支出合計', f'=SUMIF(B{FIRST_DATA_ROW}:B,"{KIND_EXPENSE}",D{FIRST_DATA_ROW}:D)'],
            ['収入合計', f'=SUMIF(B{FIRST_DATA_ROW}:B,"{KIND_INCOME}",D{FIRST_DATA_ROW}:D)'],
            ['収支',     '=B2-B1'],
        ]},
        {'range': f"'{name}'!A{HEADER_ROW}:F{HEADER_ROW}", 'values': [HEADERS]},
    ]


def _overview_layout() -> list:
    rows = [['月', '支出合計', '収入合計', '収支']]
    for i, month in enumerate(FISCAL_MONTHS):
        r = 4 + i                       # 見出しが3行目なのでデータは4行目から
        ref = f"'{month_sheet_name(month)}'"
        rows.append([f'{month}月', f'={ref}!B1', f'={ref}!B2', f'=C{r}-B{r}'])
    last = 4 + len(FISCAL_MONTHS) - 1
    rows.append(['合計', f'=SUM(B4:B{last})', f'=SUM(C4:C{last})', f'=SUM(D4:D{last})'])
    return [{'range': f"'{OVERVIEW_SHEET}'!A3:D{last + 1}", 'values': rows}]


def _format_requests(sid: int, is_month: bool) -> list:
    """見出しの装飾・固定・列幅・数値書式。"""
    header_row = HEADER_ROW - 1 if is_month else 2   # 0始まり
    reqs = [
        {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': header_row, 'endRowIndex': header_row + 1},
            'cell': {'userEnteredFormat': {
                'textFormat': {'bold': True},
                'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9},
            }},
            'fields': 'userEnteredFormat(textFormat,backgroundColor)',
        }},
        {'updateSheetProperties': {
            'properties': {'sheetId': sid,
                           'gridProperties': {'frozenRowCount': header_row + 1}},
            'fields': 'gridProperties.frozenRowCount',
        }},
        {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 3,
                      'startColumnIndex': 0, 'endColumnIndex': 1},
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
            'fields': 'userEnteredFormat.textFormat',
        }},
    ]
    if is_month:
        reqs += [
            # 日付列: 値は YYYY-MM-DD で持ち、表示だけ日にする
            {'repeatCell': {
                'range': {'sheetId': sid, 'startRowIndex': FIRST_DATA_ROW - 1,
                          'startColumnIndex': 0, 'endColumnIndex': 1},
                'cell': {'userEnteredFormat': _DATE_FMT},
                'fields': 'userEnteredFormat.numberFormat',
            }},
            # 金額列（データ部）と合計欄
            {'repeatCell': {
                'range': {'sheetId': sid, 'startRowIndex': FIRST_DATA_ROW - 1,
                          'startColumnIndex': 3, 'endColumnIndex': 4},
                'cell': {'userEnteredFormat': _MONEY_FMT},
                'fields': 'userEnteredFormat.numberFormat',
            }},
            {'repeatCell': {
                'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 3,
                          'startColumnIndex': 1, 'endColumnIndex': 2},
                'cell': {'userEnteredFormat': _MONEY_FMT},
                'fields': 'userEnteredFormat.numberFormat',
            }},
            {'updateDimensionProperties': {
                'range': {'sheetId': sid, 'dimension': 'COLUMNS',
                          'startIndex': 0, 'endIndex': 6},
                'properties': {'pixelSize': 150},
                'fields': 'pixelSize',
            }},
        ]
    else:
        reqs.append({'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 3,
                      'startColumnIndex': 1, 'endColumnIndex': 4},
            'cell': {'userEnteredFormat': _MONEY_FMT},
            'fields': 'userEnteredFormat.numberFormat',
        }})
    return reqs


def init_workbook(sheet_id: str) -> list:
    """全体収支＋12か月分のシートを作る。何度実行しても安全。

    月シートを最初に全部作るのは、全体収支の集計式が存在しないシートを
    参照すると #REF! になるため。作成したシート名を返す。
    """
    svc      = _service()
    existing = _sheet_ids(svc, sheet_id)
    wanted   = [OVERVIEW_SHEET] + [month_sheet_name(m) for m in FISCAL_MONTHS]

    created = [n for n in wanted if n not in existing]
    if created:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={'requests': [{'addSheet': {'properties': {'title': n}}}
                               for n in created]},
        ).execute()
        existing = _sheet_ids(svc, sheet_id)

    # 中身（見出しと式）を書く
    data = _overview_layout()
    for m in FISCAL_MONTHS:
        data += _month_layout(month_sheet_name(m))
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={'valueInputOption': 'USER_ENTERED', 'data': data},
    ).execute()

    # 装飾
    reqs = _format_requests(existing[OVERVIEW_SHEET], is_month=False)
    for m in FISCAL_MONTHS:
        reqs += _format_requests(existing[month_sheet_name(m)], is_month=True)

    # 新規スプレッドシートの既定シートが残っていたら消す
    for title, sid in existing.items():
        if title not in wanted:
            reqs.append({'deleteSheet': {'sheetId': sid}})

    svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id,
                                   body={'requests': reqs}).execute()
    return created


# ── 記録 ──────────────────────────────────────────────────

def append_entry(sheet_id: str, date: str, kind: str, content: str,
                 amount, receipt: str, note: str) -> tuple[str, int]:
    """日付の月のシートに1行追記し、(シート名, 行番号) を返す。"""
    name = sheet_name_for_date(date)
    # OVERWRITE で既存の空行に書き込む。INSERT_ROWS だと合計式より上に行が
    # 挿入され、SUMIF の範囲（B6:B）が自動でずれて集計から漏れるため。
    # 空行には初期化時の日付/金額書式が付いているので表示も崩れない。
    resp = _service().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{name}'!A{FIRST_DATA_ROW}:F",
        valueInputOption='USER_ENTERED',
        insertDataOption='OVERWRITE',
        body={'values': [[date, kind, content, amount, receipt, note]]},
    ).execute()
    return name, _row_number(resp['updates']['updatedRange'])


def update_entry(sheet_id: str, sheet_name: str, row: int, date: str, kind: str,
                 content: str, amount, receipt: str, note: str):
    """記録済みの1行を上書きする。"""
    _service().spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{sheet_name}'!A{row}:F{row}",
        valueInputOption='USER_ENTERED',
        body={'values': [[date, kind, content, amount, receipt, note]]},
    ).execute()
