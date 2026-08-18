# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:20-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${NODE_IMAGE} AS frontend-build

WORKDIR /app/frontend
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV npm_config_registry=${NPM_REGISTRY} \
    npm_config_fetch_retries=3 \
    npm_config_fetch_retry_mintimeout=10000 \
    npm_config_fetch_retry_maxtimeout=60000 \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_progress=false
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --prefer-offline
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JOB_ONE_STOP_CONFIG=/app/config.yaml \
    JOB_ONE_STOP_DATABASE_URL=sqlite:////data/job_one_stop.sqlite3 \
    JOB_ONE_STOP_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000 \
    JOB_ONE_STOP_OPENCLI_SERVER_ENABLED=false

WORKDIR /app
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=0 \
    PIP_DEFAULT_TIMEOUT=60

# 单层安装：pandas/lxml/numpy 等大包用长超时兜底，pip cache mount 负责缓存
COPY requirements-runtime.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 --timeout 300 \
    --index-url "${PIP_INDEX_URL}" \
    -r requirements-runtime.txt

COPY backend ./backend
# config.yaml 是本地 gitignore 文件；镜像用跟踪的模板生成一份默认配置。
# 运行时 docker-compose 会用宿主机的 ./config.yaml 覆盖挂载（若存在）。
COPY config.example.yaml ./config.example.yaml
RUN cp config.example.yaml config.yaml
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.getenv('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5).read()"
CMD sh -c "python -m uvicorn backend.app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}"
