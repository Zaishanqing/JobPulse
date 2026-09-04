FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/jd-extraction/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install /tmp/jobgraph-contracts
COPY services/jd-extraction/src ./src
COPY services/jd-extraction/scripts ./scripts
COPY services/jd-extraction/config ./config

RUN useradd --create-home --uid 10001 extraction \
    && chown -R extraction:extraction /app
USER extraction

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
