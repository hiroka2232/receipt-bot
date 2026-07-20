# レシートボット (receipt-bot)

Discord に貼ったレシート画像を Gemini で読み取り、Google Drive に保存し、
Google スプレッドシートに家計簿として記録する Discord ボット。収入はテキストで記録。
記録後は Discord 上で自然文（例:「さっきの金額を2800円に直して」）で修正できる。
誰が記録したか分かるよう、投稿者のサーバー表示名も記録者として記録する。

## 構成ファイル

| ファイル | 役割 |
|---|---|
| `bot.py` | Discord ボット本体（エントリポイント） |
| `config.py` | 環境変数・秘密情報・**プロファイル**の読み込みを一元管理 |
| `gemini.py` | Gemini でレシート/テキストを解析・修正指示を判定 |
| `gdrive.py` | Google Drive へ領収書画像をアップロード（`DriveClient`） |
| `gsheets.py` | Google スプレッドシートへ記録（フラット2タブ型フォーマット） |
| `auth_drive.py` | Drive の初回OAuth認証（アカウントごとに1回だけ実行） |
| `Dockerfile` / `docker-compose.yml` | サーバ常駐運用向けのコンテナ設定 |

秘密情報は **環境変数が優先、無ければファイルにフォールバック**。
そのためローカル（ファイル）でもサーバ（環境変数）でも同じコードで動く。

## スプレッドシートのフォーマット

**支出**タブと**収入**タブに1行ずつ追記し（種別＝タブ）、**全体収支**タブが月別集計を
数式（`SUMIFS`）で自動計算する。空のスプレッドシートを渡せば、初回記録時
（または `!初期化` コマンド）にこの3タブを自動生成する。会計年度は **2月始まり**。

支出タブ（収入タブも同じ6列）:

| 日付 | 内容 | 金額 | 領収書等 | 備考 | 記録者 |
|---|---|---|---|---|---|
| 2026-07-17 | 九大生協（…） | ¥274 | 20260717_九大生協.jpg | AI自動記録（Gemini） | たろう |

- 月別集計は日付の範囲を見る `SUMIFS` なので **行数に上限が無い**
- 日付は `YYYY-MM-DD` の実データ（数式が月を判定できる）
- 領収書は **ファイル名を表示テキストにした Drive へのハイパーリンク**（`=HYPERLINK()`）
- 記録者はレシート画像／収入テキストを投稿した人の **そのサーバーでの表示名**（ニックネーム優先）
- 会計年度を変える場合は `gsheets.py` の `FISCAL_START_YEAR` / `FISCAL_START_MONTH` を編集

## プロファイル（複数アカウント対応）

チャンネルごとに **別アカウント・別ドライブ** へ記録できる。1プロファイル =
`{対象チャンネル, 記録モード, スプレッドシート, Driveフォルダ, Drive認証トークン}`。
`.env` に `PROFILE_1_*`, `PROFILE_2_* …` と番号で並べる（記入例は `.env.example`）。

- **サービスアカウント**（Sheets書き込み用）は全プロファイル共通。
  各スプレッドシートを SA の `client_email` に「編集者」で共有すれば1つで足りる。
- **Drive のトークンだけ**はフォルダを持つアカウントごとに必要
  （サービスアカウントはマイドライブ容量を持たずアップロードできないため）。
- `PROFILE_1_*` を1つも設定しなければ、従来の単一設定
  （`RECEIPT_CHANNEL_ID` 等）から1プロファイルを組み立てる（後方互換）。

---

## 1. ローカルで動かす（テスト用）

前提: このリポジトリに `.venv`（Python仮想環境）が作成済み。

```powershell
# 1. .env を用意（初回のみ）
Copy-Item .env.example .env    # → .env を開いて実際の値を記入

# 2. アカウントごとに Drive のトークンを作成（ブラウザが開く）
#    出力ファイル名を PROFILE_x_DRIVE_TOKEN_FILE に合わせる
.\.venv\Scripts\python.exe auth_drive.py drive_token_1.json
#    2アカウント目があれば別名でもう一度（認証したいアカウントでログイン）
#    .\.venv\Scripts\python.exe auth_drive.py drive_token_2.json

# 3. 起動
.\.venv\Scripts\python.exe bot.py
```

停止は `Ctrl + C`。初回記録時に 支出/収入/全体収支 の3タブが自動生成される
（先に作りたい場合は Discord のチャンネルで `!初期化` コマンド）。

> **新しいスプレッドシートを使うとき**: 空のスプレッドシートを作り、
> サービスアカウントの `client_email` に「編集者」で共有してから
> `PROFILE_x_SHEET_ID` に指定する。`!初期化` は既存データ（2行目以降）には
> 触れず、見出しと全体収支の数式だけ用意する（冪等）。

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
   # サービスアカウント（全プロファイル共通）
   GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}   # 1行
   # Driveトークンはプロファイルごとに渡す（ローカルで作った drive_token_x.json の中身）
   PROFILE_1_DRIVE_TOKEN_JSON={"token":"...","refresh_token":"...",...}   # 1行
   # PROFILE_2_DRIVE_TOKEN_JSON=...   # 2アカウント目
   ```

   JSONの1行化（ローカルPC・PowerShell）:
   ```powershell
   (Get-Content service_account.json -Raw | ConvertFrom-Json | ConvertTo-Json -Compress)
   (Get-Content drive_token_1.json   -Raw | ConvertFrom-Json | ConvertTo-Json -Compress)
   ```
   出力をコピーして、VPSの `.env` の該当行に貼り付ける。

3. 起動:

   ```bash
   docker compose up -d --build     # ビルドしてバックグラウンド常駐
   docker compose logs -f           # ログを見る（Ctrl+Cで表示だけ抜ける）
   docker compose down              # 停止・削除
   ```

> **重要**: `auth_drive.py` はブラウザが必要なので **VPSでは実行しない**。
> ローカルPCでアカウントごとに一度だけ実行して `drive_token_x.json` を作り、その中身を
> `PROFILE_x_DRIVE_TOKEN_JSON` としてVPSに渡す。refresh_token があるので以後は自動更新される。

---

## 秘密情報の扱い（重要）

`.env` / `service_account.json` / `oauth_credentials.json` / `drive_token*.json` は
`.gitignore` 済みで **Gitにコミットされない**。第三者に渡さないこと。
