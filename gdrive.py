import io
import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

_SCOPES     = ['https://www.googleapis.com/auth/drive.file']
_TOKEN_FILE = 'drive_token.json'


def _service():
    creds = None

    # クラウド環境: 環境変数からトークンを読む
    token_json = os.getenv('DRIVE_TOKEN_JSON')
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), _SCOPES)
    elif os.path.exists(_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(_TOKEN_FILE, _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # クラウドではブラウザ認証不可: ローカルで auth_drive.py を実行してください
            raise RuntimeError(
                'Drive認証トークンが見つかりません。'
                'ローカルで auth_drive.py を実行してから DRIVE_TOKEN_JSON を設定してください。'
            )

    return build('drive', 'v3', credentials=creds)


def upload_receipt(image_data: bytes, filename: str, mime_type: str) -> str:
    svc = _service()
    metadata = {
        'name': filename,
        'parents': [os.getenv('GOOGLE_DRIVE_FOLDER_ID')],
    }
    media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype=mime_type)
    f = svc.files().create(body=metadata, media_body=media, fields='webViewLink').execute()
    return f.get('webViewLink', '')
