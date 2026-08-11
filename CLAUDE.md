# CLAUDE.md — job-one-stop 项目宪章

> 本地优先的个人求职助手:自动发现岗位、沉淀公司调研、按个人画像评分、生成面试准备。
> 单用户、本地运行、不做账号体系、**不自动投递、不自动发消息**。
> 本文件是 AI 与人类共同遵守的**架构标准与红线**,任何改动都必须先读它。修改架构前先更新本文件。

> Phase 2 集成边界：`JOB_ONE_STOP_CONTEXT_REPO_PATH` 可指向独立的个人操作仓库。读取只允许通过 `ContextRepository` 白名单；写入只发生在本人于 Web 聊天的已入库候选卡上点「写入看板」时（点击前原样展示将写入的整行），由唯一的 `ContextWriter` 通道在白名单 `board` 文件的「收集箱」列内插入一行。除此之外不得有任何写入路径（有绊线测试锁定）。

---

## 1. 架构与数据流(单一主干,不要绕过)

所有岗位来源,无论 BOSS / 文件导入 / 手动 / 公众号 / beBee,都汇入同一条管线:

```
来源(Source)
  └─> Collector.collect() -> list[dict]        # 每个来源一个采集器,产出"原始 dict"
        └─> services/normalizer.normalize_record(raw, source)   # 模糊键映射 + 薪资/城市解析 + external_id
              └─> services/importer.upsert_job_records          # 来源链接 + canonical key upsert
                    └─> SQLite(Job/Company/JobSourceLink)
                          └─> scoring / prep / research          # 纯消费端,source-agnostic
```

不可变事实:
- **采集器只产出规范化 dict,数据库写入只走 `upsert_job_records`。** 任何来源都不得直接写库。
- 规范化、入库、评分、面试准备**与来源无关**;新增来源不应改动它们。
- 来源内去重键仍保留 `UNIQUE(Job.source, Job.external_id)` 兼容旧数据；新入库同时写 `JobSourceLink(source, external_id)` 作为来源证据。
- 跨来源去重用 `Job.canonical_key = sha1(title|company|city|area)`。命中 canonical 时保留最早 `Job.source/external_id/url`，只新增来源链接并更新岗位快照字段；避免同一岗位因公众号/beBee/CSV 重复出现。
- `external_id` 默认 `sha1(url)`；**一个 url 拆出多个岗位时**(如公众号一文多岗),在采集器里覆写为 `sha1(url|title|company)`,并保留 `url` 为可点击原链。

关键文件:
- `backend/app/services/collectors.py` — 各来源采集器(`BossOpenCLICollector` / `TabularFileCollector` / `WeChatPasteCollector` / `BeBeeCollector`)
- `backend/app/services/normalizer.py` — `normalize_record` / `parse_salary` / `parse_city_area` / `stable_external_id`(**改这里要极谨慎,影响所有来源**)
- `backend/app/services/importer.py` — `upsert_job_records` / `get_or_create_company`
- `backend/app/services/<source>.py` — 单来源的抓取/解析细节(如 `wechat.py` / `bebee.py` / `ai.py` / `yuanbao.py`)
- `backend/app/services/context_repository.py` — 外部个人操作仓库的只读白名单适配器；不得绕过它读取任意路径
- `backend/app/main.py` — app 装配（中间件/异常处理/静态挂载/`include_router`）+ 生命周期 + Telegram 轮询循环 + SPA 兜底路由 + meta 组端点（config/health/context/diagnostics/ai，与模块级 `settings` 缓存耦合，暂留此处）。**新增业务端点走 `routers/`，不要往 main.py 加路由。**
- `backend/app/routers/<域>.py` — 按域拆分的 `APIRouter`（jobs/chat/collect/scoring/misc/companies/drafts/followups/interviews），main.py `include_router` 挂载。路由只依赖 `deps/models/schemas/services/config`，**绝不 import main**（循环）。
- `backend/app/services/queries.py` — 跨路由共享的查询/落盘 helper（`query_jobs`/`get_profile`/`score_job_into_db`/`application_events`/`download_response`/`job_response`/`validate_weights`）。
- `backend/app/services/{job_ops,collect_ops,prep_ops,sprint_ops,chat_support}.py` — 各域从 main 下沉的专属 helper（岗位状态重算/删除、采集运行、面试准备、冲刺包、聊天上下文）。
- `backend/app/services/advice.py` — 候选岗位的初步决策建议（复用 `decision_chat` 规则引擎 + `ai.analyze_decision_chat_llm`，只读）。构造的 `Job(...)` 是纯内存载体，**从不 add/upsert**；建议挂在候选的纯 UI 字段 `advice` 上，入库前由 `strip_ui_only_fields` 剔除。
- `backend/app/services/decision_reply.py` — 一轮决策问答的落盘核心（user 消息 + 规则/模型分析 + assistant 回复 + `AnalysisRun`），Web `POST /messages` 与 Telegram 追问共用；**只写聊天，不 import importer/upsert**。
- `backend/app/services/chat_ingest.py` — ingest→chat 落盘/线程查找/删除的中立模块（`_persist_ingest_to_chat` 等），被 chat 路由与 Telegram 轮询共享；**绝不 import importer / upsert（绊线测试锁定）**。注意：`commit`/`board-write`/采集的 upsert 是允许的用户触发入库，放在 `routers/` 或 `collect_ops` 里，**不得塞进本模块**。
- `backend/app/deps.py` — 共享 FastAPI 依赖（`get_session` / `SessionDep`），供路由模块统一 import
- `backend/app/models.py` — 表结构(见红线 §3.5)，含 `JobSourceLink` 来源证据表

