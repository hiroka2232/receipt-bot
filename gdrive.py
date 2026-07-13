import io

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import config

_SCOPES = ['https://www.googleapis.com/auth/drive.file']


def _save_creds(creds: Credentials) -> None:
    # 環境変数からトークンを読んでいる場合はファイルに書き戻せない（書かない）。
    # メモリ上のcredsは有効なので、この起動中はそのまま使える。
    if config.drive_token_from_env():
        return
    with open(config.drive_token_file(), 'w', encoding='utf-8') as f:
        f.write(creds.to_json())


def _load_creds() -> Credentials:
    info  = config.drive_token_info()
    creds = Credentials.from_authorized_user_info(info, _SCOPES) if info else None

    if creds and creds.valid:
        return creds

    # 期限切れでも refresh_token があればヘッドレスで更新できる
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_creds(creds)
        return creds

    # ここに来る = 有効なトークンが無い。
    # ローカル初回のみ、明示的に許可されていればブラウザ認証を行う。
    if config.ALLOW_INTERACTIVE_AUTH:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow  = InstalledAppFlow.from_client_secrets_file(config.oauth_credentials_file(), _SCOPES)
        creds = flow.run_local_server(port=0)
        _save_creds(creds)
        return creds

    # サーバ等ではブラウザを開けないので、明確なエラーで止める。
    raise RuntimeError(
        'Google Drive の認証トークンがありません。\n'
        'ローカルで `python auth_drive.py` を実行して drive_token.json を作成し、\n'
        'その中身を環境変数 GOOGLE_DRIVE_TOKEN_JSON に設定してください。'
    )


def _service():
    return build('drive', 'v3', credentials=_load_creds())


def upload_receipt(image_data: bytes, filename: str, mime_type: str) -> str:
    svc = _service()
    metadata = {
        'name': filename,
        'parents': [config.GOOGLE_DRIVE_FOLDER_ID],
    }
    media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype=mime_type)
    f = svc.files().create(body=metadata, media_body=media, fields='webViewLink').execute()
    return f.get('webViewLink', '')
