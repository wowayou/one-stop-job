# Operations

## 推荐运行方式

日常试用和开发优先使用本地开发模式，启动快、日志直接、热更新清晰：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cd frontend && npm install && cd ..

# 终端 1
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

# 终端 2
cd frontend && npm run dev
```

访问：

```text
http://127.0.0.1:5173/
```

Docker Compose 用于 Windows 一键运行、交接部署或需要前后端单端口托管的场景：

```bash
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8000/
```

停止：

```bash
docker compose down
```

Windows Docker 用户可双击：

- `start_app.bat`
- `rebuild_app.bat`
- `status_app.bat`
- `stop_app.bat`
- `run_deploy_check.bat`

日常打开 Docker 版本用 `start_app.bat`，它会复用已有镜像；首次没有镜像时 Docker Compose 会自动构建。拉取新代码、修改 `Dockerfile`、`requirements-small.txt`、`requirements-large.txt`、`frontend/package-lock.json` 或前端源码后，再用 `rebuild_app.bat` 强制重建。这样可以避开每次启动都访问 npm/pip 镜像源导致的卡顿或失败。

启动前或排障时先跑轻量部署自检：

```bash
scripts/deploy_check.sh
```

它不要求 `.venv` 或 `frontend/node_modules`，只检查项目关键文件、`config.yaml`、Docker Compose 配置，并在服务已启动时探测 `/api/health` 和 `/api/ready`。如果应用未启动，会给出告警但不会把本地静态检查判为失败。

## Windows + WSL 的 Docker 选择

如果项目放在 WSL 路径下，优先使用 Docker Desktop 的 WSL Integration。

操作路径：

1. 打开 Docker Desktop。
2. Settings -> Resources -> WSL Integration。
3. 开启保存本项目的 WSL distro。
4. 重启 `start_app.bat`。

不优先建议在 WSL 里单独安装 Docker Engine，原因是 Windows 接手者通常已经有 Docker Desktop，单独安装会多一套服务、权限和网络配置。只有这台机器要脱离 Docker Desktop、长期作为 Linux 原生宿主机运行时，再考虑在 WSL/Ubuntu 内安装 Docker Engine。

如果已经决定长期在 WSL/Linux 里运行，建议固定使用 WSL 原生 Docker，并用 Docker Desktop 只做 fallback。此时确认：

```bash
which docker
docker version
docker compose version
scripts/docker_doctor.sh
```

WSL 原生 Docker 下 `which docker` 应接近：

```text
/usr/bin/docker
```

如果显示 `/mnt/c/Program Files/Docker/...`，说明还在用 Docker Desktop 的 Windows shim。

如果双击 bat 时看到类似：

```text
wsl: Failed to translate 'E:\Dev\...'
```

这是 Windows PATH 里存在 WSL 无法翻译的失效路径，通常不影响容器启动。要消除提示，可在 Windows「环境变量」里删除这些不存在的 PATH 项，或让相关盘符/目录恢复可访问。

## 构建加速和网络故障

首次构建需要拉基础镜像并下载 npm/pip 依赖，受网络影响最大。后续只要 `Dockerfile`、`requirements-small.txt`、`requirements-large.txt`、`frontend/package-lock.json` 没变，Docker cache 会复用大部分步骤。

当前 Dockerfile 已做的加速：

- Node 构建镜像使用 `node:20-bookworm-slim`，比完整 `bookworm` 更小。
- healthcheck 使用 Python 标准库，不再 `apt-get install curl`。
- `npm ci` 使用 BuildKit cache，并默认走 `https://registry.npmmirror.com`。
- `pip install` 使用 BuildKit cache，默认只走 `https://mirrors.aliyun.com/pypi/simple`，避免官方 PyPI/文件站超时拖住构建。
- Docker 镜像按 `requirements-small.txt` 和 `requirements-large.txt` 分阶段安装运行时依赖；`requirements.txt` 追加本地测试依赖，不进入生产镜像。

可调构建参数：

```bash
NPM_REGISTRY=https://registry.npmmirror.com \
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
docker compose up -d --build
```

