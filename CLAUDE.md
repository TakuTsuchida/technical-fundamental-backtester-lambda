# technical-fundamental-backtester-lambda

J-Quants API を使った株価取得バッチの Lambda コードリポジトリ（Python 3.12）。

## プロジェクト構成

- `src/token_refresher/` — J-Quants 認証 → SSM `id_token` 更新
- `src/dispatcher/`      — 銘柄一覧取得 → S3 保存 → SQS enqueue
- `src/worker/`          — SQS 消費 → OHLCV 取得 → Parquet → S3
- `src/price_lake/`      — 週次で daily-prices の最新スナップショットを結合し
  lake-store（parquet + metadata.json）へ upsert 登録
- `src/shared/`          — SSM ヘルパー + J-Quants クライアント + S3Store（全関数共用）
- `tests/`               — pytest スイート
- `scripts/`             — ZIP パッケージングスクリプト
- `.github/workflows/`   — CI + デプロイワークフロー × 2

## パッケージ管理

必ず `uv` を使う（`pip install` 禁止）:

```bash
uv sync --all-groups     # dev + worker 依存含めてすべてインストール
uv run ruff check .      # lint
uv run ruff format .     # フォーマット
uv run mypy src/         # 型チェック
uv run pytest            # テスト実行
```

## ビルド / デプロイ

```bash
make build-token-refresher   # dist/token-refresher.zip
make build-dispatcher        # dist/dispatcher.zip
make build-worker            # Docker イメージビルド（ECR 向け）
make build-price-lake        # Docker イメージビルド（GIT_COMMIT_SHA を焼き込み）
make push-worker             # ECR プッシュ（AWS 認証が必要）
make push-price-lake
make deploy-token-refresher  # ZIP ビルド + Lambda 更新
make deploy-dispatcher
make deploy-worker           # Docker ビルド + ECR プッシュ + Lambda 更新
make deploy-price-lake
```

## Lambda ハンドラーの制約

- Terraform に `handler = "handler.handler"` が設定されているため、ZIP のルートに `handler.py` が必要
- `boto3` は ZIP に含めない（Lambda ランタイムが提供）
- `shared/` は ZIP ごとにベンダリング（コピー）する

## AWS リージョン

`ap-northeast-1`（東京）