---

## 2. 新增一个岗位来源(标准配方,照抄)

1. **解析细节放 `services/<source>.py`**:抓取(统一用 `httpx`,带超时/UA/限速)+ 解析成 dict 列表。键用规范化器认识的名字:`title / company_name / url / salary_text / city / area / experience / degree / skills / description / recruiter`(其余字段交给 `normalize_record`)。
2. **在 `collectors.py` 加 `<Source>Collector`**(`@dataclass`,实现 `collect() -> list[dict]`):取数 → 解析 → 逐条 `normalize_record(raw, source="<中文来源名>")` →(一文多岗时覆写 `external_id`)→ 去重。维护 `self.report = {urls_total, urls_ok, jobs, skipped:[{url,reason}]}`。
3. **配置放 `config.yaml` 的 `<source>:` 段**,在 `config.py` 加 `<source>_config` 属性读取;**密钥只进 `.env`**。
4. **加端点**（放 `routers/collect.py`，不要加到 main.py）:
   - 配置驱动(类似 BOSS/beBee):用 `services/collect_ops.py` 的 `run_collector(session, source_label, collector)`。
   - 粘贴/外部输入驱动(类似公众号):参考 `collect_ops.run_wechat_collection`。
   - 端点负责建 `SourceRun`、跑采集器、`upsert_job_records`、把 `collector.report` 写进 `SourceRun.raw_config`。失败置 `status="failed"` + `error`,**绝不抛裸异常给前端**。
5. **前端**:岗位带上新 `source` 会自动出现在表格「来源」列与来源筛选;如需主动触发,在 topbar 加一个按钮调用对应端点(照搬 `runBossCollection` / `collectWeChat` 模式)。视图层不得写来源特判逻辑。
6. **测试必须有**(见 §4):解析器纯函数测试 + 端点流程测试(`monkeypatch` 掉网络抓取,不联网)。
7. **更新文档**:README 加来源说明,必要时更新本文件的"当前数据源"。

---

## 3. 红线(硬性,不可逾越)

1. **本地优先 / 单用户**:无多用户、无登录体系;数据只存本机 SQLite。
2. **不自动化对外动作**:不自动投递、不自动发消息、不自动外发任何联系方式。抓到的招聘人微信/电话/邮箱**仅本地留存供查看**。
   - **例外(仅此一种):给机主本人发系统回执。** Telegram 渠道只向白名单 `allowed_chat_id`(机主自己那个 chat)回执「识别到 N 个候选，请在 Web 确认入库」、随回执附上的**初步决策建议**,以及机主用 `?`/`/ask` 主动提问时的**回答**——都属于本机→本人的状态通知/判断结果,**不是对外动作**。绝不向招聘方或任何第三方发任何消息;回执内容不得包含未经本人触发就外发的联系方式。**ingest 默认不写 Job 表**，用户在聊天里点「入库选中」才 upsert;建议与追问同样只读,不入库、不改岗位状态。
