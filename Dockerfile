# レシートボット本体をパッケージ化するための設計図。
# python:3.12-slim = 余計なものを削った軽量な公式Pythonイメージ。
FROM python:3.12-slim

# ログを即時出力し、.pyc を作らない（コンテナ向けの定番設定）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依存だけ先にコピーしてインストール（コード変更時にキャッシュが効く）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリのコード（.pyファイル）をコピー。
# .env や *.json（秘密情報）は .dockerignore で除外し、環境変数で渡す。
COPY *.py ./

# コンテナ起動時に実行されるコマンド
CMD ["python", "bot.py"]
