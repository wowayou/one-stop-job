# CLAUDE.md — job-one-stop 项目宪章

> 本地优先的个人求职助手:自动发现岗位、沉淀公司调研、按个人画像评分、生成面试准备。
> 单用户、本地运行、不做账号体系、**不自动投递、不自动发消息**。
> 本文件是 AI 与人类共同遵守的**架构标准与红线**,任何改动都必须先读它。修改架构前先更新本文件。

> Phase 2 集成边界：`JOB_ONE_STOP_CONTEXT_REPO_PATH` 可指向独立的个人操作仓库。读取只允许通过 `ContextRepository` 白名单；写入只发生在本人于 Web 聊天的已入库候选卡上点「写入看板」时（点击前原样展示将写入的整行），由唯一的 `ContextWriter` 通道在白名单 `board` 文件的「收集箱」列内插入一行。除此之外不得有任何写入路径（有绊线测试锁定）。

> **自动驾驶边界**：`automation.mode=autopilot` 只自动执行每日搜集、去重、岗位族识别、评分、候选排序和本地投递材料准备，最终进入人工确认队列；它不提交表单、不联系招聘方、不改变岗位/看板状态。`auto_apply_experiment` 仅保留为未来独立高风险模式名，当前版本不实现也不允许启用。自动驾驶与自动投递不是同一能力，任何后续自动投递实验都必须另做权限、幂等、审计、白名单和平台风险评估。

---

## 1. 架构与数据流(单一主干,不要绕过)

所有岗位来源,无论 BOSS / 文件导入 / 手动 / 公众号 / beBee,都汇入同一条管线:

```
来源(Source)
  └─> Collector.collect() -> list[dict]        # 每个来源一个采集器,产出"原始 dict"
        └─> services/normalizer.normalize_record(raw, source)   # 模糊键映射 + 薪资/城市解析 + external_id
              └─> services/collect_filter.apply_area_filter     # ①区域白名单(只挡采集器路径;挡掉的只记数,不静默丢)
                    ├─ 已在岗位池(importer.split_known_records) ─> upsert_job_records   # 只刷新快照,created 恒为 0
                    └─ 全新 ─> ②_dedupe_fresh                        # 已在待筛/已跳过的不再列一遍
                          └─> jobs.attach_candidate_scores           # 与岗位池 FitScore 同一个 score_job
                                └─> ③collect_filter.apply_score_gate # 硬排除 / 低于 min_score / 超 max_pending
                                      └─> chat_ingest.persist_collect_candidates   # kind="collect" 候选,不写 Job
                                            └─> ④本人在 Web 勾选「入库选中」        # 硬阻断/已在池/重复的默认不勾
                                                  └─> routers/chat.commit_candidates -> upsert_job_records
                                                        └─> SQLite(Job/Company/JobSourceLink)
                                                              └─> scoring / prep / research   # 纯消费端,source-agnostic
```

不可变事实:
- **采集器只产出规范化 dict,数据库写入只走 `upsert_job_records`。** 任何来源都不得直接写库。
- **采集不落盘**:采集器带回的**全新**岗位一律先进候选(`kind="collect"` 聊天线索),本人勾选后才 upsert;已在池中的岗位照旧刷新快照(那是你早就筛过的,不算新噪音)。CSV 导入 / 手动单条 / Telegram 截图 ingest **不走**这条初筛(前两者是你主动挑的输入,后者本来就是候选制)。
- 规范化、入库、评分、面试准备**与来源无关**;新增来源不应改动它们。
- 来源内去重键仍保留 `UNIQUE(Job.source, Job.external_id)` 兼容旧数据；新入库同时写 `JobSourceLink(source, external_id)` 作为来源证据。
- 跨来源去重用 `Job.canonical_key = sha1(title|company|city|area)`。命中 canonical 时保留最早 `Job.source/external_id/url`，只新增来源链接并更新岗位快照字段；避免同一岗位因公众号/beBee/CSV 重复出现。
- `external_id` 默认 `sha1(url)`；**一个 url 拆出多个岗位时**(如公众号一文多岗),在采集器里覆写为 `sha1(url|title|company)`,并保留 `url` 为可点击原链。

