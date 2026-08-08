# one-stop-job

本地优先的个人求职助手：把拿不准的事情或岗位材料放进聊天，先按个人规则判断，再继续管理岗位、公司证据、跟进和面试准备。

> 隐私边界：公开仓库只包含程序、空白默认画像和合成测试数据。真实 `.env`、SQLite、个人上下文仓库、聊天记录、岗位卡和截图不得提交。详见 [PRIVACY.md](PRIVACY.md)。

## ✨ 核心功能

- **决策聊天**：通用聊天 + 岗位专属聊天，可重命名；支持文字、链接旁正文及选择/粘贴截图，先运行硬规则，再由可选 AI 给出优先级、风险、唯一下一步和回复草稿
- **岗位池管理**：多来源采集（BOSS/智联/公众号/beBee/CSV）、跨来源去重、状态流转
- **公司调研**：沉淀官网、招聘页、小红书、脉脉、看准等证据
- **智能评分**：按岗位匹配、薪资、成长性、稳定性、口碑等维度输出 100 分解释
- **面试准备**：生成 JD 摘要、技能差距、优势话术、STAR 素材、反问问题；配置 AI 后按 JD + 个人画像定制打招呼语、简历重排与反问（不配 AI 回退模板）
- **跟进任务 & 提醒**：把投递、沟通、调研动作沉淀为本地任务；fit/面试中久无进展的岗位自动标记「需跟进」

## 🚀 快速开始

**单进程部署（推荐，日常使用，Linux/WSL/macOS）：**

```bash
scripts/app.sh start
```

首次运行会自动装依赖、建虚拟环境、构建前端，然后启动一个后端进程同时提供页面与 API。访问 `http://127.0.0.1:8000/`；查看状态用 `scripts/app.sh status`，停止用 `scripts/app.sh stop`。

**备用（Windows 无 WSL 环境时）：** 用 Docker 一键脚本，先启动 Docker Desktop，双击 `start_app.bat`（首次或代码更新后用 `rebuild_app.bat`）。

**详细说明（含本地开发热更新模式、Docker 命令行方式）：** 见 [QUICKSTART.md](QUICKSTART.md)

## 📖 主要文档

| 文档 | 说明 |
|------|------|
| [QUICKSTART.md](QUICKSTART.md) | **快速开始指南**（本地/Docker/Windows/Linux） |
| [CLAUDE.md](CLAUDE.md) | **项目架构标准**（AI 与人类共同遵守） |
| [docs/maintenance-guide.md](docs/maintenance-guide.md) | 日常使用流程、维护入口、故障定位 |
| [docs/operations.md](docs/operations.md) | 运行部署（单进程/本地开发/Docker）、数据备份、运行排障 |

<details>
<summary>开发者文档</summary>

- [docs/data-flow.md](docs/data-flow.md) - 数据流架构
- [docs/testing-system.md](docs/testing-system.md) - 测试体系
- [docs/handoff.md](docs/handoff.md) - 项目交接清单

</details>

## 🎯 核心设计原则

1. **本地优先**：单用户、SQLite、无账号体系、数据不出本机
2. **不自动化对外动作**：不自动投递、不自动发消息、联系方式仅本地留存
3. **来源解耦**：统一数据管线，新增来源不影响评分/面试准备逻辑
4. **AI 可选**：决策聊天、公众号抽取、面试准备可接 AI；聊天先执行本地规则，调用失败时保留规则判断
5. **合规抓取**：仅公开内容、低频、人工触发、不破解验证码、不二次分发
6. **KISS 优先**：聊天保持主入口，低频求职管理能力按需展开；能复用就不新增页面、服务和依赖

## 🔌 数据来源

所有来源最终汇入同一条管线（采集器 → 规范化 → 入库 → 评分），互不耦合。

| 来源 | 触发方式 | 说明 |
|------|---------|------|
| **BOSS 直聘** | 宿主机脚本 | 需安装 OpenCLI，Windows 双击 `tools\host_collect_boss.bat` |
| **智联招聘** | 宿主机脚本 | 需安装 OpenCLI，默认禁用，配置后启用 |
| **公众号** | 粘贴导入 | 粘贴元宝回答或 mp.weixin 链接，自动拆分多岗位 |
| **beBee** | 配置采集 | 解析页面 JobPosting JSON-LD 或 Next.js payload |
| **CSV/Excel** | 文件上传 | 支持自定义字段映射 |
| **手动录入** | 表单填写 | 单条岗位快速录入 |
| **Telegram 截图/文本** | 手机发消息 | 见下方「手机一键入库」；先写聊天候选，本人确认后才入库 |

### 📱 手机一键入库（Telegram，可选）

离开电脑时，用 Telegram 把岗位链接或截图发给自己的 bot。后端**只抽取候选并写入本地聊天**，**默认不入库**；你在 Web「聊天」里勾选要沉淀的岗位再点「入库选中」。这是「手机发过来 → 自动处理 → 本人确认后落盘」的入口。

