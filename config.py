"""環境変数と秘密情報の一元管理。

方針: 「環境変数を優先し、無ければローカルのファイルにフォールバック」。
- ローカル / テスト : .env と *.json ファイルをそのまま使える
- サーバ (Docker)   : すべて環境変数で渡せる（サーバに秘密ファイルを置かなくてよい）

■ プロファイル
チャンネルごとに別アカウント・別ドライブへ記録できるよう、記録先を「プロファイル」
として複数持てる。1プロファイル = {対象チャンネル, 記録モード, スプレッドシート,
Driveフォルダ, Drive認証トークン}。

サービスアカウント（Sheets書き込み用）と Gemini は全プロファイル共通。
各スプレッドシートをサービスアカウントのメールアドレスに共有すれば1つで足りる。
一方 Drive はフォルダ所有アカウントごとの OAuth トークンが要る（サービスアカウントは
マイドライブの容量を持たずアップロードできないため）。

設定は PROFILE_1_*, PROFILE_2_* ... と番号で並べる:
    PROFILE_1_NAME=個人
    PROFILE_1_CHANNEL=123456789012345678
    PROFILE_1_MODE=both            # receipt / income / both
    PROFILE_1_SHEET_ID=...
    PROFILE_1_DRIVE_FOLDER_ID=...
    PROFILE_1_DRIVE_TOKEN_FILE=drive_token.json   # または _DRIVE_TOKEN_JSON=<1行JSON>

PROFILE_1_* が1つも無ければ、従来の単一設定
(RECEIPT_CHANNEL_ID/INCOME_CHANNEL_ID/GOOGLE_SHEETS_ID/GOOGLE_DRIVE_FOLDER_ID)
から1プロファイルを組み立てる（後方互換）。
"""
import json
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# このファイル（＝プロジェクト直下）の場所。秘密ファイルはここ基準で解決する。
# こうすることで、どのディレクトリから起動してもファイルを見つけられる。
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# .env もプロジェクト直下から読む（起動場所に依存しない）
load_dotenv(os.path.join(_BASE_DIR, '.env'))


def _resolve(path: str) -> str:
    """相対パスをプロジェクト直下基準の絶対パスに変換する。"""
    return path if os.path.isabs(path) else os.path.join(_BASE_DIR, path)


# ── Discord ───────────────────────────────────────────────
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', '')

# ── Gemini ────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL   = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')

# 対話認証（ブラウザを開くOAuth）を許可するか。
# ローカルで初回トークンを作るときだけ 1 にする。サーバでは必ず 0。
ALLOW_INTERACTIVE_AUTH = os.getenv('ALLOW_INTERACTIVE_AUTH', '0') == '1'

MODE_RECEIPT = 'receipt'
MODE_INCOME  = 'income'
MODE_BOTH    = 'both'
_VALID_MODES = (MODE_RECEIPT, MODE_INCOME, MODE_BOTH)


@dataclass
class Profile:
    """1チャンネル分の記録先設定。"""
    name: str
    channel_id: int
    mode: str                 # receipt / income / both
    sheet_id: str
    drive_folder_id: str
    drive_token: dict         # Drive OAuth トークン（dict）
    # 更新後トークンを書き戻せるファイルパス。環境変数由来なら None（書けない）。
    drive_token_path: Optional[str] = None

    @property
    def accepts_receipt(self) -> bool:
        return self.mode in (MODE_RECEIPT, MODE_BOTH)

    @property
    def accepts_income(self) -> bool:
        return self.mode in (MODE_INCOME, MODE_BOTH)


def service_account_info() -> dict:
    """サービスアカウント認証情報を dict で返す（環境変数優先・ファイルfallback）。"""
    raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if raw:
        return json.loads(raw)
    path = _resolve(os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'service_account.json'))
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def oauth_credentials_file() -> str:
    return _resolve(os.getenv('GOOGLE_OAUTH_CREDENTIALS_FILE', 'oauth_credentials.json'))


def _load_drive_token(json_var: str, file_var: str,
                      file_default: str) -> tuple[Optional[dict], Optional[str]]:
    """(トークンdict, 書き戻し先パス) を返す。環境変数由来ならパスは None。"""
    raw = os.getenv(json_var)
    if raw:
        return json.loads(raw), None
    path = _resolve(os.getenv(file_var, file_default))
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f), path
    # まだトークンが無い（初回認証前）。パスだけ返して呼び出し側に判断させる。
    return None, path