3. **抓取合规**:只抓**公开**内容;低频、人工触发;**不破解验证码 / 风控页 / 付费墙**;尊重 robots 与各平台 ToS;**不二次分发**抓到的内容。被风控拦截就跳过并记录原因,不硬刚。
4. **不泄密**:`.env`、`*.sqlite3`、`data/`、日志、`*.xlsx`、登录态(`.yuanbao/`、`*storage_state*`)一律不提交;改 `.gitignore` 前先确认不会带出隐私。
5. **不用 `create_all` 偷改表结构**:`init_db()` 走 `SQLModel.metadata.create_all`——**新增表 OK,但给现有表加列不会自动迁移**。优先复用 `Job` 现有字段;确需加列/改列,写显式 alembic 迁移并在 PR 说明,不可假设旧库会自动升级。
6. **管线唯一**:新增来源必须经 `normalizer` + `importer`,不得绕过;不得在采集器里直接 `session.add(Job(...))`。
7. **不静默丢数据**:解析/抓取失败要进 `report.skipped` 带原因;一篇都拆不出时兜底产出至少 1 条,而不是返回空。
8. **来源解耦**:`scoring.py` / `prep.py` / 前端视图保持 source-agnostic,禁止出现 `if source == "xxx"` 的业务特判。
9. **网络访问统一封装**:一律走 `httpx`,带超时、移动端 UA、限速;不在路由函数里裸发请求。重依赖(如 `playwright`)放 `requirements-automation.txt` 并**延迟 import**,默认关闭。
10. **外部上下文写入唯一通道**:`JOB_ONE_STOP_CONTEXT_REPO_PATH` 指向的仓库不是应用数据库。读取只走 `ContextRepository` 白名单;写入未经本人在 Web 点击确认,不得写入任何字节;确认后也只允许 `ContextWriter` 在白名单 `board` 文件的指定列内插入一行(不改写、不删除既有内容,不创建/移动/删除文件,不 EOF 追加——看板是 Obsidian Kanban 文件,尾部有设置块)。`ContextWriter` 的引用只允许出现在 context_repository.py / board_write.py / main.py(AST 绊线测试锁定);不得把宿主机绝对路径返回 API。看板列=岗位状态唯一事实源,状态流转由本人在 Obsidian 拖卡完成,应用绝不写「移动卡片/状态变更」类内容;岗位卡(cards/)在拿到真实样例并回读 roadmap 之前不开放写入。
11. **KISS 优先**:聊天是默认入口，岗位管理是按需展开的辅助能力。新增功能前先证明它解决高频用户动作；优先复用现有模型、路由和组件，不为低频场景增加常驻导航、后台服务、抽象层或新依赖。

---

## 4. 测试与质量门槛

- 框架:`pytest`。当前环境中 `fastapi.testclient.TestClient`/AnyIO 线程池会卡住,后端流程测试改用 `httpx.AsyncClient(ASGITransport)` + 重载 app/db/config + `monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", tmp sqlite)` 做隔离。
- **测试不得联网**:抓取函数(`wechat.fetch_article` / `bebee.fetch_listing` 等)在测试里用 `monkeypatch` 桩掉;解析用 `tests/fixtures/` 下的样例 HTML。
- 每个新来源至少覆盖:链接/字段抽取、一文多岗拆分、`external_id` 唯一性、抓取失败的 skip 记录。
- 提交前跑 `scripts/quality_gate.sh` 必须全绿；它包含后端测试、前端构建、真实 HTTP 系统冒烟和 Alembic 旧库迁移烟测。
- 系统冒烟使用 `scripts/system_smoke.sh`，只写临时 SQLite，不读取真实 `data/job_one_stop` 数据。
- 注释/命名/语言风格**沿用周边代码**(后端中文注释、4 空格;前端 TS 风格)。

---

## 5. 目录与约定

