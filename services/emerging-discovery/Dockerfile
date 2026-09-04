FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /tmp/jobgraph-contracts
COPY services/emerging-discovery/pyproject.toml services/emerging-discovery/alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && pip install . \
    && rm -rf app
COPY services/emerging-discovery/alembic ./alembic
COPY services/emerging-discovery/app ./app
COPY services/emerging-discovery/config ./config
COPY services/emerging-discovery/evaluation ./evaluation
COPY services/emerging-discovery/scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
