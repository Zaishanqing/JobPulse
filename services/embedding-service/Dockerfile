FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=10
ARG TORCH_VARIANT=cpu
ARG TORCH_CPU_VERSION=2.6.0+cpu
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /tmp/jobgraph-contracts
COPY services/embedding-service/pyproject.toml services/embedding-service/README.md ./
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$TORCH_VARIANT" = "cpu" ]; then \
      pip install --index-url "$TORCH_CPU_INDEX_URL" --timeout "$PIP_DEFAULT_TIMEOUT" --retries "$PIP_RETRIES" "torch==$TORCH_CPU_VERSION"; \
    elif [ "$TORCH_VARIANT" != "gpu" ]; then \
      echo "Unsupported TORCH_VARIANT: $TORCH_VARIANT (expected cpu or gpu)" >&2; \
      exit 2; \
    fi \
    && mkdir -p app \
    && touch app/__init__.py \
    && pip install --timeout "$PIP_DEFAULT_TIMEOUT" --retries "$PIP_RETRIES" . \
    && rm -rf app
COPY services/embedding-service/app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall .

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
