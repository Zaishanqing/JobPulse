FROM python:3.11-slim

ARG INSTALL_RESPONSIBILITY_CE=false
ARG TORCH_CPU_VERSION=2.6.0+cpu
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service
COPY packages/contracts /tmp/jobgraph-contracts
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /tmp/jobgraph-contracts
COPY services/matching-service/pyproject.toml services/matching-service/README.md ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && if [ "$INSTALL_RESPONSIBILITY_CE" = "true" ]; then \
         pip install --index-url "$TORCH_CPU_INDEX_URL" "torch==$TORCH_CPU_VERSION" \
         && pip install '.[responsibility-ce]'; \
       else \
         pip install .; \
       fi \
    && rm -rf app
COPY services/matching-service/app ./app
COPY services/matching-service/alembic.ini ./alembic.ini
COPY services/matching-service/migrations ./migrations
COPY services/matching-service/docker ./docker
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall . \
    && sed -i 's/\r$//' /service/docker/entrypoint.sh \
    && chmod 755 /service/docker/entrypoint.sh

EXPOSE 8000 9091
ENTRYPOINT ["/service/docker/entrypoint.sh"]
CMD ["api"]
