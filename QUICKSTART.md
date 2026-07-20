# 快速开始

> 推荐使用**本地开发模式**，启动只需 5-10 秒。Docker 作为可选的一键部署方案。

---

## 🚀 方式一：本地开发（推荐，最快）

### 前置条件

- Python 3.10+
- Node.js 18+

### 首次安装（约 2-3 分钟）

**在 WSL/Linux/macOS：**

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 2. 前端依赖
cd frontend && npm install && cd ..

# 3. 复制配置文件
cp .env.template .env

# （可选）编辑 .env 配置 AI 功能
# nano .env
```

**在 Windows（PowerShell）：**

```powershell
# 1. 创建虚拟环境
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 前端依赖
cd frontend
npm install
cd ..

# 3. 复制配置
copy .env.template .env
```

### 日常启动（5-10 秒）

**终端 1 - 启动后端：**

```bash
# WSL/Linux/macOS
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

# Windows
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

**终端 2 - 启动前端：**

```bash
cd frontend
npm run dev
```

**访问：** `http://127.0.0.1:5173`

### 停止

按 `Ctrl+C` 停止两个终端即可。

---

## 🐳 方式二：Docker Compose（可选，适合部署）

### 前置条件

- Docker Desktop（Windows）或 Docker Engine（Linux）

### 首次构建（约 3-5 分钟）

**重要**：首次构建前配置镜像源，避免超时：

```bash
# 1. 复制配置模板
cp .env.template .env

# 2. 确认 .env 中已配置（默认已配置阿里云镜像）
# PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
# NPM_REGISTRY=https://registry.npmmirror.com

# 3. 构建并启动
docker compose up -d --build
```

**如果遇到 `Read timed out` 错误**：

编辑 `.env`，尝试切换镜像源：

```bash
# 方案 1: 清华镜像
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 方案 2: 官方源（需要稳定国际网络）
PIP_INDEX_URL=https://pypi.org/simple
```

然后重新构建：

```bash
docker compose up -d --build
```

**详细构建优化**：见 [docs/docker-optimization.md](docs/docker-optimization.md)

### 日常启动（秒级）

**Linux/macOS/WSL：**

```bash
docker compose up -d
```

**Windows：** 双击 `start_app.bat`

**访问：** `http://127.0.0.1:8000`

### 停止

```bash
docker compose down

# 或 Windows 双击：stop_app.bat
```

### 查看日志

```bash
docker compose logs -f

# 或 Windows 双击：run_backend.bat
```

---

## 📋 配置说明

### 基础配置（必需）

编辑 `config.yaml`：

```yaml
opencli:
  boss_cmd:
    - "opencli"
    - "boss"
    - "search"
    - "示例岗位" # 使用前仅在本机替换
    - "--city"
    - "示例市"
    - "--salary"
    - "8-20k"
    - "--limit"
    - "200"
    - "--format"
    - "csv"

general:
  data_dir: "./data/job_one_stop"  # 数据库目录
```

### AI 配置（可选）

编辑 `.env`：

```bash
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1  # 可选
OPENAI_MODEL=gpt-4o-mini                     # 可选
```

然后在 `config.yaml` 启用：

```yaml
ai:
  enabled: true
```

---

## 🔧 环境选择建议

| 场景 | 推荐方式 | 原因 |
|------|---------|------|
| **日常开发/调试** | 本地开发 | 启动快、热更新、方便调试 |
| **初次试用** | 本地开发 | 无需等待 Docker 构建 |
| **多人协作/部署** | Docker | 环境一致、一键启动 |
| **云端部署** | Docker | 标准化、易于管理 |

---

## ❓ 常见问题

### Q1: WSL 中 `python3: command not found`

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

### Q2: Windows 提示 "cannot be loaded because running scripts is disabled"

本指南不要求激活虚拟环境，优先使用 `.\.venv\Scripts\python.exe -m ...`。如果你仍想执行 `Activate.ps1`，再按需运行：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Q3: `npm install` 很慢

```bash
# 切换淘宝镜像
npm config set registry https://registry.npmmirror.com
```

### Q4: Docker 构建超时

```bash
# 检查 Docker 状态
scripts/docker_doctor.sh

# 使用国内镜像源
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple docker compose up -d --build
```

### Q5: 数据库被锁 "database is locked"

不要同时运行本地开发和 Docker。选择其中一种方式运行。

### Q6: 本地开发和 Docker 的数据互通吗？

默认不互通。本地开发使用 `./data/job_one_stop/job_one_stop.sqlite3`，Docker 使用 volume `job_one_stop_data` 里的 `/data/job_one_stop.sqlite3`。这样可以避免两个后端同时写同一个 SQLite 导致锁库。

### Q7: `No module named uvicorn`

说明你正在用系统 Python，而不是项目虚拟环境。使用：

```bash
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

如果 `.venv/bin/activate` 缺失或虚拟环境异常，直接重建：

```bash
python3 -m venv --clear .venv
.venv/bin/python -m pip install -r requirements.txt
```

不要运行 `source .venv/bin/python`，`python` 是二进制程序，不是 shell 脚本。

---

## 📚 下一步

1. **首次使用：** 打开应用后会自动展示"使用指南"
2. **配置个人画像：** 在「匹配评分」页面填写目标岗位、技能等
3. **导入岗位：** 使用 CSV 导入、公众号粘贴或配置 BOSS 采集
4. **生成冲刺包：** 点击顶栏「生成今日求职冲刺包」

**详细使用流程：** 见 [docs/maintenance-guide.md](docs/maintenance-guide.md)