关键文件:
- `backend/app/services/collectors.py` — 各来源采集器(`BossOpenCLICollector` / `TabularFileCollector` / `WeChatPasteCollector` / `BeBeeCollector`)
- `backend/app/services/normalizer.py` — `normalize_record` / `parse_salary` / `parse_city_area` / `stable_external_id`(**改这里要极谨慎,影响所有来源**)
- `backend/app/services/importer.py` — `upsert_job_records` / `get_or_create_company` / `split_known_records`（只读分流：哪些记录已在岗位池、哪些是全新的）
- `backend/app/services/collect_filter.py` — 采集结果过滤纯函数，两道闸门都在这里，只被 `collect_ops` 调用：
  - **区域白名单**（`normalize_area` 削行政区后缀、`area_allowed`、`apply_area_filter`），跑在评分**之前**；`city == area` 视为「区域未知」（`parse_city_area` 对单段输入的产物），是否放行看 `keep_unknown_area`。配置在 `config.yaml collect.area_filter`。
  - **候选闸门**（`apply_score_gate`），必须跑在 `attach_candidate_scores` **之后**（它筛的就是候选上的 `score`/`hard_blocked`）：硬阻断 → 低于 `min_score` → 超出 `max_pending` 的尾部，依次收窄。`score is None`（评分故障）一律放行——分数缺失是我们自己的问题，不能因此吃掉一个可能合适的岗位。配置在 `config.yaml collect.score_gate`，默认 `min_score: 45` / `max_pending: 15`，`enabled: false` 即回到加它之前的行为。
  两道闸门的报告结构都固定并进 `SourceRun.raw_config`，挡掉的条数与前 5 条样例都要能查（§7 不静默丢数据）；计数措辞由 `collect_run_summary` 统一输出，Web 线索正文与手机回执共用。
