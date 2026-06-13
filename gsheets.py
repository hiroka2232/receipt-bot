import os

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES  = ['https://www.googleapis.com/auth/spreadsheets']
_HEADERS = ['日付', '内容', '金額', '領収書等', '備考']

# 初期化済みシートのキャッシュ（起動中は再チェック不要）
_initialized: set[str] = set()


def _service():
    creds = Credentials.from_service_account_file(
        os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'service_account.json'),
        scopes=_SCOPES,
    )
    return build('sheets', 'v4', credentials=creds)


def _ensure_header(svc, sid: str, sheet_name: str):
    key = f'{sid}:{sheet_name}'
    if key in _initialized:
        return

    result = svc.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f'{sheet_name}!A1:E1',
    ).execute()

    if result.get('values'):
        _initialized.add(key)
        return

    # ヘッダー行を書き込む
    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f'{sheet_name}!A1',
        valueInputOption='USER_ENTERED',
        body={'values': [_HEADERS]},
    ).execute()

    # シートIDを取得してフォーマット適用
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    sheet_id = next(
        s['properties']['sheetId']
        for s in meta['sheets']
        if s['properties']['title'] == sheet_name
    )
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={'requests': [
            # ヘッダー行を太字に
            {
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': 0,
                        'endRowIndex': 1,
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'textFormat': {'bold': True},
                            'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9},
                        }
                    },
                    'fields': 'userEnteredFormat(textFormat,backgroundColor)',
                }
            },
            # 1行目を固定
            {
                'updateSheetProperties': {
                    'properties': {
                        'sheetId': sheet_id,
                        'gridProperties': {'frozenRowCount': 1},
                    },
                    'fields': 'gridProperties.frozenRowCount',
                }
            },
            # 列幅を調整（A:日付, B:内容, C:金額, D:領収書等, E:備考）
            {
                'updateDimensionProperties': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'COLUMNS',
                        'startIndex': 0,
                        'endIndex': 5,
                    },
                    'properties': {'pixelSize': 160},
                    'fields': 'pixelSize',
                }
            },
        ]},
    ).execute()

    _initialized.add(key)


def _append_row(sheet_name: str, row: list):
    svc = _service()
    sid = os.getenv('GOOGLE_SHEETS_ID')
    _ensure_header(svc, sid, sheet_name)
    svc.spreadsheets().values().append(
        spreadsheetId=sid,
        range=f'{sheet_name}!A:E',
        valueInputOption='USER_ENTERED',
        body={'values': [row]},
    ).execute()


def append_expense(date: str, content: str, amount: str, drive_link: str, note: str):
    _append_row('支出', [date, content, amount, drive_link, note])


def append_income(date: str, content: str, amount: str, note: str):
    _append_row('収入', [date, content, amount, '', note])