如果 npm/pip 镜像源不适合当前网络，可换另一个单一 PyPI 镜像源：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple docker compose up -d --build
```

或切回官方源：

```bash
NPM_REGISTRY=https://registry.npmjs.org \
PIP_INDEX_URL=https://pypi.org/simple \
docker compose up -d --build
```

如果失败信息是 `ECONNRESET`、`i/o timeout`、`DeadlineExceeded`，优先处理网络和镜像源，不要先改应用代码。

如果失败信息类似：

```text
No matching distribution found for fastapi<0.116.0,>=0.111.0
```

而前面其它包已经能从镜像源下载，通常是该镜像源的 Python 包索引同步异常。可直接切主源：

```bash
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple docker compose up -d --build
```

如果构建在 `pip install` 步骤超过 10 分钟只有 timeout/retry 日志，先 `Ctrl+C` 中断，再换单一镜像源重试。不要同时配置一个很慢的 `PIP_EXTRA_INDEX_URL`，pip 可能会为依赖元数据访问多个源，把构建拖到几十分钟。

诊断：

```bash
scripts/docker_doctor.sh
scripts/docker_doctor.sh --pull-check
```

`--pull-check` 会真实拉取 `node:20-bookworm-slim` 和 `python:3.12-slim`，会访问外网。

## 健康检查和云端接口

服务提供三个运行状态接口：

- `GET /api/health`：轻量探活，适合 Docker/云平台 healthcheck。
- `GET /api/ready`：就绪检查；配置解析或数据库连接失败时返回 503，其它可降级项以 `warning` 标记。
- `GET /api/diagnostics/deployment`：完整部署诊断，包含配置文件、数据库、前端构建、CORS、上传上限、AI 和采集来源状态。

容器镜像已预留云端常见变量：

| 变量 | 用途 |
|---|---|
| `PORT` | 容器内监听端口，默认 `8000` |
| `HOST` | Uvicorn 监听地址，默认 `0.0.0.0` |
| `DATABASE_URL` | 云端平台常见数据库 URL；优先级低于 `JOB_ONE_STOP_DATABASE_URL` |
| `JOB_ONE_STOP_DATABASE_URL` | 应用数据库 URL，本地 Docker 默认 `sqlite:////data/job_one_stop.sqlite3` |
| `JOB_ONE_STOP_CONFIG` | 配置文件路径，本地 Docker 默认 `/app/config.yaml` |
| `JOB_ONE_STOP_CORS_ORIGINS` | 逗号分隔的浏览器来源白名单 |
| `JOB_ONE_STOP_MAX_UPLOAD_MB` | CSV/XLSX 上传大小上限，默认 `20` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | AI 兜底能力配置 |

本地 Compose 可通过 `HOST_PORT` 改宿主机端口，通过 `PORT` 改容器内端口：

```bash
HOST_PORT=18000 PORT=8000 docker compose up -d --build
```

如果部署到云端，通常让平台注入 `PORT` 和数据库连接字符串；同时设置 `JOB_ONE_STOP_CORS_ORIGINS` 为实际访问域名。

## 数据保存位置

Docker 运行：

- SQLite 数据库：Docker volume `job_one_stop_data`，容器内路径 `/data/job_one_stop.sqlite3`。
- 配置文件：仓库根目录 `config.yaml`，挂载到容器 `/app/config.yaml`。
- 前端静态文件：镜像内 `/app/frontend/dist`，由 FastAPI 托管。
- AI 密钥：宿主机环境变量或本地 `.env`，不进入 Git。
- BOSS/智联 OpenCLI 登录态：宿主机浏览器/OpenCLI 自己管理，不进入容器。

开发模式：

- 默认 SQLite 数据目录来自 `config.yaml general.data_dir`，当前是 `./data/job_one_stop`。
- `data/` 已被 `.gitignore` 排除。

本地开发和 Docker 默认不互通。需要复制数据时先停止正在运行的后端，再从对应 SQLite 文件或 Docker volume 做备份/恢复。

查看 Docker volume：

```bash
docker volume ls | grep job_one_stop_data
docker volume inspect one-stop-job_job_one_stop_data
```

备份数据库示例：

```bash
docker compose exec app python - <<'PY'
from pathlib import Path
source = Path("/data/job_one_stop.sqlite3")
target = Path("/data/job_one_stop.backup.sqlite3")
target.write_bytes(source.read_bytes())
print(target)
PY
```

## 新人接手路径

使用者从这里开始：

1. 先读 `README.md` 和 `QUICKSTART.md`，默认走本地开发模式。
2. 本地启动后打开 `http://127.0.0.1:5173/`；Docker 模式打开 `http://127.0.0.1:8000/`。
3. 如果 Docker 启动失败，先跑 `scripts/deploy_check.sh` 或双击 `run_deploy_check.bat`。
4. 首次进入系统时阅读“使用指南”弹窗；之后可用顶栏信息按钮重新打开。
5. 系统配置页确认个人画像、OpenCLI 命令、beBee/公众号配置。
6. 按 `docs/maintenance-guide.md` 的“日常使用路径”推进求职闭环。

维护者从这里开始：

1. `docs/handoff.md`
2. `docs/data-flow.md`
3. `docs/scoring-audit.md`
4. `docs/testing-system.md`
5. `docs/maintenance-guide.md`
6. `docs/project-audit.md`

## 测试和压测

提交前：

```bash
scripts/quality_gate.sh
```

部署前/排障轻量检查：

```bash
scripts/deploy_check.sh
```

系统冒烟：

```bash
scripts/system_smoke.sh
```

本地压力冒烟：

```bash
scripts/load_smoke.sh
```

可调参数：

```bash
JOBS=1000 CONCURRENCY=12 scripts/load_smoke.sh
```

压力冒烟只使用临时 SQLite 和本地 Uvicorn，不读写真实数据，不访问真实招聘平台。

## 外部平台速率

- 不并发跑 BOSS/智联/beBee/公众号采集。
- BOSS/智联由宿主机脚本人工触发；脚本有本地锁，避免重复双击并发。
- beBee/公众号使用 `config.yaml` 里的 `rate_limit_seconds`。
- 真实平台采集不进入自动测试；自动测试只使用 fixture、临时数据库和本地 HTTP。