def _profile_from_env(idx: int) -> Optional[Profile]:
    p = f'PROFILE_{idx}_'
    channel = os.getenv(p + 'CHANNEL')
    if not channel:
        return None
    token, path = _load_drive_token(p + 'DRIVE_TOKEN_JSON', p + 'DRIVE_TOKEN_FILE',
                                    f'drive_token_{idx}.json')
    mode = os.getenv(p + 'MODE', MODE_BOTH).strip().lower()
    if mode not in _VALID_MODES:
        raise RuntimeError(f'{p}MODE が不正です: {mode!r}（{"/".join(_VALID_MODES)}）')
    return Profile(
        name=os.getenv(p + 'NAME', f'プロファイル{idx}'),
        channel_id=int(channel),
        mode=mode,
        sheet_id=os.getenv(p + 'SHEET_ID', ''),
        drive_folder_id=os.getenv(p + 'DRIVE_FOLDER_ID', ''),
        drive_token=token or {},
        drive_token_path=path,
    )


def _legacy_profile() -> Optional[Profile]:
    """従来の単一設定から1プロファイルを組み立てる（後方互換）。"""
    receipt = os.getenv('RECEIPT_CHANNEL_ID')
    income  = os.getenv('INCOME_CHANNEL_ID')
    if not receipt and not income:
        return None
    # 従来は受信/収入で別チャンネルを想定しつつ、実際は同一IDでも動いていた。
    # 同一IDなら both、異なるIDなら…従来コードは両方を別扱いしていたが、
    # プロファイルは1チャンネル=1設定なので、代表チャンネルに both を割り当てる。
    ch = int(receipt or income)
    if receipt and income and receipt != income:
        raise RuntimeError(
            'RECEIPT_CHANNEL_ID と INCOME_CHANNEL_ID が別々のIDです。\n'
            '複数チャンネルは PROFILE_1_*, PROFILE_2_* 形式で設定してください'
            '（.env.example 参照）。'
        )
    token, path = _load_drive_token('GOOGLE_DRIVE_TOKEN_JSON', 'GOOGLE_DRIVE_TOKEN_FILE',
                                    'drive_token.json')
    return Profile(
        name=os.getenv('PROFILE_NAME', '既定'),
        channel_id=ch,
        mode=MODE_BOTH,
        sheet_id=os.getenv('GOOGLE_SHEETS_ID', ''),
        drive_folder_id=os.getenv('GOOGLE_DRIVE_FOLDER_ID', ''),
        drive_token=token or {},
        drive_token_path=path,
    )


def load_profiles() -> list[Profile]:
    profiles = []
    idx = 1
    while (prof := _profile_from_env(idx)) is not None:
        profiles.append(prof)
        idx += 1
    if not profiles:
        legacy = _legacy_profile()
        if legacy:
            profiles.append(legacy)

    channels = [p.channel_id for p in profiles]
    dup = {c for c in channels if channels.count(c) > 1}
    if dup:
        raise RuntimeError(f'同じチャンネルIDが複数プロファイルにあります: {dup}')
    return profiles


def validate() -> list[Profile]:
    """起動時チェック。必須設定が無ければ分かりやすい例外を投げ、プロファイルを返す。"""
    missing = [name for name in ('DISCORD_TOKEN', 'GEMINI_API_KEY')
               if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            '必須の環境変数が未設定です: ' + ', '.join(missing)
            + '\n.env を確認してください（.env.example を参照）。'
        )

    profiles = load_profiles()
    if not profiles:
        raise RuntimeError(
            '記録先プロファイルが1つも設定されていません。\n'
            'PROFILE_1_* を設定してください（.env.example 参照）。'
        )
    for p in profiles:
        lack = [k for k, v in (('SHEET_ID', p.sheet_id),
                               ('DRIVE_FOLDER_ID', p.drive_folder_id)) if not v]
        if lack:
            raise RuntimeError(f'プロファイル「{p.name}」の設定不足: {", ".join(lack)}')
    return profiles
