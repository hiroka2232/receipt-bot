"""環境変数と秘密情報の一元管理。

方針: 「環境変数を優先し、無ければローカルのファイルにフォールバック」。
- ローカル / テスト : .env と *.json ファイルをそのまま使える
- サーバ (Docker)   : すべて環境変数で渡せる（サーバに秘密ファイルを置かなくてよい）

秘密情報（サービスアカウント / Driveトークン）は JSON 文字列を
環境変数に入れて渡せる。設定が無ければ従来どおりファイルから読む。
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()


# ── Discord ───────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv('DISCORD_TOKEN', '')
RECEIPT_CHANNEL_ID = int(os.getenv('RECEIPT_CHANNEL_ID', '0'))
INCOME_CHANNEL_ID  = int(os.getenv('INCOME_CHANNEL_ID', '0'))

# ── Gemini ────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL   = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')

# ── Google (Sheets / Drive) ───────────────────────────────
GOOGLE_SHEETS_ID       = os.getenv('GOOGLE_SHEETS_ID', '')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '')

# 対話認証（ブラウザを開くOAuth）を許可するか。
# ローカルで初回トークンを作るときだけ 1 にする。サーバでは必ず 0。
ALLOW_INTERACTIVE_AUTH = os.getenv('ALLOW_INTERACTIVE_AUTH', '0') == '1'


def service_account_info() -> dict:
    """サービスアカウント認証情報を dict で返す（環境変数優先・ファイルfallback）。"""
    raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if raw:
        return json.loads(raw)
    path = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'service_account.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def drive_token_file() -> str:
    return os.getenv('GOOGLE_DRIVE_TOKEN_FILE', 'drive_token.json')


def drive_token_from_env() -> bool:
    """Driveトークンを環境変数から読んでいるか（=ファイルに書き戻せない）。"""
    return bool(os.getenv('GOOGLE_DRIVE_TOKEN_JSON'))


def drive_token_info() -> dict | None:
    """Drive OAuthトークンを dict で返す。無ければ None。"""
    raw = os.getenv('GOOGLE_DRIVE_TOKEN_JSON')
    if raw:
        return json.loads(raw)
    path = drive_token_file()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return None


def validate() -> None:
    """起動時チェック。必須設定が無ければ分かりやすい例外を投げる。"""
    missing = [
        name for name in ('DISCORD_TOKEN', 'GEMINI_API_KEY', 'GOOGLE_SHEETS_ID')
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            '必須の環境変数が未設定です: ' + ', '.join(missing)
            + '\n.env を確認してください（.env.example を参照）。'
        )
