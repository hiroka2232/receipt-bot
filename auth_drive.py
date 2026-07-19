"""Google Drive の OAuth 認証を行うスクリプト。

アカウント（プロファイル）ごとに1回ずつ実行してトークンを作る。
出力ファイル名は引数で指定できる（既定 drive_token_1.json）。

    python auth_drive.py                     # → drive_token_1.json
    python auth_drive.py drive_token_2.json  # → 2つ目のアカウント用

ブラウザで認証したいアカウントに切り替えてから「許可」を押すこと。
"""
import io
import os
import sys

# Windowsの既定コンソール(cp932)でも絵文字で落ちないように出力をUTF-8化
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES     = ['https://www.googleapis.com/auth/drive.file']
CREDS_FILE = 'oauth_credentials.json'
TOKEN_FILE = sys.argv[1] if len(sys.argv) > 1 else 'drive_token_1.json'

if not os.path.exists(CREDS_FILE):
    print(f'エラー: {CREDS_FILE} が見つかりません。')
    print('Google Cloud Console からOAuthクライアントIDのJSONをダウンロードして')
    print(f'このフォルダに {CREDS_FILE} という名前で置いてください。')
    sys.exit(1)

print('ブラウザが開きます。認証したいGoogleアカウントでログインして「許可」を押してください。')
flow  = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
    f.write(creds.to_json())

print(f'\n認証完了。{TOKEN_FILE} を作成しました。')
print('このファイル名を .env の PROFILE_x_DRIVE_TOKEN_FILE に設定してください。')
