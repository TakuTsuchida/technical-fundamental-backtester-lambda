AWS_REGION ?= ap-northeast-1
ECR_REPO   ?= my-service-worker
ECR_URI    ?=
IMAGE_TAG  ?= $(shell git rev-parse --short HEAD)

.PHONY: install lint format typecheck test \
        build-dispatcher build-worker \
        push-worker \
        deploy-dispatcher deploy-worker

# ── Dev ───────────────────────────────────────────────────────────────────────

install:
	uv sync --all-groups

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy --package shared --package dispatcher --package worker
	uv run mypy tests/

test:
	uv run pytest

# ── Build ─────────────────────────────────────────────────────────────────────

build-dispatcher:
	bash scripts/package_zip.sh dispatcher

build-worker:
	@test -n "$(IMAGE_TAG)" || (echo "ERROR: IMAGE_TAG is empty" >&2; exit 1)
	docker build -f src/worker/Dockerfile -t $(ECR_REPO):$(IMAGE_TAG) -t $(ECR_REPO):latest .

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

# ── Deploy ────────────────────────────────────────────────────────────────────

deploy-dispatcher: build-dispatcher
	aws lambda update-function-code \
		--function-name my-service-dispatcher \
		--zip-file fileb://dist/dispatcher.zip \
		--region $(AWS_REGION)

deploy-worker: build-worker push-worker
	@test -n "$(ECR_URI)" || (echo "ERROR: ECR_URI is not set" >&2; exit 1)
	aws lambda update-function-code \
		--function-name my-service-worker \
		--image-uri $(ECR_URI):$(IMAGE_TAG) \
		--region $(AWS_REGION)
