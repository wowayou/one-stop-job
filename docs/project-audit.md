# Project Audit

审计日期：2026-06-10

## 收敛结论

当前项目主线已经收敛为一个本地优先、单用户、SQLite 持久化的 Web 求职助手。核心闭环是：

```text
岗位来源 -> normalizer -> importer/upsert -> Job/Company/JobSourceLink
       -> 公司调研 -> 个人画像评分 -> 面试准备/准备素材 -> 跟进任务 -> 今日冲刺包
```

项目不做账号体系，不自动投递，不自动发送消息，不把本地数据上传到云端。

## 入口文件

| 类型 | 文件 | 状态 |
|---|---|---|
| 本地运行 | `README.md` / `QUICKSTART.md` / `scripts/dev_wsl.sh` | 本地开发优先，后端 `http://127.0.0.1:8000/`，前端 `http://127.0.0.1:5173/` |
| Docker 运行 | `docker-compose.yml` / `start_app.bat` / `rebuild_app.bat` / `status_app.bat` / `stop_app.bat` | Windows 一键运行和部署入口，前后端统一 `http://127.0.0.1:8000/` |
| 日志查看 | `run_backend.bat` / `run_frontend.bat` | 跟随 Docker app 容器日志，不再单独启动 Python/Vite |
| 宿主机采集 | `tools/host_collect_boss.bat` / `tools/host_collect_zhilian.bat` / `tools/host_opencli_import.py` | BOSS/智联在宿主机运行 OpenCLI，CSV 自动导入主服务 |
| WSL 开发运行器 | `scripts/dev_wsl.sh` | 管理本地后端、前端、PID 和日志 |
| 部署自检 | `run_deploy_check.bat` / `scripts/deploy_check.sh` | 不依赖 `.venv`/`node_modules`，检查配置、Compose 和运行中服务探针 |
| 质量门禁 | `run_quality_check.bat` / `scripts/quality_gate.sh` | 提交前必跑 |
| 系统冒烟 | `scripts/system_smoke.sh` | 启动真实后端，用临时 SQLite 跑业务闭环 |
| 压力冒烟 | `scripts/load_smoke.sh` | 临时 SQLite，覆盖批量导入、并发评分和冲刺包耗时预算 |
| 聊天压测 | `scripts/chat_stress.sh` | 临时 SQLite，覆盖长线程退化、并发写、边界输入和追问锚点正确性 |
| 运维交接 | `docs/operations.md` | 数据位置、备份、Windows/WSL Docker 和新人接手路径 |

## 删除与清理

本轮删除：

- `job_monitor.py`
- `run_job_monitor.bat`

原因：

- 文件已乱码且 `py_compile` 报 `SyntaxError`，不能作为可靠入口。
- 功能与当前 Web 主线重复，且绕过当前 `normalizer -> importer -> SQLite` 主干。
- README 仍提示旧脚本可运行，会增加新用户上手成本。

同步清理：

- `config.yaml` 移除旧邮件、Excel 备份、旧日志配置，只保留当前后端实际读取的配置项。
- `.env.template` 从邮箱授权码模板改为 OpenAI 与运行覆盖项模板。
- `.gitignore` 补充 `.sqlite`、覆盖率和浏览器测试报告等本地产物。

未删除：

- `requirements-automation.txt`：元宝 Playwright 自动化仍是明确的可选能力。
- `docs/12-hour-sprint-playbook.md`：仍用于求职冲刺、作品集和 AI roadmap。
- `tests/fixtures/*`：离线解析测试样例，属于测试资产，不是用户数据。
- `data/`、`.env`、SQLite、日志：属于本地运行产物，不进入仓库，也不在收敛中删除。