**为什么用 Telegram 长轮询**：后端主动向 `api.telegram.org` 拉取消息，**不需要对外暴露端口、不需要内网穿透**——`127.0.0.1` 本机运行即可。

**链接不是唯一事实源**：很多平台（BOSS 等）有风控/付费墙、抓不到公开页。所以 ingest 同时支持三种输入——
- **认识的链接**（公众号 `mp.weixin` / beBee）→ 走专用采集器抓取解析；
- **复制的文本**（如手动复制的 BOSS JD）→ 走 LLM 抽取；
- **一张截图** → 走 LLM 视觉抽取。
三者可任意组合。原文与截图会保留在对应聊天线程里（不因「不入库」而删除）。freeform 抽出的岗位 `Job.source` 用 `ingest.manual_source`（默认 `manual`），仅在你确认入库后才写入 Job 表。

**启用步骤**：
1. 在 Telegram 找 `@BotFather` 创建一个 bot，拿到 token。
2. `.env` 加：
   ```
   TELEGRAM_BOT_TOKEN=你的token
   ```
3. 给你的 bot 发一条消息，用 `@userinfobot` 查到**你本人**的 chat id，填进 `config.yaml`：
   ```yaml
   telegram:
     enabled: true
     allowed_chat_id: 123456789   # 你本人的 chat id（整数）
     poll_timeout_seconds: 30
   ```
4. 重启后端。手机发链接/截图后 bot 回执「识别到 N 个候选…打开 Web 确认」；在聊天线程里勾选入库。

> **隐私与边界（红线 §2）**：bot 只处理 `allowed_chat_id`（你本人）的消息，陌生人发来的会被静默忽略。回执**只发给你本人**；**绝不向招聘方发消息**。开启 AI 抽取后，文本和截图会发送给你配置的模型服务商。

也可以不配 Telegram，直接 `POST /api/ingest`（body: `{"text": "…"}` 和/或 `{"image_data_url": "data:..."}`）写入聊天候选，再在 Web 确认入库。

## ⚙️ 配置说明

### 基础配置

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
  data_dir: "./data/job_one_stop"

scoring:
  weights:
    role_match: 25
    salary_city: 15
    growth: 15
    stability: 15
    reputation: 10
    commute_rest: 10
    interview_roi: 10

followup:
  stale_days: 5   # fit/面试中超过该天数无活动 → 标记「需跟进」
```

### 个人操作仓库（只读 + 一键写回收集箱）

如需让应用读取独立维护的个人画像、求职规则、岗位看板和岗位卡，在 `.env` 配置当前运行环境可识别的绝对路径：

```bash
# Windows
JOB_ONE_STOP_CONTEXT_REPO_PATH=D:\path\to\personal-context

# WSL（与 Windows 示例二选一）
# JOB_ONE_STOP_CONTEXT_REPO_PATH=/mnt/d/path/to/personal-context
```

启动后访问 `GET /api/context/status` 检查只读连接。接口只返回白名单文件是否可用，不返回绝对路径、文件数量、更新时间或 Markdown 正文。决策聊天会读取规则、画像和看板；聊天里确认入库的候选岗位，还可以在候选卡上点一次「写入看板」——点击前先看到将要写入的那一行预览，点击后把这一行追加到看板（Obsidian Kanban 看板文件）的「收集箱」列，只新增这一行，不改写既有卡片。**不点「写入看板」就不会写入一个字节**；岗位状态本身仍由本人在 Obsidian 里拖动看板卡片决定，应用不会替你移动卡片。

### AI 配置（可选）

用于决策聊天、公众号岗位抽取和面试准备。推荐在设置页配置，也可以手动改 `.env`。

**方式一（推荐）：设置页 → AI → Provider 卡**

打开「设置 → AI」，点「添加 Provider」，在弹窗里填名称（可选）、Base URL、Model、API Key，点「保存」。Key 只写入本机 `.env`，**单进程部署模式下即时生效，无需重启**；界面全程不回显已保存的 Key，卡片只显示「已配置/未配置」徽标。国内可用示例（阿里百炼 Qwen，兼容 OpenAI 协议）：Base URL 填 `https://dashscope.aliyuncs.com/compatible-mode/v1`，视觉任务用 `qwen-vl-max`、纯文本任务用 `qwen-plus`，Key 从阿里云 DashScope 控制台获取。

多张 Provider 卡按顺序尝试，前一个调用失败会先退避重试几次，仍失败才换下一个；全部失败才会走既有的规则/模板降级。同一个 Key 服务多张卡时，弹窗里「这次填写的 Key 也同时写入其它 Provider」可以一次写多个 `.env` 变量。

