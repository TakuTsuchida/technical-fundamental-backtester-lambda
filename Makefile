AWS_REGION          ?= ap-northeast-1
ECR_REPO            ?= my-service-worker
ECR_URI             ?=
ECR_REPO_PRICE_LAKE ?= stock-data-pipeline-price-lake
ECR_URI_PRICE_LAKE  ?=
IMAGE_TAG           ?= $(shell git rev-parse --short HEAD)

.PHONY: install lint format typecheck test \
        build-dispatcher build-fins-dispatcher build-worker build-price-lake \
        push-worker push-price-lake \
        deploy-dispatcher deploy-fins-dispatcher deploy-worker deploy-price-lake

# ── Dev ───────────────────────────────────────────────────────────────────────

install:
	uv sync --all-groups

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy --package shared --package dispatcher --package worker --package fins_dispatcher \
		--package price_lake
	uv run mypy tests/

test:
	uv run pytest

# ── Build ─────────────────────────────────────────────────────────────────────

build-dispatcher:
	bash scripts/package_zip.sh dispatcher

build-fins-dispatcher:
	bash scripts/package_zip.sh fins_dispatcher

build-worker:
	@test -n "$(IMAGE_TAG)" || (echo "ERROR: IMAGE_TAG is empty" >&2; exit 1)
	docker build -f src/worker/Dockerfile -t $(ECR_REPO):$(IMAGE_TAG) -t $(ECR_REPO):latest .

build-price-lake:
	@test -n "$(IMAGE_TAG)" || (echo "ERROR: IMAGE_TAG is empty" >&2; exit 1)
	docker build -f src/price_lake/Dockerfile \
		--build-arg GIT_COMMIT_SHA=$(IMAGE_TAG) \
		-t $(ECR_REPO_PRICE_LAKE):$(IMAGE_TAG) -t $(ECR_REPO_PRICE_LAKE):latest .

# ── Push ──────────────────────────────────────────────────────────────────────

push-worker:
	@test -n "$(ECR_URI)" || (echo "ERROR: ECR_URI is not set" >&2; exit 1)
	@test -n "$(IMAGE_TAG)" || (echo "ERROR: IMAGE_TAG is empty" >&2; exit 1)
	aws ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin $(ECR_URI)
	docker tag $(ECR_REPO):$(IMAGE_TAG) $(ECR_URI):$(IMAGE_TAG)
	docker tag $(ECR_REPO):latest      $(ECR_URI):latest
	docker push $(ECR_URI):$(IMAGE_TAG)
	docker push $(ECR_URI):latest

push-price-lake:
	@test -n "$(ECR_URI_PRICE_LAKE)" || (echo "ERROR: ECR_URI_PRICE_LAKE is not set" >&2; exit 1)
	@test -n "$(IMAGE_TAG)" || (echo "ERROR: IMAGE_TAG is empty" >&2; exit 1)
	aws ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin $(ECR_URI_PRICE_LAKE)
	docker tag $(ECR_REPO_PRICE_LAKE):$(IMAGE_TAG) $(ECR_URI_PRICE_LAKE):$(IMAGE_TAG)
	docker tag $(ECR_REPO_PRICE_LAKE):latest      $(ECR_URI_PRICE_LAKE):latest
	docker push $(ECR_URI_PRICE_LAKE):$(IMAGE_TAG)
	docker push $(ECR_URI_PRICE_LAKE):latest

# ── Deploy ────────────────────────────────────────────────────────────────────

deploy-dispatcher: build-dispatcher
	aws lambda update-function-code \
		--function-name my-service-dispatcher \
		--zip-file fileb://dist/dispatcher.zip \
		--region $(AWS_REGION)

deploy-fins-dispatcher: build-fins-dispatcher
	aws lambda update-function-code \
		--function-name my-service-fins-dispatcher \
		--zip-file fileb://dist/fins_dispatcher.zip \
		--region $(AWS_REGION)

deploy-worker: build-worker push-worker
	@test -n "$(ECR_URI)" || (echo "ERROR: ECR_URI is not set" >&2; exit 1)
	aws lambda update-function-code \
		--function-name my-service-worker \
		--image-uri $(ECR_URI):$(IMAGE_TAG) \
		--region $(AWS_REGION)

deploy-price-lake: build-price-lake push-price-lake
	@test -n "$(ECR_URI_PRICE_LAKE)" || (echo "ERROR: ECR_URI_PRICE_LAKE is not set" >&2; exit 1)
	aws lambda update-function-code \
		--function-name stock-data-pipeline-price-lake \
		--image-uri $(ECR_URI_PRICE_LAKE):$(IMAGE_TAG) \
		--region $(AWS_REGION)
