FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install /tmp/jobgraph-contracts
COPY services/cv-extraction /app/cv-extraction
WORKDIR /app/cv-extraction
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
