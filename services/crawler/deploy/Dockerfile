FROM python:3.12-slim-bookworm

ARG DEBIAN_MIRROR_URL=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright

# 系统工具
RUN sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g; s|https://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g; s|http://deb.debian.org/debian-security|https://security.debian.org/debian-security|g; s|https://deb.debian.org/debian-security|https://security.debian.org/debian-security|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends \
    wget curl ca-certificates chromium \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖 (不含 playwright 的浏览器)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
    apscheduler==3.10.4 \
    fastapi==0.115.6 \
    uvicorn==0.34.0 \
    pymysql==1.1.0 \
    dbutils==3.1.0 \
    pyyaml==6.0.2 \
    loguru==0.7.3 \
    openpyxl==3.1.5 \
    httpx==0.28.1 \
    requests==2.32.3 \
    beautifulsoup4==4.12.3 \
    lxml==5.3.0 \
    cryptography==44.0.0 \
    playwright==1.49.0 \
    drissionpage

# Install Playwright system dependencies.
RUN playwright install-deps chromium

# Chromium is installed by apt above; the scraper uses it directly so the
# image does not depend on a Playwright headless-shell mirror.

WORKDIR /app

# Candidate builds use the JobPulse repository root as context.
COPY services/crawler/ /app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV BROWSER_EXECUTABLE_PATH=/usr/bin/chromium

CMD ["python", "-m", "patches.scheduler"]
