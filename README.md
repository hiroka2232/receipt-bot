# レシートボット (receipt-bot)

Discord に貼ったレシート画像を Gemini で読み取り、Google Drive に保存し、
Google スプレッドシートに家計簿として記録する Discord ボット。収入はテキストで記録。

## 構成ファイル

| ファイル | 役割 |
|---|---|
| `bot.py` | Discord ボット本体（エントリポイント） |
| `config.py` | 環境変数・秘密情報の読み込みを一元管理 |
| `gemini.py` | Gemini でレシート/テキストを解析 |
| `gdrive.py` | Google Drive へ領収書画像をアップロード |
| `gsheets.py` | Google スプレッドシートへ記録 |
| `auth_drive.py` | Drive の初回OAuth認証（ローカルで1回だけ実行） |
| `Dockerfile` / `docker-compose.yml` | サーバ常駐運用向けのコンテナ設定 |

秘密情報は **環境変数が優先、無ければファイルにフォールバック**。
そのためローカル（ファイル）でもサーバ（環境変数）でも同じコードで動く。

---

## 1. ローカルで動かす（テスト用）

前提: このリポジトリに `.venv`（Python仮想環境）が作成済み。

```powershell
# 1. .env を用意（初回のみ）
Copy-Item .env.example .env    # → .env を開いて実際の値を記入

# 2. Drive のトークンを作成（初回のみ・ブラウザが開く）
.\.venv\Scripts\python.exe auth_drive.py

# 3. 起動
.\.venv\Scripts\python.exe bot.py
```

停止は `Ctrl + C`。

> `.venv` が無い場合は作成:
> ```powershell
> py -3.14 -m venv .venv
> .\.venv\Scripts\python.exe -m pip install -r requirements.txt
> ```

---

## 2. サーバで24/7動かす（Docker）

### そもそも Docker とは？

一言でいうと **「アプリを“箱（コンテナ）”に丸ごと詰めて、どこでも同じように動かす仕組み」** です。

いま困っていた「PythonのバージョンやライブラリがPCによって違って動かない」問題を、
Docker は根本から解決します。箱の中に **OS相当の環境・Python・ライブラリ・自分のコード**を
すべて閉じ込めるので、あなたのPCでもVPSでも、箱ごと動かせば中身は完全に同じです。

用語を最小限だけ:

| 用語 | 意味 | このプロジェクトでは |
|---|---|---|
| **イメージ (image)** | 箱の“設計図”から作った“完成品テンプレート” | `Dockerfile` から `docker build` で作る |
| **コンテナ (container)** | イメージを実際に起動した“動いている箱” | この中でボットが常駐する |
| **Dockerfile** | イメージの作り方を書いた設計図 | Python導入→依存インストール→コード配置 |
| **docker compose** | 複数の設定（再起動ポリシー等）をまとめて管理する道具 | `docker-compose.yml` |

イメージ＝レシート（設計図）、コンテナ＝そのレシートで作った料理、と考えると分かりやすいです。

### なぜこのボットに向くのか

- **環境の再現性**: 「自分のPCでは動くのにサーバで動かない」が起きない
- **常駐と自動復帰**: `restart: unless-stopped` で、クラッシュやサーバ再起動後も自動で立ち上がる
- **秘密情報を焼き込まない**: コードだけを箱に入れ、トークン類は環境変数で外から渡す

### 手順（VPS側）

前提: VPS に Docker と Docker Compose が入っていること
（未導入なら `curl -fsSL https://get.docker.com | sh`）。

1. このリポジトリをVPSに置く（`git clone` など。秘密ファイルはコミットされない）
2. `.env` をVPS上に用意する。**秘密情報のJSONは1行にして環境変数で渡す**:

   ```bash
   # サービスアカウント
   GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}   # 1行
   # Driveトークン（ローカルで auth_drive.py を実行して作った drive_token.json の中身）
   GOOGLE_DRIVE_TOKEN_JSON={"token":"...","refresh_token":"...",...}   # 1行
   ```

   JSONの1行化（ローカルPC・PowerShell）:
   ```powershell
   (Get-Content service_account.json -Raw | ConvertFrom-Json | ConvertTo-Json -Compress)
   (Get-Content drive_token.json     -Raw | ConvertFrom-Json | ConvertTo-Json -Compress)
   ```
   出力をコピーして、VPSの `.env` の該当行に貼り付ける。

3. 起動:

   ```bash
   docker compose up -d --build     # ビルドしてバックグラウンド常駐
   docker compose logs -f           # ログを見る（Ctrl+Cで表示だけ抜ける）
   docker compose down              # 停止・削除
   ```

> **重要**: `auth_drive.py` はブラウザが必要なので **VPSでは実行しない**。
> ローカルPCで一度だけ実行して `drive_token.json` を作り、その中身を
> `GOOGLE_DRIVE_TOKEN_JSON` としてVPSに渡す。refresh_token があるので以後は自動更新される。

---

## 秘密情報の扱い（重要）

`.env` / `service_account.json` / `oauth_credentials.json` / `drive_token.json` は
`.gitignore` 済みで **Gitにコミットされない**。第三者に渡さないこと。