- `backend/app/services/reach_policy.py` — 求职相邻度唯一业务入口：解析 `reach.policy` 岗位族、按 `core/adjacent/exploratory` 分类、生成带 100/70-30/50-30-20 配额的搜索计划，并输出能力重合、必要条件缺口、可补齐差距和推荐/人工判断/排除结论。个人岗位族只放本机忽略的 `config.yaml`；公开仓库的 `config.example.yaml` 只给通用空模板，不写个人经历。
- `backend/app/services/<source>.py` — 单来源的抓取/解析细节(如 `wechat.py` / `bebee.py` / `ai.py` / `yuanbao.py`)
- `backend/app/services/context_repository.py` — 外部个人操作仓库的只读白名单适配器；不得绕过它读取任意路径
- `backend/app/services/board_sla.py` — 看板「下一步」日期解析（纯函数，只读）：活跃列卡片 → 到期动作（send/follow/close），供日清单使用。日期只从「下一步：」之后、「[详情]」链接之前的动作区提取。
- `backend/app/services/daily_digest.py` — 晨间日清单组装（看板到期动作 + followup stale 岗位 → digest 文本）；被 `routers/followups.py` 的 `/api/follow-ups/board-sla` 端点与 main.py 的 `_daily_digest_loop` 共用。推送仅发机主本人（红线 §2 豁免），配置在 `config.yaml schedule.digest`（默认关闭）。循环为 15 分钟轮询 + `data 目录/daily_digest_state.json` 状态文件：到点未发才发、当天只发一次，发送时点关机则开机后补发。状态文件里 **`last_sent`（确认送达的日期）与 `last_collected`（晨间采集的日期）必须分开记**：`telegram.send_message` 失败只吞异常返回 None，所以「发出去了」只能看返回值——没送达就不写 `last_sent`，下个周期重试；而采集绝不能跟着重试（红线 §3.3 频率上限每日一次）。采集失败（`run_source` 只置 `SourceRun.failed` 并返回，不抛）压成一行 `collect_note` 附在推送正文，否则「今天没岗位」和「今天没采到」在手机上长得一模一样；附注里带 `/collect` 补采入口——定时那次不自动重跑，补救手段必须写在失败现场（见 §7 Telegram 命令）。
- **出站可达性**：`api.telegram.org` 在国内常被 DNS 污染，直连是 `[Errno 101] Network is unreachable`；而自启进程由 Windows 启动文件夹的 `.bat` → `wsl.exe` 拉起，**拿不到交互 shell 的代理变量**。代理写进 `.env`（`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`，`config.py` 的 `load_dotenv` 会灌进 `os.environ`，httpx 自动生效），`NO_PROXY` 保留国内域名让 AI/公众号/BOSS 继续直连。诊断顺序：`data/app/backend.log` → `daily_digest_state.json` → `source_runs` 表。
- `backend/app/main.py` — app 装配（中间件/异常处理/静态挂载/`include_router`）+ 生命周期 + Telegram 轮询循环 + 晨间日清单/自动驾驶循环（`_daily_digest_loop` 常驻低频检查，关闭时不采集）+ SPA 兜底路由 + meta 组端点（config/health/context/diagnostics/ai，与模块级 `settings` 缓存耦合，暂留此处）。**新增业务端点走 `routers/`，不要往 main.py 加路由。**
- `backend/app/services/automation.py` — 自动驾驶的薄服务层：读写模式/相邻度、调用既有 `run_source` 扫描、批量追加 FitScore 并原位重评候选。不得复制采集漏斗，也不得实现任何外部投递动作。
- `backend/app/services/updates.py` + `backend/app/routers/updates.py` — 升级发现（`GET /api/updates/check`）：查 GitHub 公开 Releases，语义版本比较，只认正式 Release（draft / pre-release 一律跳过），结果按 `updates.cache_ttl_hours` 缓存（失败只短缓存 5 分钟，手动检查 `force=true` 绕过）。**只发现，不安装**——不下载安装包、不改文件、不重启进程；应用内一键更新需要代码签名与 updater 公钥，未做也不允许悄悄补上。平台/架构由后端 `sys.platform` / `platform.machine()` 判定（同一台机器，比在 webview 里猜 UA 可靠），安装包按后缀优先级 + 架构关键字匹配；匹配不到只给发布页链接，绝不塞一个错架构的包。`offline` 与 `latest` 必须分开——网络不通时显示「无法连接更新服务」，不能误报「已是最新」。
- `backend/app/services/diagnostics.py` + `backend/app/routers/diagnostics.py` — 运行时诊断与失败恢复（`GET /api/diagnostics/runtime` / `GET /api/diagnostics/logs` / `POST /api/diagnostics/backup`；原有的 `GET /api/diagnostics/deployment` 仍留在 main.py，它与模块级 `settings` 缓存耦合，搬动有行为变化风险）。三条边界：① **不回传任何密钥值**——`.env` 一侧只报「变量名 + 是否有值」的布尔，日志先按已知密钥值精确替换、再上正则兜底（长值先替换，否则短值会把长值切碎留下可辨认的尾巴）；② **不做新的出站探测**——"网络连接状态"只汇总已有信号（`updates.cached_result()`、代理是否配、Telegram 是否启用），判断不了就写"未知"，绝不为了画绿点去连别人的服务器（红线 §3.9）；③ 个人上下文仓库的宿主机绝对路径绝不出现在返回里（红线 §10），但应用**自己的** `data_dir` 会返回——「打开数据目录」按钮要靠它。备份用 SQLite 的 `Connection.backup()` 在线复制到 `data/backups/<时间戳>/`，**只新建，不覆盖既有备份、不动原库**，目录布局与 `scripts/app.sh backup` 一致以便互相还原。
- `VERSION` + `backend/app/version.py` + `scripts/sync_version.py` — 版本号唯一事实源是根目录 `VERSION`，脚本同步到 `frontend/package.json` / `src-tauri/Cargo.toml` / `src-tauri/Cargo.lock` / `src-tauri/tauri.conf.json` / `backend/app/version.py`，`tests/test_version_sync.py` 锁住一致性。**不要在任何一处单独改版本号**：升级检查靠语义版本比较判新旧，漂移会让用户「装完就被再提示一次升级」。release workflow 打标签时会校验「标签 == VERSION == 各清单」。
- `backend/app/routers/<域>.py` — 按域拆分的 `APIRouter`（jobs/chat/collect/scoring/misc/companies/drafts/followups/interviews），main.py `include_router` 挂载。路由只依赖 `deps/models/schemas/services/config`，**绝不 import main**（循环）。
- `backend/app/services/queries.py` — 跨路由共享的查询/落盘 helper（`query_jobs`/`get_profile`/`score_job_into_db`/`application_events`/`download_response`/`job_response`/`validate_weights`）。
- `backend/app/services/{job_ops,collect_ops,prep_ops,sprint_ops,chat_support}.py` — 各域从 main 下沉的专属 helper（岗位状态重算/删除、采集运行、面试准备、冲刺包、聊天上下文）。`collect_ops._triage_records` 是采集漏斗（区域过滤 → 已知刷新 / 全新 → 判重 → 评分 → 候选闸门）的唯一实现，`run_collector` 与 `run_wechat_collection` 共用；**顺序不可调**：闸门筛的是评分产物，挪到 `attach_candidate_scores` 之前就变成按空字段过滤，会把整批候选静默挡掉。计数摘要 `collect_run_summary` 同时供聊天正文与手机回执，两处措辞不许各写一遍。
- `backend/app/services/advice.py` — 候选岗位的初步决策建议（复用 `decision_chat` 规则引擎 + `ai.analyze_decision_chat_llm`，只读）。构造的 `Job(...)` 是纯内存载体，**从不 add/upsert**；建议挂在候选的纯 UI 字段 `advice` 上，入库前由 `strip_ui_only_fields` 剔除。
- `backend/app/services/decision_reply.py` — 一轮决策问答的落盘核心（user 消息 + 规则/模型分析 + assistant 回复 + `AnalysisRun`），Web `POST /messages` 与 Telegram 追问共用；**只写聊天，不 import importer/upsert**。
- `backend/app/services/chat_ingest.py` — 候选→chat 落盘/线程查找/删除的中立模块：`_persist_ingest_to_chat`（`kind="ingest"`，手机/HTTP 发来的材料）与 `persist_collect_candidates`（`kind="collect"`，采集初筛）、`recent_collect_candidates`（近 30 天候选，供采集判重与日清单待筛段）。被 chat 路由、Telegram 轮询与 `collect_ops` 共享；**绝不 import importer / upsert（绊线测试锁定）**。注意：`commit`/`board-write`/采集的 upsert 是允许的用户触发入库，放在 `routers/` 或 `collect_ops` 里，**不得塞进本模块**。
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
   - 端点负责建 `SourceRun`、跑采集器、把 `collector.report` 写进 `SourceRun.raw_config`。失败置 `status="failed"` + `error`,**绝不抛裸异常给前端**。
   - 入库口径由 `collect_ops._triage_records` 统一处理(区域过滤 → 已知刷新 → 全新 → 判重 → 评分 → 候选闸门),新来源**什么都不用做**;不要在新端点里自己调 `upsert_job_records`,那会绕开人工初筛,也会绕开收窄漏斗。
