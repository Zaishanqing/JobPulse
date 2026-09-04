# Heavy system packages (Tesseract OCR + Chinese data) live in a separate,
# rarely rebuilt base image (apps/api/Dockerfile.base). compose-up.ps1,
# compose-rebuild.ps1, export-offline-images.ps1 and competition-smoke.sh all
# build it before this image; a manual build needs it present locally first.
FROM jobpulse-main-backend-base:candidate

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCR_PROVIDER=tesseract \
    JOBPULSE_PROJECT_ROOT=/app \
    JOBPULSE_APP_ROOT=/app

WORKDIR /app

COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
    && python -m pip install /tmp/jobgraph-contracts

COPY apps/api/pyproject.toml apps/api/README.md ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && python -m pip install . \
    && rm -rf app

COPY apps/api/app ./app
COPY apps/api/config ./config
COPY apps/api/docs ./docs
COPY apps/api/data ./data
COPY data/extraction-audit/position-cleaned-publishable-v3-recleaned-20260814.zip ./data/extraction-audit/
COPY data/extraction-audit/recent-position-signals-selection.v1.json ./data/extraction-audit/
COPY data/extraction-audit/capability-evolution-2026-selection.json ./data/extraction-audit/
COPY apps/api/scripts ./scripts
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
COPY packages/contracts/jobgraph_contracts ./packages/contracts/jobgraph_contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps --force-reinstall . \
    && PYTHON_CONFIG_DIR="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/config" \
    && mkdir -p "$PYTHON_CONFIG_DIR" \
    && find /app/config -maxdepth 1 -type f -exec cp {} "$PYTHON_CONFIG_DIR/" \; \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/data /app/uploads \
    && chown -R app:app /app/data /app/uploads

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["sh", "-c", "python -m alembic upgrade head && python scripts/seed_bootstrap_accounts.py && python scripts/sync_skill_taxonomy_catalog.py && python scripts/sync_position_taxonomy_catalog.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
