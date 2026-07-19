"""Google Drive への領収書アップロード。

認証トークンとフォルダはプロファイルごとに異なるため、DriveClient に持たせる。
モジュールはグローバル設定を読まない。
"""
import io
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import config

_SCOPES = ['https://www.googleapis.com/auth/drive.file']


class DriveClient:
    """1プロファイル分の Drive アクセス。creds は必要時に用意し使い回す。"""

    def __init__(self, token_info: dict, folder_id: str,
                 token_path: Optional[str] = None):
        self._token_info = token_info or None
        self._folder_id  = folder_id
        self._token_path = token_path
        self._svc = None

    def _save(self, creds: Credentials) -> None:
        # 環境変数由来（書き戻し先パス無し）の場合はファイルに書けない。
        # メモリ上の creds はこの起動中は有効なのでそのまま使う。
        if not self._token_path:
            return
        with open(self._token_path, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())

    def _load_creds(self) -> Credentials:
        info  = self._token_info
        creds = Credentials.from_authorized_user_info(info, _SCOPES) if info else None

        if creds and creds.valid:
            return creds
        # 期限切れでも refresh_token があればヘッドレスで更新できる
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save(creds)
            return creds
        # ローカル初回のみ、明示的に許可されていればブラウザ認証を行う。
        if config.ALLOW_INTERACTIVE_AUTH:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow  = InstalledAppFlow.from_client_secrets_file(
                config.oauth_credentials_file(), _SCOPES)
            creds = flow.run_local_server(port=0)
            self._save(creds)
            return creds
        raise RuntimeError(
            'Google Drive の認証トークンがありません。\n'
            'ローカルで `python auth_drive.py` を実行してトークンを作成してください。'
        )

    def _service(self):
        if self._svc is None:
            self._svc = build('drive', 'v3', credentials=self._load_creds())
        return self._svc

    def upload_receipt(self, image_data: bytes, filename: str,
                       mime_type: str) -> tuple[str, str]:
        """領収書を保存し、(ファイルID, 閲覧リンク) を返す。

        ファイルIDは、後から記録を修正したときにリネームするために必要。
        """
        metadata = {'name': filename, 'parents': [self._folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype=mime_type)
        f = self._service().files().create(
            body=metadata, media_body=media, fields='id,webViewLink').execute()
        return f['id'], f.get('webViewLink', '')

    def rename_file(self, file_id: str, new_name: str) -> None:
        self._service().files().update(
            fileId=file_id, body={'name': new_name}).execute()