5. **测试必须有**(见 §4):解析器纯函数测试 + 端点流程测试(`monkeypatch` 掉网络抓取,不联网) + 「采集不新建 Job、只产出候选」的断言(照抄 `tests/test_collect_triage.py`)。
6. **前端**:岗位带上新 `source` 会自动出现在表格「来源」列与来源筛选;如需主动触发,在 topbar 加一个按钮调用对应端点(照搬 `runBossCollection` / `collectWeChat` 模式)。视图层不得写来源特判逻辑。
7. **更新文档**:README 加来源说明,必要时更新本文件的"当前数据源"。

---

## 3. 红线(硬性,不可逾越)

1. **本地优先 / 单用户**:无多用户、无登录体系;数据只存本机 SQLite。
2. **不自动化对外动作**:不自动投递、不自动发消息、不自动外发任何联系方式。抓到的招聘人微信/电话/邮箱**仅本地留存供查看**。
   - **例外(仅此一种):给机主本人发系统回执。** Telegram 渠道只向白名单 `allowed_chat_id`(机主自己那个 chat)回执「识别到 N 个候选，请在 Web 确认入库」、随回执附上的**初步决策建议**,以及机主用 `?`/`/ask` 主动提问时的**回答**——都属于本机→本人的状态通知/判断结果,**不是对外动作**。绝不向招聘方或任何第三方发任何消息;回执内容不得包含未经本人触发就外发的联系方式。**ingest 默认不写 Job 表**，用户在聊天里点「入库选中」才 upsert;建议与追问同样只读,不入库、不改岗位状态。
   - `automation.mode=autopilot` 也不构成例外扩张：它只能产出本地候选与材料，确认按钮仍是唯一投递前人工闸门；“停止自动化”必须能立即阻止后续定时扫描。
