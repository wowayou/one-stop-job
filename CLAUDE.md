# CLAUDE.md — job-one-stop 项目宪章

> 本地优先的个人求职助手:自动发现岗位、沉淀公司调研、按个人画像评分、生成面试准备。
> 单用户、本地运行、不做账号体系、**不自动投递、不自动发消息**。
> 本文件是 AI 与人类共同遵守的**架构标准与红线**,任何改动都必须先读它。修改架构前先更新本文件。

> Phase 0 集成边界：`JOB_ONE_STOP_CONTEXT_REPO_PATH` 可指向独立的个人操作仓库。应用只允许通过 `ContextRepository` 读取白名单 Markdown；在实现“写入建议 + diff 复核 + 本人确认”之前，不得写入该仓库。

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
- `backend/app/main.py` — 薄路由 + `_run_collector` / `_run_wechat_collection` 生命周期封装
- `backend/app/models.py` — 表结构(见红线 §3.5)，含 `JobSourceLink` 来源证据表

---

## 2. 新增一个岗位来源(标准配方,照抄)

1. **解析细节放 `services/<source>.py`**:抓取(统一用 `httpx`,带超时/UA/限速)+ 解析成 dict 列表。键用规范化器认识的名字:`title / company_name / url / salary_text / city / area / experience / degree / skills / description / recruiter`(其余字段交给 `normalize_record`)。
2. **在 `collectors.py` 加 `<Source>Collector`**(`@dataclass`,实现 `collect() -> list[dict]`):取数 → 解析 → 逐条 `normalize_record(raw, source="<中文来源名>")` →(一文多岗时覆写 `external_id`)→ 去重。维护 `self.report = {urls_total, urls_ok, jobs, skipped:[{url,reason}]}`。
3. **配置放 `config.yaml` 的 `<source>:` 段**,在 `config.py` 加 `<source>_config` 属性读取;**密钥只进 `.env`**。
4. **加端点**:
   - 配置驱动(类似 BOSS/beBee):用 `_run_collector(session, source_label, collector)`。
   - 粘贴/外部输入驱动(类似公众号):参考 `_run_wechat_collection`。
   - 端点负责建 `SourceRun`、跑采集器、`upsert_job_records`、把 `collector.report` 写进 `SourceRun.raw_config`。失败置 `status="failed"` + `error`,**绝不抛裸异常给前端**。
5. **前端**:岗位带上新 `source` 会自动出现在表格「来源」列与来源筛选;如需主动触发,在 topbar 加一个按钮调用对应端点(照搬 `runBossCollection` / `collectWeChat` 模式)。视图层不得写来源特判逻辑。
6. **测试必须有**(见 §4):解析器纯函数测试 + 端点流程测试(`monkeypatch` 掉网络抓取,不联网)。
7. **更新文档**:README 加来源说明,必要时更新本文件的"当前数据源"。

---

## 3. 红线(硬性,不可逾越)

1. **本地优先 / 单用户**:无多用户、无登录体系;数据只存本机 SQLite。
2. **不自动化对外动作**:不自动投递、不自动发消息、不自动外发任何联系方式。抓到的招聘人微信/电话/邮箱**仅本地留存供查看**。
3. **抓取合规**:只抓**公开**内容;低频、人工触发;**不破解验证码 / 风控页 / 付费墙**;尊重 robots 与各平台 ToS;**不二次分发**抓到的内容。被风控拦截就跳过并记录原因,不硬刚。
4. **不泄密**:`.env`、`*.sqlite3`、`data/`、日志、`*.xlsx`、登录态(`.yuanbao/`、`*storage_state*`)一律不提交;改 `.gitignore` 前先确认不会带出隐私。
5. **不用 `create_all` 偷改表结构**:`init_db()` 走 `SQLModel.metadata.create_all`——**新增表 OK,但给现有表加列不会自动迁移**。优先复用 `Job` 现有字段;确需加列/改列,写显式 alembic 迁移并在 PR 说明,不可假设旧库会自动升级。
6. **管线唯一**:新增来源必须经 `normalizer` + `importer`,不得绕过;不得在采集器里直接 `session.add(Job(...))`。
7. **不静默丢数据**:解析/抓取失败要进 `report.skipped` 带原因;一篇都拆不出时兜底产出至少 1 条,而不是返回空。
8. **来源解耦**:`scoring.py` / `prep.py` / 前端视图保持 source-agnostic,禁止出现 `if source == "xxx"` 的业务特判。
9. **网络访问统一封装**:一律走 `httpx`,带超时、移动端 UA、限速;不在路由函数里裸发请求。重依赖(如 `playwright`)放 `requirements-automation.txt` 并**延迟 import**,默认关闭。
10. **外部上下文只读**:`JOB_ONE_STOP_CONTEXT_REPO_PATH` 指向的仓库不是应用数据库。Phase 0 只能读取 `ContextRepository` 白名单文件，不得创建、修改、移动或删除其中任何文件，也不得把宿主机绝对路径返回 API。
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

- 后端:FastAPI + SQLModel;逻辑在 `services/`,`main.py` 只放薄路由 + 生命周期封装。
- 前端:React + Vite + TS;复用 `src/api.ts` 的 `api()/jsonBody()` 与既有 CSS 类(`modal`/`primary-action`/`icon-button`/`source-select` 等),不引重组件库。
- 配置:`config.yaml`(每来源一段 + `scoring`/`followup` 等功能段)+ `.env`(密钥)。AI 走 OpenAI 兼容协议(`OPENAI_API_KEY`/`OPENAI_BASE_URL`),`ai.enabled` 默认关;启用后既做公众号 LLM 兜底抽取,也做面试准备按 JD 定制(`ai.tailor_interview_prep_llm`),不可用/失败时逐键回退 `prep.py` 模板。`followup.stale_days` 控制 fit/interview 岗位多少天无活动算「需跟进」(`services/followup.py`,source-agnostic)。
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
