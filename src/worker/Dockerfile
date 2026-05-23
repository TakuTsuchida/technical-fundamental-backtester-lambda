# Stage 1: install dependencies
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir "uv==0.4.*"

WORKDIR /build

COPY src/shared /build/src/shared

RUN uv pip install --system --no-cache --target /build/deps "pyarrow>=16.0"
RUN uv pip install --system --no-cache --target /build/deps /build/src/shared

# Stage 2: Lambda runtime
FROM public.ecr.aws/lambda/python:3.12

RUN dnf install -y shadow-utils && \
    /usr/sbin/useradd --no-create-home --shell /sbin/nologin lambda-user && \
    dnf clean all

COPY --from=builder --chown=lambda-user:lambda-user /build/deps ${LAMBDA_TASK_ROOT}/
COPY --chown=lambda-user:lambda-user src/worker/handler.py ${LAMBDA_TASK_ROOT}/handler.py

USER lambda-user

HEALTHCHECK CMD python -c "import handler; assert callable(handler.handler)" || exit 1

CMD ["handler.handler"]
