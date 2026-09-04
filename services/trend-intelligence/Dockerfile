FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /tmp/jobgraph-contracts
COPY services/trend-intelligence/pyproject.toml services/trend-intelligence/alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && pip install . \
    && rm -rf app
COPY services/trend-intelligence/alembic ./alembic
COPY services/trend-intelligence/app ./app
COPY services/trend-intelligence/demo ./demo
COPY services/trend-intelligence/scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.entrypoint"]
CMD ["api"]
