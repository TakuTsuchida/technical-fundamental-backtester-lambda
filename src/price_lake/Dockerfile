# Stage 1: install dependencies
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir "uv==0.4.*"

WORKDIR /build

COPY src/shared /build/src/shared

RUN uv pip install --system --no-cache --target /build/deps "pyarrow>=16.0"
RUN uv pip install --system --no-cache --target /build/deps /build/src/shared

# Stage 2: Lambda runtime
FROM public.ecr.aws/lambda/python:3.12

# Baked in at build time (see Makefile's build-price-lake target) so
# metadata.json can record which commit produced the parquet dataset.
ARG GIT_COMMIT_SHA
ENV GIT_COMMIT_SHA=${GIT_COMMIT_SHA}

RUN dnf install -y shadow-utils && \
    /usr/sbin/useradd --no-create-home --shell /sbin/nologin lambda-user && \
    dnf clean all

COPY --from=builder --chown=lambda-user:lambda-user /build/deps ${LAMBDA_TASK_ROOT}/
COPY --chown=lambda-user:lambda-user src/price_lake/ ${LAMBDA_TASK_ROOT}/price_lake/
COPY --chown=lambda-user:lambda-user src/price_lake/handler.py ${LAMBDA_TASK_ROOT}/handler.py

USER lambda-user

HEALTHCHECK CMD python -c "import handler; assert callable(handler.handler)" || exit 1

CMD ["handler.handler"]