- 后端:FastAPI + SQLModel;逻辑在 `services/`,端点按域放 `routers/`,`main.py` 只放 app 装配 + 生命周期 + meta 组。
- 前端:React + Vite + TS;复用 `src/api.ts` 的 `api()/jsonBody()` 与既有 CSS 类(`modal`/`primary-action`/`icon-button`/`source-select` 等),不引重组件库。
- 配置:`config.yaml`(每来源一段 + `scoring`/`followup` 等功能段)+ `.env`(密钥)。AI 走 OpenAI 兼容协议(`OPENAI_API_KEY`/`OPENAI_BASE_URL`),`ai.enabled` 默认关;启用后既做公众号 LLM 兜底抽取,也做面试准备按 JD 定制(`ai.tailor_interview_prep_llm`),不可用/失败时逐键回退 `prep.py` 模板。`followup.stale_days` 控制 fit/interview 岗位多少天无活动算「需跟进」(`services/followup.py`,source-agnostic)。
  多 provider 容错:`ai.providers` 列表(可选)按顺序尝试多个 OpenAI 兼容 provider,每个失败先退避重试再切下一个,全部失败才落进既有的规则/模板降级;密钥各进不同 `.env` 变量(`api_key_env`/`base_url_env`/`model_env` 指名去哪个 env 读),不进 `config.yaml`。不配置 `providers` 时行为与单一 `OPENAI_*` 环境变量完全一致(见 `services/ai.py::_providers`)。
  Provider 卡可在设置页(`ConfigView` AI 区)以弹窗形式增/删/改/排序:每次操作都单独 `PUT /api/config` 落盘 `ai.providers`(`label`/`api_key_env`/`base_url`/`model`,均非密钥);Key 只经 `POST /api/ai/credentials` 写 `PROJECT_DIR/.env`(`env_name` 需匹配 `_ENV_NAME_PATTERN` 大写变量名,`_is_sensitive_key_name` 放行 `*_env` 结尾的引用字段,拦截字面量密钥字段),写完立即 `os.environ[...] + get_settings.cache_clear()`,单进程部署下同进程内即时生效,无需重启;`GET /api/ai/status` 的 `provider_keys` 只按 `api_key_env` 回布尔「该变量是否有值」,绝不回传密钥本身。
- 外部个人上下文路径只进环境变量 `JOB_ONE_STOP_CONTEXT_REPO_PATH`；应用通过只读 `ContextRepository` 检查入口、决策规则、画像、看板和岗位卡，不在 `config.yaml` 保存宿主机绝对路径。
- 依赖:核心进 `requirements.txt`;可选/重依赖进 `requirements-automation.txt`。

---

## 6. 运行(WSL / Linux / macOS)

```bash
python3 -m venv .venv                                  # 强烈建议用虚拟环境,避免 pip/python 指向混乱
.venv/bin/python -m pip install -r requirements.txt
scripts/quality_gate.sh                                 # 完整质量门禁
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000   # 后端
cd frontend && npm install && npm run dev               # 前端 http://127.0.0.1:5173
```
Windows 宿主机可用 `run_backend.bat` / `run_frontend.bat`。**不要混用宿主机与 WSL 的环境**:venv 必须在运行所在的系统里创建。`opencli`(BOSS 采集)是 Windows 工具;公众号 / beBee 等纯 Python 来源在 WSL 即可运行。

日常使用(非改代码)优先单进程部署模式:`scripts/app.sh start`——构建一次 `frontend/dist` 后只跑 `uvicorn` 一个进程(:8000,前端由后端挂载),`stop`/`status`/`logs`/`update` 见脚本;与上面的开发模式共用 `./data/job_one_stop/` 数据库,两者不要同时启动。

---

## 7. 当前数据源

| 来源 (`Job.source`) | 采集器 | 触发 | 取数方式 |
|---|---|---|---|
| `BOSS直聘` | `BossOpenCLICollector` | `POST /api/collect/runs?source=boss` | 调 opencli 子进程(Windows) |
| `导入文件` | `TabularFileCollector` | `POST /api/jobs/import` | 上传 CSV/XLSX |
| `manual` | — | `POST /api/jobs` | 手动单条 |
| `公众号` | `WeChatPasteCollector` | `POST /api/collect/wechat` | 粘贴元宝回答/链接 → 抓 mp.weixin 正文 → 拆多岗位(正则,LLM 兜底);可选元宝 Playwright 自动化 |
| `beBee` | `BeBeeCollector` | `POST /api/collect/bebee` | 抓 bebee 角色/列表页 → 解析 JobPosting JSON-LD、Next/RSC jobs、microdata 或可见卡片 |

> 外部平台页面会变化。公众号、元宝自动化和 beBee 首次接入新页面时必须先拿真实样例核对；解析器有 fixture 测试和 skipped 降级，但不要盲写选择器。

### 统一 ingest 入口与传输层