最后在「设置 → AI」勾选「启用 AI 兜底」（对应 `config.yaml` 的 `ai.enabled: true`）。

**方式二（手动）：直接编辑 `.env`**

不想用设置页时，也可以自己在项目根目录 `.env` 里加：

```bash
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1  # 可选
```

这是不配置任何 Provider 卡时的兜底环境变量；`config.yaml` 只保存 `ai.enabled` 和每张 Provider 卡的 `label`/`api_key_env`/`base_url`/`model`，**从不保存 Key 本身**。

---

启用后，「决策聊天」会把本次材料、最近对话、岗位事实和白名单个人上下文发送给配置的模型服务商；截图分析要求模型支持视觉输入。模型结果不能覆盖规则引擎发现的硬性失败。「面试准备」仍可按岗位 JD 和个人画像定制材料。

不配置 AI 不影响主流程：聊天保留规则判断，公众号回退到纯正则解析，面试准备回退到模板生成。仅粘贴一个登录态或受限网页链接时，聊天不会假装已经读取页面，而会要求补充正文或截图。

### 聊天数据与截图

- 会话、规则运行结果和附件元数据保存在本地 SQLite。
- 截图原文件保存在 `data/job_one_stop/chat_attachments/`（或 `general.data_dir` 对应目录），不会写入 Git；单张前端限制 4 MB，仅接受 PNG、JPEG、WebP。
- Archive JSON 会包含聊天文字和分析结果，但不嵌入截图文件。导出文件仍可能包含个人信息，不能直接发布。

## 🧪 测试与质量

```bash
# 完整质量门禁（提交前必跑）
scripts/quality_gate.sh

# 或 Windows 双击
run_quality_check.bat

# 单独运行后端测试
.venv/bin/python -m pytest -q

# 部署前自检
scripts/deploy_check.sh
```

## 🔧 常见问题

**Q: Docker 构建太慢或 `Read timed out`？**

A: 推荐使用本地开发模式（5-10 秒启动）。如需 Docker，在 `.env` 配置镜像源。详见 [docs/docker-optimization.md](docs/docker-optimization.md)。

**Q: `No module named uvicorn` 或 `.venv/bin/activate` 不存在？**

A: 不要用系统 Python 启动后端，也不要 `source .venv/bin/python`。直接运行 `.venv/bin/python -m uvicorn ...`。如果 `.venv` 缺少 `bin/activate`，执行 `python3 -m venv --clear .venv && .venv/bin/python -m pip install -r requirements.txt` 重建虚拟环境。

**Q: database is locked？**

A: 不要同时运行本地开发和 Docker。选择一种方式运行。

**Q: BOSS 采集失败？**

A: 确保宿主机已安装 OpenCLI 且浏览器登录态有效。见 [QUICKSTART.md](QUICKSTART.md)。

**Q: 公众号抓取被拦截？**

A: 被风控时会跳过并记录原因，可用「手动粘正文」方式导入。

更多问题见 [QUICKSTART.md](QUICKSTART.md) 和 [docs/operations.md](docs/operations.md)。

## 📊 求职冲刺流程

1. **配置个人画像**：在「匹配评分」页填写目标岗位、技能、工作经历
2. **导入岗位**：通过 BOSS/公众号/CSV 等方式收集岗位
3. **生成冲刺包**：点顶栏「生成今日求职冲刺包」，系统自动补评分、筛 Top 岗位、生成面试准备
4. **执行行动**：复制 Markdown 清单，调研 Top 5，决定投递/拒绝/归档
5. **跟进收口**：冲刺包与「跟进任务」页会列出「需跟进」岗位（fit/面试中超 `stale_days` 天无进展），及时联系或更新状态

详细执行节奏见 [docs/12-hour-sprint-playbook.md](docs/12-hour-sprint-playbook.md)。

## 🎨 技术栈

- **后端**：FastAPI + SQLModel + SQLite + Alembic
- **前端**：React + Vite + TypeScript
- **AI**：OpenAI 兼容协议（可选）
- **采集**：httpx + BeautifulSoup + OpenCLI（宿主机）
- **部署**：Docker Compose（可选）

## 🤝 贡献指南

1. 阅读 [CLAUDE.md](CLAUDE.md) 了解架构红线
2. 新增功能前先跑 `scripts/quality_gate.sh`
3. 测试不得联网，使用 fixtures
4. 提交前确保质量门禁全绿

## 📄 许可

MIT

## 🔗 相关资源

- [OpenCLI](https://github.com/KeJunMao/openreader) - 多平台招聘信息采集工具
- [腾讯元宝](https://yuanbao.tencent.com/) - 公众号语料搜索
- [beBee](https://bebee.com/) - 国际招聘平台

---

**注意**：本项目仅供个人本地使用，不自动投递、不自动发消息。抓取遵循合规原则：仅公开内容、低频、人工触发、不二次分发。