3. **抓取合规**:只抓**公开**内容;低频、人工触发(唯一例外:本人在 `config.yaml` 显式开启 `automation.mode=autopilot` 或 `schedule.digest.collect_first` 后的**每日一次**晨间定时采集,两者共用同一幂等位,频率上限即每日一次,失败只记日志不重试);**不破解验证码 / 风控页 / 付费墙**;尊重 robots 与各平台 ToS;**不二次分发**抓到的内容。被风控拦截就跳过并记录原因,不硬刚。
4. **不泄密**:`.env`、`*.sqlite3`、`data/`、日志、`*.xlsx`、登录态(`.yuanbao/`、`*storage_state*`)一律不提交;改 `.gitignore` 前先确认不会带出隐私。
5. **不用 `create_all` 偷改表结构**:`init_db()` 走 `SQLModel.metadata.create_all`——**新增表 OK,但给现有表加列不会自动迁移**。优先复用 `Job` 现有字段;确需加列/改列,写显式 alembic 迁移并在 PR 说明,不可假设旧库会自动升级。
6. **管线唯一**:新增来源必须经 `normalizer` + `importer`,不得绕过;不得在采集器里直接 `session.add(Job(...))`。
7. **不静默丢数据**:解析/抓取失败要进 `report.skipped` 带原因;一篇都拆不出时兜底产出至少 1 条,而不是返回空。
8. **来源解耦**:`scoring.py` / `prep.py` / 前端视图保持 source-agnostic,禁止出现 `if source == "xxx"` 的业务特判。
9. **网络访问统一封装**:一律走 `httpx`,带超时、移动端 UA、限速;不在路由函数里裸发请求。重依赖(如 `playwright`)放 `requirements-automation.txt` 并**延迟 import**,默认关闭。
   - **升级检查**(`services/updates.py`)是除采集/AI 之外唯一的出站请求:GitHub 公开只读 Releases 接口,不带凭据、不上传任何本地数据,`updates.enabled=false` 即完全不发。它不属于 §2 的"对外动作"(不联系任何第三方、不外发联系方式),但同样受"只发现不执行"约束——**下载与安装必须由本人在系统浏览器/系统安装器里完成**,应用不得自行下载、替换或重启自己。
   - 前端打开外部链接一律走 `api.ts` 的 `openExternal`(桌面端经已授权的 `shell:allow-open`,浏览器退回 `window.open`)。桌面端 CSP 只放行 `connect-src 'self' http://127.0.0.1:*`,所以**任何第三方接口都不能从 webview 直接 fetch**,必须由后端代理——这也是升级检查放后端的原因。
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
  `updates` 段控制升级发现(`enabled` / `repo` / `check_on_startup` / `cache_ttl_hours` / `timeout_seconds`);默认值都写在 `services/updates.py`,配置段缺失时按默认值工作,`enabled=false` 即完全不发出站请求。
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
Windows 宿主机可用 `start_app.bat`(Docker 模式)或 `run_quality_check.bat`。**不要混用宿主机与 WSL 的环境**:venv 必须在运行所在的系统里创建。`opencli`(BOSS 采集)是 Windows 工具;公众号 / beBee 等纯 Python 来源在 WSL 即可运行。

日常使用(非改代码)优先单进程部署模式:`scripts/app.sh start`——构建一次 `frontend/dist` 后由**看门狗子进程**(`setsid` 独立会话)循环拉起 `uvicorn`(:8000,前端由后端挂载);uvicorn 崩溃时看门狗自动退避重启(5→10→…→60s 封顶,活过 5 分钟则重置),`do_stop` 删哨兵文件 `data/app/run.watchdog` 通知看门狗干净退出。`stop`/`status`/`logs`/`update` 见脚本;与上面的开发模式共用 `./data/job_one_stop/` 数据库,两者不要同时启动。pid 文件(`data/app/backend.pid`)指向看门狗进程本身,不是 uvicorn——`is_running` 检查的是守护是否在。

---

## 7. 当前数据源

