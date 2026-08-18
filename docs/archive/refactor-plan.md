# 重构方案 · Phase R（结构）+ Phase P（AI 多 provider 容错）

> 目标：把两个巨型文件拆成「后续 Agent 能快速定位、独立修改」的模块，并让 AI 调用在主 key 不稳时自动容错。
> 铁律：**纯搬运/收口，不改任何行为**。169 个测试 + 绊线测试是安全网；每一步做完必须 pytest 全绿 + 前端 tsc/build 干净 + 前端步骤 Playwright 截图自验。
> 每一步都是一个独立可提交、可回滚的单元；中途中断不留半成品。

## 现状（2026-07-23 实测）

| 文件 | 行数 | 问题 |
|---|---|---|
| `backend/app/main.py` | 2676 | 69 个端点 + 128 个顶层函数堆一处；路由、生命周期、Telegram 轮询、ingest 落盘、诊断全混在一起 |
| `frontend/src/App.tsx` | 4781 | ~41 个组件 + 根状态中枢全在一个文件 |
| 候选状态 | — | `metadata_json.candidates` 里 status + job_id + board_written + existing_job_id + duplicate_in_thread_id + source_tg_message_id + edited_from_tg_message_id，**全无类型约束**，是接手者最易踩坑处 |

services/ 层已经分得不错（最大 bebee.py 627 行），本次基本不动；`api.ts`/`types.ts` 已分离，保留。

## 非目标（明确不做，防止重构膨胀成重写）

- 不改数据库表结构、不写迁移（候选仍存 JSON，只加类型）。
- 不改任何 API URL / 请求响应契约（前后端契约冻结）。
- 不改任何业务逻辑、评分算法、红线语义。
- 不新增运行时依赖（容错用现有 openai 客户端）。

## Phase R — 结构拆分

### R1. 后端：抽出共享依赖 + ingest/chat 落盘助手搬家
- 新建 `backend/app/deps.py`：`get_session` / `SessionDep` / settings 访问等被多路由共享的东西。
- 新建 `backend/app/services/chat_ingest.py`：把 `_persist_ingest_to_chat`、`_find_ingest_thread_by_receipt`、`_find_ingest_message_by_tg_id`、`_delete_chat_thread`、候选查重/去重辅助搬进来——因为它们被 chat 路由**和** Telegram 轮询同时依赖，必须有一个两边都能 import 的中立位置。
- **绊线测试同步更新目标模块**：现有 `test_ingest_and_telegram_modules_never_import_importer` / `_persist_ingest_and_poll_loop_write_chat_only` 按新模块路径断言，保证「不自动 upsert」约束一字不松（这正是宪章要求的「推翻要三处同时动」）。
- 出口：pytest 全绿。

### R2. 后端：按域拆 APIRouter
新建 `backend/app/routers/`，每组一个 `APIRouter`，`main.py` 只保留 app 创建 + 中间件 + lifespan + 静态挂载 + `include_router`：

| 路由模块 | 端点组 | 端点数 |
|---|---|---|
| `jobs.py` | /api/jobs（CRUD/import/score/events） | 13 |
| `chat.py` | /api/chat（线程/消息/候选 commit/board-write/restore/batch-delete） | 12 |
| `companies.py` | /api/companies（调研） | 5 |
| `followups.py` | /api/follow-ups | 5 |
| `collect.py` | /api/collect（boss/bebee/wechat/yuanbao）+ /api/ingest | 6 |
| `interviews.py` | /api/interviews | 3 |
| `meta.py` | /api/config、/api/ai、/api/diagnostics、/api/health、/api/context、/api/sources、/api/profile | ~12 |
| `misc.py` | /api/sprint、/api/ready、/api/exports、/api/events、/api/analytics、/api/drafts | ~7 |

