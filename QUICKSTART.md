# 快速开始

> 日常使用优先走单进程部署；改代码和调试时使用本地开发；不想装开发环境就用桌面安装包。
> 前两种默认都用端口 `8000`(本地开发的前端另占 `5173`)且共用同一个数据库,**不要同时启动**。

---

## 🚀 方式一：单进程部署（推荐，日常使用）

适用于 Linux / WSL / macOS。构建一次前端产物后,后端直接挂载它并对外提供完整应用,全程只需要一个进程。

### 前置条件

- Python 3.10+
- Node.js 18+

### 首次启动

```bash
scripts/app.sh start
```

首次运行会自动:创建虚拟环境并安装后端依赖 → 安装前端依赖 → 构建前端产物(`frontend/dist`)→ 启动后端。整个过程约 2-5 分钟,取决于网络。

### 访问

`http://127.0.0.1:8000/`

### 日常操作

```bash
scripts/app.sh start   # 启动(已在运行则提示并跳过)
scripts/app.sh status  # 查看进程与健康检查状态
scripts/app.sh logs    # 跟踪日志(Ctrl+C 退出)
scripts/app.sh stop    # 停止
scripts/app.sh update  # 代码更新后：装依赖 + 重新构建前端 + 若在运行则重启
scripts/app.sh backup  # 备份 SQLite + 聊天附件到 data/backups/<时间戳>/
```

运行时文件(pid、日志)在 `data/app/`,与本地开发模式的 `data/dev/` 互不干扰,可各自独立启停,但**两者共用同一个数据库** `./data/job_one_stop/`,且都监听 `8000` 端口,所以不能同时启动。如果 `scripts/app.sh start` 报端口被占用,先确认没有本地开发后端或桌面版内置后端在跑。

首次配置 AI / Telegram 等可选能力,见 [docs/setup-checklist.md](docs/setup-checklist.md)。

---

## 🛠️ 方式二：本地开发（改代码/调试）

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

WSL/Linux/macOS 也可以用 `scripts/dev_wsl.sh start`(前后端一起起,日志在 `data/dev/`),用法见脚本内 `usage`。

### 停止

按 `Ctrl+C` 停止两个终端即可;用了 `scripts/dev_wsl.sh start` 则运行 `scripts/dev_wsl.sh stop`。

---

## 📦 方式三：桌面安装包（不想装任何开发环境）

前往 [Releases](../../releases) 下载对应平台的安装包（Windows `.msi`/`.exe`、macOS `.dmg`、Linux `.AppImage`）。
装完直接双击运行，后端内置，不需要 Python / Node / WSL。校验与升级说明见 [README](README.md#方式二桌面应用下载无需任何开发环境)。

Windows 上没装 WSL 又不想手搭环境时，走这条路。

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

推荐走设置页：启动后打开 `http://127.0.0.1:8000/` → 设置 → AI → 「添加 Provider」，弹窗里填 Base URL / Model / API Key 后点「保存」——Key 只写本机 `.env`，单进程部署模式下**即时生效，无需重启**；界面全程不回显已保存的 Key。国内可用示例（阿里百炼 Qwen）：Base URL 填 `https://dashscope.aliyuncs.com/compatible-mode/v1`，视觉任务用 `qwen-vl-max`、文本任务用 `qwen-plus`。最后勾选「启用 AI 兜底」保存。

不想用设置页也可以手动编辑 `.env`（不配置任何 Provider 卡时的兜底）：

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

`config.yaml` 只保存 `ai.enabled` 和 provider 的非密钥字段，从不保存 Key 本身；详细步骤见 [docs/setup-checklist.md](docs/setup-checklist.md)。

### 更多可选配置（Telegram、个人上下文仓库等）

见 [docs/setup-checklist.md](docs/setup-checklist.md)。

---

## 🔧 环境选择建议

| 场景 | 推荐方式 | 原因 |
|------|---------|------|
| **日常使用** | 单进程部署 | 一条命令、一个进程、无需常驻两个终端 |
| **日常开发/调试** | 本地开发 | 热更新、方便调试 |
| **初次试用** | 单进程部署 | 一条命令拉起，不必先搭前端 |
| **Windows 无 WSL / 不想装环境** | 桌面安装包 | 双击即用，后端内置 |

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

### Q4: `pip install` 超时

```bash
# 用国内镜像源（pip 原生读 PIP_INDEX_URL）
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple .venv/bin/python -m pip install -r requirements.txt
```

### Q5: 数据库被锁 "database is locked"

同一个 SQLite 库被两个后端同时占用了。`scripts/app.sh`(单进程部署)、本地开发(`--reload` 后端 / `scripts/dev_wsl.sh`)和桌面版内置后端三者只能同时跑一个；前两者共用同一个 SQLite 文件,即使端口不同也不要同时启动。

### Q6: `No module named uvicorn`

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
**待补的个人配置清单：** 见 [docs/setup-checklist.md](docs/setup-checklist.md)