## 功能闭环状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 岗位池 | 已闭环 | 新增、CSV/XLSX 导入、BOSS/智联宿主机采集导入、公众号/beBee 采集入口、跨来源去重、状态回退、收藏、分页 |
| 公司调研 | 已闭环 | 公司列表、证据新增、风险/备注更新、从公司打开岗位 |
| 匹配评分 | 已闭环 | 个人画像、评分生成、硬阻断、排序队列、冲刺包复用 |
| 面试准备 | 已闭环 | 基于个人真实经历生成核心优势话术、沟通草稿、对应简历和准备材料，队列内部滚动 |
| 跟进任务 | 已闭环 | 新增、关联岗位、截止日期、完成/重开、删除、打开关联岗位 |
| 今日冲刺包 | 已闭环 | 补评分、生成准备、创建跟进任务、输出 Markdown |
| AI 状态 | 已闭环 | 只展示配置状态，不返回密钥或 Base URL 明文 |
| 系统配置 | 已闭环 | AI 示例弹窗、来源配置、评分权重合计提示；后端拒绝非法权重和敏感字段写入；坏 YAML 会在配置页和诊断接口显示且可保存修复 |
| 启停脚本 | 已闭环 | Docker Compose 后台启动、强制重建、状态查看、停止；失败时自动 5 秒关闭，不再无限等待按键 |
| 部署诊断 | 已闭环 | `/api/health` 轻量探活，`/api/ready` 和 `/api/diagnostics/deployment` 输出数据库、配置、构建、来源和云端运行参数检查 |
| 使用指南 | 已闭环 | 首次进入自动展示一次使用指南弹窗，可一键启动聚光灯引导（高亮顶栏/导航/指标并浮出说明气泡）；顶栏信息按钮随时重开引导；维护文档沉淀在 `docs/maintenance-guide.md` |
| 前端健壮性 | 已闭环 | 顶层 `ErrorBoundary` 兜底渲染异常不白屏；`loadAll` 用 `allSettled`，单个接口失败只跳过对应区块并提示，其余仍可用；复制统一走 `copyToClipboard`（`navigator.clipboard` 失败回退 `execCommand`） |

## 测试方案

提交前默认跑：

```bash
scripts/quality_gate.sh
```

门禁包含：

- Shell 脚本语法检查，包含部署自检脚本。
- 后端 pytest：解析器、API、导入去重、评分、采集 fixture、配置校验、统一错误响应、部署诊断和上传大小限制。
- 前端 TypeScript + Vite 生产构建。
- 系统 HTTP 冒烟：真实 Uvicorn + 临时 SQLite，覆盖核心业务闭环。
- Alembic 旧库迁移烟测。

性能/容量回归可选跑：

```bash
scripts/load_smoke.sh
JOBS=1000 CONCURRENCY=12 scripts/load_smoke.sh

scripts/chat_stress.sh                              # 聊天 / ingest 面
ROUNDS=300 CONCURRENCY=16 scripts/chat_stress.sh
```

两个脚本都不访问真实招聘平台，不读写真实数据。

单独定位问题时：

```bash
.venv/bin/python -m pytest -q
cd frontend && npm run build
scripts/system_smoke.sh
.venv/bin/python -m alembic -c backend/alembic.ini upgrade head
```

浏览器手动验收仍按 `docs/testing-system.md` 的手动冒烟清单执行，尤其关注岗位抽屉关闭、分页、内部滚动和跟进任务操作。

## 剩余风险

1. beBee 真实页面结构可能继续变化。
   - 当前策略：依次解析 `JobPosting` JSON-LD、Next/RSC `jobs:[{...}]`、microdata 和可见卡片；失败返回 success + 0 jobs + skipped 原因。
   - 后续若岗位来自外部 XHR/JSON 或字段名漂移，需拿真实 HTML/Network 样例后补解析。

2. 公众号真实链接可能触发风控或图片型文章。
   - 当前策略：自动抓取失败时支持手动粘正文。
   - 测试策略：联网抓取不进自动测试，只用 fixture 和手动正文冒烟。

3. 前端还没有 Playwright E2E。
   - 当前策略：`npm run build` + 手动冒烟；顶层 `ErrorBoundary` 兜底渲染异常不白屏，`loadAll` 用 `allSettled` 让单接口失败不空屏。
   - 后续 UI 稳定后补 Playwright，覆盖抽屉、分页、任务、冲刺包和聚光灯引导。

4. URL 不随单页导航变化。
   - 当前策略：保持轻量，不引路由。
   - 后续需要深链时再加 `react-router-dom` 或 `history.pushState`。

5. 评分仍是关键词规则。
   - 当前策略：确定性评分优先，AI 只辅助解释和抽取。
   - 后续可加本地同义词表和画像变更后的批量重算。