- Telegram 轮询循环 `_telegram_poll_loop` + lifespan 留在 `main.py`（或抽 `backend/app/lifespan.py`），import R1 搬好的助手。
- 每个 router 可作为一次子提交，逐个搬、逐个绿。
- **完成后更新 CLAUDE.md §1「关键文件」清单**：main.py 不再是「薄路由」全集，改指向 routers/ + 说明生命周期封装仍在 main。

### R3. 前端：抽公共组件（低风险先行）
新建 `frontend/src/components/`：`modals/`（UsageGuideModal、ExportCenterModal、SprintBriefModal、JobEditModal）、`ScoreChip`+`ScoreBreakdown`、`CandidateListCard`、`ChatProgress`、`DecisionAnalysisCard`、`JobPickerCombobox`、`StatBar`。纯抽取 + props，行为不变。每抽一个 tsc/build 绿。

### R4. 前端：逐个抽视图
新建 `frontend/src/views/`：`ChatView`（最大 ~575 行）、`JobsView`、`CompaniesView`、`PrepView`、`TasksView`、`InterviewsView`、`ConfigView`、`JobDrawer`。一次一个视图，prop-drill 传入所需状态/回调（最小行为风险）。每个视图抽完 Playwright 截图自验该页无回归。

### R5. 候选状态收口为类型（最高「接手」价值）
- 后端：`schemas.py`（或新 `candidates.py`）定义 `Candidate` Pydantic 模型，显式列全字段 + `status: Literal["pending","committed","skipped"]`。落盘/读取在边界处走它校验/序列化，仍存 JSON、零迁移。
- 前端：`types.ts` 定义对应 `Candidate` 接口，替换现在到处散着的 `any`/内联结构。
- 出口：pytest 全绿；一个断言「非法 status 被拒/被规整」。

### R6.（可选）为 3-4 个真·全局状态引入轻量 Context
`aiStatus`、`contextStatus`、`notify/error` 这类每个视图都要的，用一个小 `AppContext` 供给，削掉 prop 噪音；其余仍 prop 传。仅在 R3/R4 之后按需做，不强求。

## Phase P — AI 多 provider 自动容错

- 配置：`config.yaml` `ai.providers`（有序列表，每项 `{base_url_env, api_key_env, model}`）；不填则回退现有单 `OPENAI_*`。密钥仍只进 `.env`。
- `services/ai.py` 单一收口点 `_client()`/`_chat()`：按序尝试每个 provider，带有限重试 + 退避（复用 Telegram 已验证的退避思路），全部失败才抛——落进**现有**「AI 失败 → 规则/模板降级」路径（`describe_extraction_error` 已就绪，无需新增用户可见分支）。
- 测试：monkeypatch 第一个 provider 抛错 → 断言自动用第二个成功；全失败 → 断言走降级且回执/聊天含可读原因、无裸密钥。
- 与 R 的关系：都碰 `ai.py`/`config.py`，放在 R 之后做，避免同文件反复冲突。

## 执行顺序与出口门槛

R1 → R2（逐 router）→ R3 → R4（逐视图）→ R5 →（R6 可选）→ P1。

每一步出口统一门槛：`scripts/quality_gate.sh` 全绿（含 pytest + 前端 build + 系统冒烟 + Alembic 迁移烟测）；前端步骤额外 Playwright 截图；触及架构的步骤（R1 绊线、R2 文件图）同步改 CLAUDE.md。任一步不绿则不提交、不进下一步。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 搬 `_persist_ingest_to_chat` 破坏「不自动 upsert」红线 | R1 绊线测试跟着搬、断言新模块；三处（代码+测试+宪章）同动 |
| 前端抽视图丢状态/回调导致某页静默失效 | 一次一视图 + 每视图 Playwright 截图；契约冻结 |
| 大重构拖慢功能/引入回归 | 步步可提交可回滚；纯搬运；169 测试兜底；不改行为 |
| Telegram 轮询与路由共享助手循环 import | 助手统一下沉 services/chat_ingest.py，单向依赖 |
