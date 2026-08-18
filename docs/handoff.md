# Handoff: job-one-stop

交接日期：2026-07-20（最后校准：2026-08-08）

## 当前状态

`job-one-stop` 是本地优先的个人求职助手：岗位采集/导入、公司调研、个人画像评分、面试准备、跟进任务和求职冲刺包都在本机运行。项目保持单用户、本地 SQLite、不自动投递、不自动发消息。

个人决策 / 求职聊天助手能力已从 Phase 0（只读）推进到 Phase 2（受控写回）：

- `backend/app/services/context_repository.py`：只读访问独立个人操作仓库的固定白名单 Markdown；`GET /api/context/status` 不返回宿主机绝对路径或正文。
- 决策聊天已落地：`ChatThread` / `ChatMessage` 持久化，规则先跑、可选 AI（OpenAI 兼容，多 provider 容错）给优先级/风险/下一步/草稿。
- **统一 ingest 入口**：文本/链接/截图 → 候选（**默认不写 Job 表**），用户在 Web 聊天勾选 commit 才入库；相册/引用回复的多图会合并到同一岗位（见 CLAUDE.md §8）。Telegram 长轮询为可选出站传输层，回执只发机主本人。
- **Phase 2 写回已实现（唯一写入通道）**：本人在已入库候选卡点「写入看板」，`ContextWriter`（`services/board_write.py`）在白名单 `board` 文件「收集箱」列插入一行，点击前原样预览；除此之外无任何写入路径（AST 绊线锁定）。详见 CLAUDE.md 顶部 Phase 2 边界与红线 §10。

本轮能力已经扩到“可交付”层：

- 个人画像、岗位池、公司调研、面试准备、待办、面试复盘、冲刺包和完整归档都可直接导出。
- 岗位抽屉支持记录投递事件（已投递 / 已回复 / 约面 / Offer / 拒绝 / 撤回），前端会汇总求职漏斗和画像提醒。

运行口径已收敛为三档（与 README / QUICKSTART / CLAUDE.md §6 / docs/operations.md 一致）：

- **日常使用（非改代码）：单进程部署 `scripts/app.sh start`**——构建一次 `frontend/dist` 后只跑一个 uvicorn 进程（:8000，前端由后端静态托管）。`status`/`logs`/`stop`/`update` 见脚本。
- **改代码/调试：本地开发热更新**——后端 Uvicorn `:8000` + 前端 Vite `:5173`。
- **备用：Docker Compose**——仅 Windows 无 WSL 一键运行（`start_app.bat` 等调用 Docker Compose）。
- 单进程部署与本地开发共用 `./data/job_one_stop/` 数据库、同监听 :8000，**不能同时启动**；Docker 用独立 volume `job_one_stop_data`，与前两者不互通。
- `config.yaml` 是**本地 gitignored 文件**，由 `config.example.yaml` 生成（`app.sh` 首启与 Dockerfile 都会 ensure）；密钥只进 `.env`。AI Provider/Key 走「设置 → AI」弹窗管理，Key 只写 `.env`，单进程部署同进程内即时生效。

BOSS / 智联已改为宿主机采集导入：

- Docker 模式下 `JOB_ONE_STOP_OPENCLI_SERVER_ENABLED=false`，服务端不调用 OpenCLI。
- `tools/host_collect_boss.bat` / `tools/host_collect_zhilian.bat` 在宿主机运行 OpenCLI。
- `tools/host_opencli_import.py` 从 `/api/sources` 读取命令，执行后把 CSV 转 UTF-8 并 POST 到 `/api/jobs/import`。
- 脚本有临时锁，避免重复双击或并发跑多个平台。

## 关键文件

- `README.md` / `QUICKSTART.md`：本地优先的运行入口。
- `Dockerfile` / `docker-compose.yml`：Docker 部署入口。
- `requirements-runtime.txt`：运行时依赖集合（Dockerfile 和本地开发共用）；`requirements.txt` 在此基础上追加本地测试依赖。
- `scripts/docker_app.ps1`：Windows bat 的 Docker Compose helper，处理普通路径和 WSL UNC 路径。
- `backend/app/main.py`：FastAPI 路由、采集生命周期、评分/准备/冲刺包接口、前端静态托管。
- `backend/app/services/sources.py`：通用来源状态；容器模式下 OpenCLI 来源返回 `host_import_required`。
- `tools/host_opencli_import.py`：宿主机 OpenCLI 采集并导入。
- `frontend/src/App.tsx`：主 UI、通知条、采集按钮、冲刺包弹窗、配置页。
- `scripts/quality_gate.sh`：质量门禁。
- `scripts/system_smoke.sh`：隔离数据库的真实 HTTP 系统冒烟。
- `docs/testing-system.md`：测试分层、门禁和手动冒烟清单。
- `docs/operations.md`：运行、数据位置、备份、Windows/WSL Docker 和接手路径。
- `docs/maintenance-guide.md`：日常使用闭环、维护入口、故障定位和变更红线。
- `docs/data-flow.md` / `docs/scoring-audit.md`：数据流图、评分排序审计。
- `docs/project-audit.md`：项目结构、入口、冗余清理和风险收敛记录。
- `docs/12-hour-sprint-playbook.md`：求职冲刺、持续运营、作品集和 AI roadmap。