| 来源 (`Job.source`) | 采集器 | 触发 | 取数方式 |
|---|---|---|---|
| `BOSS直聘` | `BossOpenCLICollector` / `OpenCLIMultiCommandCollector` | `POST /api/collect/runs?source=boss`;`schedule.digest.collect_first` 每日一次晨间定时;或机主在 Telegram 发 `/collect` 手动补采一次 | 调 opencli 子进程(Windows)。配了 `opencli.boss_keywords` 走多关键词收集器:逐关键词替换 `boss_cmd` 里 `search` 后的词、命令间限速 2 秒、按 `external_id` 跨关键词去重;单关键词失败进 `report.skipped`,全失败才 `SourceRun.failed` |
| `导入文件` | `TabularFileCollector` | `POST /api/jobs/import` | 上传 CSV/XLSX |
| `manual` | — | `POST /api/jobs` | 手动单条 |
| `公众号` | `WeChatPasteCollector` | `POST /api/collect/wechat` | 粘贴元宝回答/链接 → 抓 mp.weixin 正文 → 拆多岗位(正则,LLM 兜底);可选元宝 Playwright 自动化 |
| `beBee` | `BeBeeCollector` | `POST /api/collect/bebee` | 抓 bebee 角色/列表页 → 解析 JobPosting JSON-LD、Next/RSC jobs、microdata 或可见卡片 |

> 外部平台页面会变化。公众号、元宝自动化和 beBee 首次接入新页面时必须先拿真实样例核对；解析器有 fixture 测试和 skipped 降级，但不要盲写选择器。

### 统一 ingest 入口与传输层

- `services/ingest.py::run_ingest` 是**统一分派器**:文本/截图 → `classify_links` 分派采集器 + freeform LLM → **只返回 `candidates`（不写 Job 表）**。规范化仍走 `normalize_record`；真正入库只在用户确认后调用 `upsert_job_records_with_ids`（§6）。`Job.source` 仍是各来源标签,**不是** `Telegram`(§8)。
- **默认不入库**:`POST /api/ingest` 与 Telegram 轮询都走 `_persist_ingest_to_chat`：建 `ChatThread(kind="ingest")`，user 消息保留原文/截图附件，assistant 消息 `metadata_json.candidates` 挂候选。用户在 Web 聊天勾选后 `POST /api/chat/threads/{id}/candidates/commit` 才 upsert + 尽力评分。跳过/不入库时原料仍保留在聊天里。
- **采集也走同一套候选**:`kind="collect"` 线索（`persist_collect_candidates`）挂的是采集回来的全新岗位，带 `score`（`jobs.attach_candidate_scores`，与岗位池 FitScore 同一个 `scoring.score_job`）并按分降序；commit / restore / board-write 端点与候选卡组件**原样复用**，不认线索 kind。晨间日清单的「待筛岗位」段读的就是这些 pending 候选（`daily_digest.pending_candidate_rows`——**同时过滤掉 `hard_blocked`**：Web 候选卡里那些本来就是折叠的，推到手机上和正常岗位并排等于把已判定不要的岗位重新要一次注意力；闸门关闭或历史遗留的那批靠这里兜底）。
- **触发方式≠数据源**:HTTP/Telegram 只是触发方式。新增采集器不需要动 ingest;新增来源识别只在 `classify_links` 加一行。
- **建议与追问(手机端在线回复)**:识别到候选后,`chat_ingest` 调 `services/advice.build_candidate_advice` 为**前 `ingest.advice_max_candidates` 个**候选生成初步建议(优先级/方向/下一步/先问什么),挂在候选的纯 UI 字段 `advice` 上——Web 候选卡结构化展示,Telegram 用 `format_advice_block` 排版后附在回执末尾。开关 `ingest.advice`(默认开);`ai.enabled=false` 时只落规则引擎结论,不做模型调用。判断标准**不新写第二套**:一律复用 `decision_chat` 的 `build_rule_analysis` + `merge_model_analysis`。
  机主在 TG 用 `?` / `？` / `/ask` 开头发消息 = **提问**(`telegram.parse_question`),走 `services/decision_reply.reply_in_thread`(与 Web `POST /messages` 同一函数);回复某条回执提问落回那条 ingest 线索,否则落进固定的「手机提问」通用线程。**不带前缀的消息一律仍按材料处理**——靠内容猜意图会把补充材料吃成提问,等于丢材料。
