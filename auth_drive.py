"""
Google Drive の OAuth 認証を行うスクリプト。
初回のみ実行してください。drive_token.json が作成されたら完了です。
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES     = ['https://www.googleapis.com/auth/drive.file']
CREDS_FILE = 'oauth_credentials.json'
TOKEN_FILE = 'drive_token.json'

if not os.path.exists(CREDS_FILE):
    print(f'エラー: {CREDS_FILE} が見つかりません。')
    print('Google Cloud Console からOAuthクライアントIDのJSONをダウンロードして')
    print(f'このフォルダに {CREDS_FILE} という名前で置いてください。')
    exit(1)

print('ブラウザが開きます。Googleアカウントにログインして「許可」を押してください。')
flow  = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_FILE, 'w') as f:
    f.write(creds.to_json())

print(f'\n✅ 認証完了！{TOKEN_FILE} を作成しました。')
print('これでボットを起動できます: python bot.py')