## 验证状态

最近一次验证：

```bash
scripts/quality_gate.sh
# 198 passed（2026-08-08）；前端 Vite build、system_smoke、Alembic 旧库迁移烟测通过
```

本轮没有跑真实 BOSS/智联/beBee 采集，也没有并发请求外部平台。

## 运行方式

日常使用（单进程部署，推荐）：

```bash
scripts/app.sh start      # 首次自动建 venv/装依赖/构建前端
scripts/app.sh status
scripts/app.sh stop
```

访问 `http://127.0.0.1:8000/`。

改代码/调试（本地开发热更新）：

```bash
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

访问 `http://127.0.0.1:5173/`。

备用（Docker）：

```bash
docker compose up -d --build   # 访问 :8000
docker compose down
```

单进程部署与本地开发共用同一份 SQLite、同端口，不要同时启动；也不要让 Docker 容器和它们混用同一份数据。

## 维护红线

- 不新增登录/多用户体系。
- 不自动投递、不自动发消息、不自动外发联系方式。
- 新来源必须走 `Collector -> normalizer -> importer -> SQLite`，不得直接写 `Job`。
- 不允许绕过导入管线直接批量写表，也不允许在后台静默清理岗位。
- 抓取失败或解析失败必须写入 `report.skipped`，不能静默返回空。
- BOSS/智联等平台采集保持人工触发、低频执行，不并发跑多个平台。
- 导出只允许生成本地文件，不能自动上传云端、发邮件或写外部 SaaS。
- 导出和 API 返回都不能泄露 `.env`、API Key、宿主机绝对路径、浏览器登录态目录。
- 不要提交 `.env`、SQLite、日志、Excel、登录态目录。
- 现有表加字段必须写 Alembic 迁移，不能假设 `create_all` 会升级旧库。

## 当前已知问题与下一步

1. beBee 真实页面结构仍可能变化。
   - 现状：后端依次解析 `JobPosting` JSON-LD、Next/RSC jobs、microdata 和可见卡片；前端会显示 warning 和 skip 原因。
   - 下一步：如果岗位来自外部 XHR/JSON 或字段名漂移，拿真实 HTML/Network 样例后再补解析，不要盲写选择器。

2. 前端还是单页状态切换，URL 不随导航变化。
   - 现状：保持轻量实现。
   - 下一步：如需要深链，再引入 `react-router-dom` 或手写 `history.pushState`。

3. Docker 镜像构建没有纳入自动门禁。
   - 现状：`quality_gate.sh` 不要求本机有 Docker 或网络。
   - 下一步：改 Dockerfile/compose 后手动跑 `docker compose up -d --build` 和 `docker compose down`。

## 给下一位 AI / 维护者的指令

```text
你正在接手维护 <repo-root>。

先读 CLAUDE.md 和 docs/handoff.md，不要先改代码。
然后运行 git status --short，区分已有改动和你新增的改动，不要回滚用户或前任 AI 的修改。

项目红线：
- 本地优先、单用户、SQLite。
- 不自动投递、不自动发消息。
- 新岗位来源必须走 Collector -> normalizer -> importer。
- 抓取/解析失败必须记录 skipped reason。
- 密钥只放 .env，不能进 config.yaml 或前端。
- 默认运行口径：日常用单进程部署 scripts/app.sh，改代码用本地开发热更新，Docker 仅 Windows 无 WSL 备用；BOSS/智联走宿主机采集导入。
- 外部个人仓库读只走 ContextRepository 白名单；写只有「写入看板」经 ContextWriter 插入一行，AST 绊线锁定，不得新增写入路径。

测试要求：
- 后端跑 .venv/bin/python -m pytest -q。
- 前端跑 cd frontend && npm run build。
- 系统冒烟跑 scripts/system_smoke.sh。
- 最终提交前优先跑 scripts/quality_gate.sh。
- 当前环境不要用 fastapi.testclient.TestClient 写新测试；用 httpx.AsyncClient + ASGITransport。

优先级建议：
1. 先处理用户真实点击后的反馈/可解释性问题。
2. 再处理 beBee 真实 HTML/Network 样例解析。
3. 再做 AI 辅助 JD 粘贴解析 / 公司调研摘要等 roadmap 后续项。
4. 最后考虑 URL 路由、npm audit、Playwright E2E 等工程化收敛。
```