- `services/ingest.py::run_ingest` 是**统一分派器**:文本/截图 → `classify_links` 分派采集器 + freeform LLM → **只返回 `candidates`（不写 Job 表）**。规范化仍走 `normalize_record`；真正入库只在用户确认后调用 `upsert_job_records_with_ids`（§6）。`Job.source` 仍是各来源标签,**不是** `Telegram`(§8)。
- **默认不入库**:`POST /api/ingest` 与 Telegram 轮询都走 `_persist_ingest_to_chat`：建 `ChatThread(kind="ingest")`，user 消息保留原文/截图附件，assistant 消息 `metadata_json.candidates` 挂候选。用户在 Web 聊天勾选后 `POST /api/chat/threads/{id}/candidates/commit` 才 upsert + 尽力评分。跳过/不入库时原料仍保留在聊天里。
- **触发方式≠数据源**:HTTP/Telegram 只是触发方式。新增采集器不需要动 ingest;新增来源识别只在 `classify_links` 加一行。
- **建议与追问(手机端在线回复)**:识别到候选后,`chat_ingest` 调 `services/advice.build_candidate_advice` 为**前 `ingest.advice_max_candidates` 个**候选生成初步建议(优先级/方向/下一步/先问什么),挂在候选的纯 UI 字段 `advice` 上——Web 候选卡结构化展示,Telegram 用 `format_advice_block` 排版后附在回执末尾。开关 `ingest.advice`(默认开);`ai.enabled=false` 时只落规则引擎结论,不做模型调用。判断标准**不新写第二套**:一律复用 `decision_chat` 的 `build_rule_analysis` + `merge_model_analysis`。
  机主在 TG 用 `?` / `？` / `/ask` 开头发消息 = **提问**(`telegram.parse_question`),走 `services/decision_reply.reply_in_thread`(与 Web `POST /messages` 同一函数);回复某条回执提问落回那条 ingest 线索,否则落进固定的「手机提问」通用线程。**不带前缀的消息一律仍按材料处理**——靠内容猜意图会把补充材料吃成提问,等于丢材料。
- **追问的岗位锚点(`decision_reply.resolve_thread_anchor`)**:候选入库前没有 Job 记录、`ChatThread.job_id` 恒为 None,所以岗位事实来自 `chat_ingest.thread_candidates`(该线索已识别的候选,跨 assistant 消息去重累积)。优先级:`thread.job_id` 真实 Job > 指名的候选(`candidate_index`;TG 用 `?2`,Web 用候选卡「问这个」→ `ChatMessageCreate.candidate_index`)> 单候选 > 第一个候选。已 committed 的候选回落到真实 Job;都没有则 `kind="none"`,行为与加锚点前一致。**回答开头必须回显锚点**(`针对 ② …`),否则多候选线索里的结论无法归属。
- **同一岗位多图合并(prior_candidates)**:相册(`media_group_id`)/引用回复补充会被传输层路由到**同一** `kind="ingest"` 线程(`target_thread_id`)。往已有线程追加时,`_persist_ingest_to_chat` 会跨该线程所有 assistant 消息累积并去重出已识别候选,经 `run_ingest(prior_candidates=)` 透传给 `ai.extract_jobs_freeform(prior_candidates=)`,让模型把碎片图(如只有「任职要求」)并进已知岗位、补全字段,而不是把碎片当独立岗位认不出。分组靠传输层结构信号(可靠),合并才用 LLM。去重 key 优先 `canonical_key`,但对「公司未知」的真实标题岗位(如「独立站运营·未知公司」)会退到标题做 key——不能丢。`prior_candidates` 为空时行为与单图完全一致。**注意**:该能力目前仅覆盖 Telegram/`/api/ingest` 通道;Web 聊天只发 `/messages`(决策对话),不驱动 ingest 抽取,是查看/确认入口。
- **Telegram 传输层**(`services/telegram.py`,默认关闭,opt-in):
  - 长轮询 `getUpdates` 是后端主动**出站**请求 `api.telegram.org`,后端**无需对外暴露端口**;`config.yaml telegram.enabled=true` + `.env` 的 `TELEGRAM_BOT_TOKEN` 才启动(见 `main.py` lifespan 的 `_telegram_poll_loop`)。
  - **只处理白名单 `telegram.allowed_chat_id`(机主本人)的消息**,其余一律忽略。回执**只发机主本人**——符合 §2 的机主回执豁免,绝不发招聘方。
  - 回执文案是「识别到 N 个候选…打开 Web 确认」+ 可选的建议正文,**不声称已入库**;建议只是判断,不改变入库口径。