- **追问的岗位锚点(`decision_reply.resolve_thread_anchor`)**:候选入库前没有 Job 记录、`ChatThread.job_id` 恒为 None,所以岗位事实来自 `chat_ingest.thread_candidates`(该线索已识别的候选,跨 assistant 消息去重累积)。优先级:`thread.job_id` 真实 Job > 指名的候选(`candidate_index`;TG 用 `?2`,Web 用候选卡「问这个」→ `ChatMessageCreate.candidate_index`)> 单候选 > 第一个候选。已 committed 的候选回落到真实 Job;都没有则 `kind="none"`,行为与加锚点前一致。**回答开头必须回显锚点**(`针对 ② …`),否则多候选线索里的结论无法归属。
- **同一岗位多图合并(prior_candidates)**:相册(`media_group_id`)/引用回复补充会被传输层路由到**同一** `kind="ingest"` 线程(`target_thread_id`)。往已有线程追加时,`_persist_ingest_to_chat` 会跨该线程所有 assistant 消息累积并去重出已识别候选,经 `run_ingest(prior_candidates=)` 透传给 `ai.extract_jobs_freeform(prior_candidates=)`,让模型把碎片图(如只有「任职要求」)并进已知岗位、补全字段,而不是把碎片当独立岗位认不出。分组靠传输层结构信号(可靠),合并才用 LLM。去重 key 优先 `canonical_key`,但对「公司未知」的真实标题岗位(如「独立站运营·未知公司」)会退到标题做 key——不能丢。`prior_candidates` 为空时行为与单图完全一致。**注意**:该能力目前仅覆盖 Telegram/`/api/ingest` 通道;Web 聊天只发 `/messages`(决策对话),不驱动 ingest 抽取,是查看/确认入口。
- **Telegram 传输层**(`services/telegram.py`,默认关闭,opt-in):
  - **token 绝不许进日志。** Telegram 要求 token 出现在 URL 路径里，而 httpx 的 `HTTPStatusError` 消息带完整 URL——`raise_for_status()` 抛出后被上层 `logger.warning(..., exc_info=True)` 一记，明文 token 就永久留在 `data/app/backend.log` 里（实测踩到：两个轮询抢同一个 bot 时那批 409 Conflict 日志每行都带着 token）。`get_updates`/`send_message` 的所有异常路径都经 `telegram.redact_token` 重包，并 `raise ... from None` 断开异常链（`from exc` 会让 `exc_info=True` 把 `__cause__` 一起打出来）；连 token 的后半段也一起抹——只泄后半段照样能拼回去。红线 §3.4 不泄密，日志同样算。
  - 长轮询 `getUpdates` 是后端主动**出站**请求 `api.telegram.org`,后端**无需对外暴露端口**;`config.yaml telegram.enabled=true` + `.env` 的 `TELEGRAM_BOT_TOKEN` 才启动(见 `main.py` lifespan 的 `_telegram_poll_loop`)。
  - **只处理白名单 `telegram.allowed_chat_id`(机主本人)的消息**,其余一律忽略。回执**只发机主本人**——符合 §2 的机主回执豁免,绝不发招聘方。
  - 回执文案是「识别到 N 个候选…打开 Web 确认」+ 可选的建议正文,**不声称已入库**;建议只是判断,不改变入库口径。
  - **命令**(`telegram.parse_command`,只认光杆 `/xxx`,带正文的 `/ask 问题` 仍归 `parse_question`;带图的消息一律按材料走,不当命令):`/start` 回使用说明;`/collect` 手动补采一次(跑晨间同一来源 `_DIGEST_COLLECT_SOURCE`),回执按初筛口径报「抓取 / 区域过滤 / 已在池刷新 / 已在待筛 / 硬排除 / 低于分数线 / 超上限暂缓 / 待筛」条数(`collect_run_summary`)+ 待筛岗位段(`daily_digest.build_new_jobs_text`),并提示去 Web 勾选。这是**本人显式触发**的人工采集(红线 §3.3 允许,等价于 Web 上那颗按钮),**不是自动重试**——定时采集失败仍只记日志,绝不自行重跑。补采成功调 `daily_digest.mark_collect_success` 清掉当天的 `collect_note` 并写 `last_collected`(今天已采到,定时那次不必再跑);失败**不写**状态,定时那次照旧还有机会。
