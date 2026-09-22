# Operations

## 推荐运行方式

运行口径已收敛为三档（与 README / QUICKSTART / CLAUDE.md §6 一致）：**日常使用（非改代码）优先单进程部署 `scripts/app.sh`**；改代码/调试用本地开发热更新模式；不想装开发环境（含 Windows 无 WSL）用 [Releases](../../../releases) 里的桌面安装包。

### 日常使用：单进程部署（推荐）

构建一次 `frontend/dist` 后只跑一个 uvicorn 进程（:8000，前端由后端静态托管），无需另开 Vite：

```bash
scripts/app.sh start     # 首次会自动建 venv、装依赖、构建前端
scripts/app.sh status    # 进程 + 健康检查
scripts/app.sh logs      # 跟踪日志
scripts/app.sh stop
scripts/app.sh update    # 拉取更新并重启
```

访问 `http://127.0.0.1:8000/`。与本地开发模式共用 `./data/job_one_stop/` 数据库，两者不要同时启动（同端口 :8000）。

### 改代码/调试：本地开发热更新模式

启动快、日志直接、前后端分离热更新：

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

### 不装开发环境：桌面安装包

[Releases](../../../releases) 里下载对应平台安装包，后端内置，不需要 Python / Node / WSL。
Windows 无 WSL 时走这条路。校验、升级发现与「设置 → 诊断」见 [README](../README.md)。

> 桌面版内置后端同样监听 :8000 并使用它自己的数据目录，不要与单进程/本地开发模式同时启动。

### 依赖装不上（pip 超时）

`pip` 原生读 `PIP_INDEX_URL`，换国内镜像源即可：

```bash
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple .venv/bin/python -m pip install -r requirements.txt
# 备选：https://pypi.tuna.tsinghua.edu.cn/simple ；官方源 https://pypi.org/simple
```

npm 用它自己的配置：`npm config set registry https://registry.npmmirror.com`。

失败信息是 `ECONNRESET` / `i/o timeout` / `DeadlineExceeded` 时优先处理网络和镜像源，不要先改应用代码。
不要同时配一个很慢的 `PIP_EXTRA_INDEX_URL`——pip 会为依赖元数据访问多个源，把安装拖到几十分钟。

## 健康检查和云端接口

服务提供三个运行状态接口：

- `GET /api/health`：轻量探活。
- `GET /api/ready`：就绪检查；配置解析或数据库连接失败时返回 503，其它可降级项以 `warning` 标记。
- `GET /api/diagnostics/deployment`：完整部署诊断，包含配置文件、数据库、前端构建、CORS、上传上限、AI 和采集来源状态。

可用的运行时环境变量：

| 变量 | 用途 |
|---|---|
| `PORT` | 监听端口，默认 `8000` |
| `HOST` | Uvicorn 监听地址，默认 `0.0.0.0` |
| `DATABASE_URL` | 通用数据库 URL；优先级低于 `JOB_ONE_STOP_DATABASE_URL` |
| `JOB_ONE_STOP_DATABASE_URL` | 应用数据库 URL；不设时按 `general.data_dir` 推导 |
| `JOB_ONE_STOP_CONFIG` | 配置文件路径，默认仓库根目录 `config.yaml` |
| `JOB_ONE_STOP_CORS_ORIGINS` | 逗号分隔的浏览器来源白名单 |
| `JOB_ONE_STOP_MAX_UPLOAD_MB` | CSV/XLSX 上传大小上限，默认 `20` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | AI 兜底能力配置 |

改端口：`PORT=18000 scripts/app.sh start`。

## 数据保存位置

单进程部署 / 本地开发（共用同一份数据）：

- SQLite 数据库：目录来自 `config.yaml general.data_dir`，当前是 `./data/job_one_stop`。
- 运行时文件：单进程模式 `data/app/`（pid、日志、看门狗哨兵），本地开发 `data/dev/`。
- 配置文件：仓库根目录 `config.yaml`；AI 密钥在 `.env`。两者都被 `.gitignore` 排除。
- 前端静态文件：`frontend/dist`，由 FastAPI 托管。
- BOSS/智联 OpenCLI 登录态：宿主机浏览器/OpenCLI 自己管理。

桌面版用它自己的数据目录，与上面两者不互通。

备份走应用自带的入口，它用 SQLite 的在线备份接口，只新建不覆盖：

```bash
scripts/app.sh backup      # 或「设置 → 诊断 → 一键备份」，落到 data/backups/<时间戳>/
```

需要在两种模式间搬数据时，先停掉正在运行的后端再复制 SQLite 文件——不要在运行中直接拷。

## 新人接手路径

使用者从这里开始：

1. 先读 `README.md` 和 `QUICKSTART.md`，默认走单进程部署（`scripts/app.sh start`）。
2. 单进程模式打开 `http://127.0.0.1:8000/`；本地开发模式打开 `http://127.0.0.1:5173/`。
3. 起不来先看 `scripts/app.sh status` 和 `scripts/app.sh logs`，再看「设置 → 诊断」。
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

聊天 / ingest 面的压测（长线程退化、并发写、边界输入、追问锚点正确性）：

```bash
scripts/chat_stress.sh
ROUNDS=300 CONCURRENCY=16 scripts/chat_stress.sh
```

两个压测都只使用临时 SQLite 和本地 Uvicorn，不读写真实数据，不访问真实招聘平台。

## 外部平台速率

- 不并发跑 BOSS/智联/beBee/公众号采集。
- BOSS/智联由宿主机脚本人工触发；脚本有本地锁，避免重复双击并发。
- beBee/公众号使用 `config.yaml` 里的 `rate_limit_seconds`。
- 真实平台采集不进入自动测试；自动测试只使用 fixture、临时数据库和本地 HTTP。
