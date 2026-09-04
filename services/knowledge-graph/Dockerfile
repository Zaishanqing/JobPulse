FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /tmp/jobgraph-contracts
COPY services/knowledge-graph/pyproject.toml services/knowledge-graph/alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && pip install . \
    && rm -rf app
COPY services/knowledge-graph/app app
COPY services/knowledge-graph/alembic alembic
COPY services/knowledge-graph/config config
COPY services/knowledge-graph/schemas schemas
COPY services/knowledge-graph/scripts scripts
COPY services/knowledge-graph/evaluation/reports/real-graph-baseline/A-DATA-01_frozen_manifest.json evaluation/reports/real-graph-baseline/A-DATA-01_frozen_manifest.json
COPY services/knowledge-graph/evaluation/reports/emerge-v3.2-formal-replay.json evaluation/reports/emerge-v3.2-formal-replay.json
COPY services/knowledge-graph/config/skill_taxonomy_catalog.v1.json config/skill_taxonomy_catalog.v1.json
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
