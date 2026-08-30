# Stage: builder -- shared dependency install, reused by every function's
# runtime stage below via `COPY --from=builder`. Keeping this as a single
# stage means each function's deps are only resolved once per build cache
# entry instead of duplicated per-Dockerfile.
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir "uv==0.4.*"

WORKDIR /build

COPY src/shared /build/src/shared

RUN uv pip install --system --no-cache --target /build/deps "pyarrow>=16.0"
RUN uv pip install --system --no-cache --target /build/deps /build/src/shared

# Stage: worker -- select with `docker build --target worker`
FROM public.ecr.aws/lambda/python:3.12 AS worker

RUN dnf install -y shadow-utils && \
    /usr/sbin/useradd --no-create-home --shell /sbin/nologin lambda-user && \
    dnf clean all

COPY --from=builder --chown=lambda-user:lambda-user /build/deps ${LAMBDA_TASK_ROOT}/
COPY --chown=lambda-user:lambda-user src/worker/ ${LAMBDA_TASK_ROOT}/worker/
COPY --chown=lambda-user:lambda-user src/worker/handler.py ${LAMBDA_TASK_ROOT}/handler.py

USER lambda-user

HEALTHCHECK CMD python -c "import handler; assert callable(handler.handler)" || exit 1

CMD ["handler.handler"]

# Stage: price_lake -- select with `docker build --target price_lake`
FROM public.ecr.aws/lambda/python:3.12 AS price_lake

# Baked in at build time (see Makefile's build-price-lake target) so
# metadata.json can record which commit produced the parquet dataset.
ARG GIT_COMMIT_SHA
ENV GIT_COMMIT_SHA=${GIT_COMMIT_SHA}

# _fetch_latest_rows runs 20 concurrent worker threads that repeatedly
# json.loads() large payloads. glibc's default per-thread malloc arenas
# fragment under that churn and never return freed memory to the OS, so
# peak RSS climbs for the entire run even though the /tmp-spill batching
# already bounds how much data is ever live in Python objects at once
# (confirmed via a local repro on this same base image: with the default
# arena behavior peak RSS kept climbing for all 4505 simulated codes;
# with MALLOC_ARENA_MAX=1 it plateaued after ~2000). Forcing a single
# arena removes the fragmentation instead of just shrinking it.
ENV MALLOC_ARENA_MAX=1

RUN dnf install -y shadow-utils && \
    /usr/sbin/useradd --no-create-home --shell /sbin/nologin lambda-user && \
    dnf clean all

COPY --from=builder --chown=lambda-user:lambda-user /build/deps ${LAMBDA_TASK_ROOT}/
COPY --chown=lambda-user:lambda-user src/price_lake/ ${LAMBDA_TASK_ROOT}/price_lake/
COPY --chown=lambda-user:lambda-user src/price_lake/handler.py ${LAMBDA_TASK_ROOT}/handler.py

USER lambda-user

HEALTHCHECK CMD python -c "import handler; assert callable(handler.handler)" || exit 1

CMD ["handler.handler"]
