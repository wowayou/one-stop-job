# Testing System

本项目按“越底层越自动、越上层越贴近真实使用”的方式组织测试。目标不是追求测试数量，而是每次改动都能快速发现：数据管线坏了、API 坏了、前端构建坏了、迁移坏了、关键交互闭环断了。

## 一键门禁

WSL 内运行：

```bash
scripts/quality_gate.sh
```

Windows 双击：

```text
run_quality_check.bat
```

当前门禁包含：

- Shell 脚本语法检查：`bash -n scripts/dev_wsl.sh`、`bash -n scripts/system_smoke.sh`
- 后端测试：`.venv/bin/python -m pytest -q`
- 前端类型与构建：`cd frontend && npm run build`
- 系统 HTTP 冒烟：`scripts/system_smoke.sh`
- 旧库迁移烟测：临时旧版 SQLite `jobs` 表升级到 Alembic head，并验证 `job_source_links` 与 `canonical_key` 回填。

Docker 构建不强制纳入一键门禁，因为本地/CI 可能没有 Docker 或没有网络构建镜像。每次改 Dockerfile、compose 或 bat 启停逻辑后，额外手动跑：

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/api/health
docker compose down
```

本地压力冒烟不纳入默认门禁，避免拉长每次提交耗时。需要评估导入、列表、并发评分和冲刺包性能时运行：

```bash
scripts/load_smoke.sh
JOBS=1000 CONCURRENCY=12 scripts/load_smoke.sh
```

压力冒烟使用临时 SQLite 和本地 Uvicorn，不访问真实招聘平台。

聊天 / ingest 面另有一条压测（同样不纳入默认门禁）。改完聊天、ingest 落盘、追问锚点相关代码后跑一遍：

```bash
scripts/chat_stress.sh
ROUNDS=300 CONCURRENCY=16 scripts/chat_stress.sh
```

## 测试分层

### 1. 纯函数 / 解析器测试

放在 `tests/test_normalizer.py`、`tests/test_wechat.py`、`tests/test_bebee.py` 等。

覆盖：

- 字段映射、城市/区域、薪资、发布时间、招聘状态解析。
- 公众号 / beBee HTML fixture 解析。
- 多岗位拆分和 `external_id` 唯一性。
- OpenCLI 配置检测、命令代理与通用来源状态。

适用场景：新增来源、新增字段、解析规则调整。

### 2. 入库与去重测试

放在 `tests/test_importer.py`。

覆盖：

- `normalizer -> importer -> SQLite` 主链路。
- `JobSourceLink` 来源证据。
- 跨来源 canonical 去重。
- JSON payload 是否可序列化。

适用场景：改模型、去重策略、导入逻辑、来源链接逻辑。

### 3. API 流程测试

放在 `tests/test_api.py`，统一使用 `httpx.AsyncClient + ASGITransport`，不要退回 `fastapi.testclient.TestClient`。

覆盖：

- 岗位新增、评分、准备包。
- 公众号 / beBee 采集端点。
- 通用来源列表与禁用来源保护。
- 冲刺包生成任务。
- 跟进任务创建、更新、删除。
- AI 状态接口不泄露密钥，AI 环境变量不改变规则评分。

适用场景：改路由、schema、业务闭环。

### 4. 前端类型与构建

命令：

```bash
cd frontend
npm run build
```

覆盖：

- TypeScript 类型。
- Vite 构建。
- API 类型字段变更是否同步到前端。

适用场景：任何前端组件、类型、API 响应字段调整。

### 5. 迁移烟测

由 `scripts/quality_gate.sh` 自动执行。

覆盖：

- 旧 SQLite schema 能升级。
- 新字段和新表能回填。

适用场景：任何 `backend/app/models.py` 的现有表字段变更。

### 6. 系统 HTTP 冒烟

命令：

```bash
scripts/system_smoke.sh
```

执行方式：

- 自动选择空闲本地端口启动真实 Uvicorn 后端。
- 使用临时 SQLite，退出后删除；不会读写 `data/job_one_stop` 的真实数据。
- 通过 HTTP 覆盖健康检查、AI 状态、岗位新增、状态回退、CSV 导入去重、来源筛选、公司更新、调研证据、画像更新、评分、面试准备、草稿、跟进任务 CRUD、冲刺包、公众号手动正文导入和采集记录。

适用场景：大改 API、数据流、导入去重、任务闭环、启动生命周期后，确认真实服务形态没有断。

### 7. 功能测试矩阵

| 模块 | 自动测试 | 系统冒烟 | 手动检查 |
|---|---|---|---|
| 启动/停止 | `bash -n scripts/dev_wsl.sh` | Uvicorn 临时启动和健康检查 | `start_app.bat` / `status_app.bat` / `stop_app.bat`，并确认 `http://127.0.0.1:8000/` 随容器启停 |
| 岗位池 | `test_api.py`、`test_importer.py` | 新增、导入、去重、来源筛选、状态回退、批量更新 | 搜索、筛选、页码跳转、批量状态、抽屉打开/关闭 |
| 公司调研 | `test_api.py` | 公司更新、证据新增、公司详情 | 公司分页、从公司打开岗位 |
| 匹配评分 | `test_scoring.py`、`test_api.py` | 画像更新、评分生成 | 排序队列、硬阻断展示、个人画像同高滚动 |
| 面试准备 | `test_api.py` | 准备包、核心优势话术、沟通草稿和对应简历生成 | 准备队列内滚动、素材可读可复制 |
| 跟进任务 | `test_api.py` | 创建、完成、删除 | 新增、改标题、改日期、完成/重开、打开关联岗位 |
| 采集来源 | `test_collectors.py`、`test_api.py` | 来源状态列表 | 系统配置页来源卡、容器模式提示宿主机采集、禁用智联 |
| 公众号 / 元宝 | `test_wechat.py`、`test_api.py` | 手动正文导入，不联网 | 粘贴真实链接时的错误提示和 skipped 原因 |
| beBee | `test_bebee.py`、`test_api.py` | 不联网，靠单测 fixture 覆盖 | 真实页面首次接入时核对 skipped/HTML 样例 |
| 迁移 | `quality_gate.sh` | 不覆盖 | 旧库升级后真实数据抽查 |
| 前端 | `npm run build` | 不覆盖浏览器交互 | 真实浏览器完整冒烟 |

### 8. 压力冒烟

`scripts/load_smoke.sh` 覆盖：

- 生成指定数量的 CSV 岗位并导入。
- 读取岗位列表。
- 并发生成一批评分。
- 生成今日冲刺包。
- 用固定耗时预算做失败判断。

默认参数：

```text
JOBS=300
CONCURRENCY=8
```

`scripts/chat_stress.sh` 覆盖另一条主干（聊天入口），分两个阶段：

HTTP 阶段（临时 SQLite + 本地 Uvicorn，AI 关闭）：

- 单条线程连发 N 次追问，看耗时是否随线程变长而退化。
- ingest 线索里反复追问（每次都要重算候选锚点）。
- 建 60 条线索后的落盘与列表耗时。
- 并发混合读写，抓 `database is locked` 和 5xx。
- 边界与恶意输入（超长/超限文本、坏 data URL、控制字符、越界 `candidate_index`），确认既不 500 也不把空请求放行。

进程内阶段（Telegram 专属路径 HTTP 打不到；规则模式下也拆不出多候选，需直接合成）：

- `reply_in_thread` 在 3000 条消息的线程里的耗时倍数。
- `_find_ingest_thread_by_receipt` 随聊天记录总量的耗时。
- 多候选下指名 `?N` 的锚点正确性，含脏数据、去重、越界回落。
- 候选字段异常（超长/None/错类型）不炸锚点与回答。
- 建议正文长度 vs Telegram 4000 字符截断点。

默认参数：

```text
ROUNDS=150
CONCURRENCY=12
```

**计时类断言只是趋势看板，不是守门人。** 真正钉住「热路径不得无上界」的是 `tests/test_ingest.py`
里的 `test_reply_in_thread_reads_a_bounded_history_window` 和 `test_receipt_lookup_scans_a_bounded_window`
（确定性断言、零计时）。脚本里的阈值经过正反两向校准：把对应修复回退掉，场景 6 会从 ×1.7 跳到
×8.3 并失败；场景 7 的计时在几千条消息量级分辨不出来，注释里已写明它只兜灾难级回归。

### 9. 手动冒烟清单

每次大改交互后，用真实浏览器检查：

1. `start_app.bat` 启动，`status_app.bat` 显示 app 容器 running；代码更新后用 `rebuild_app.bat` 验证强制重建。
2. 打开 `http://127.0.0.1:8000/`。
3. 岗位池：搜索、来源筛选、状态筛选、10 条分页、页码跳转、表头固定、批量状态/收藏、打开岗位。
4. 岗位抽屉：状态可逆切换、收藏、跟进、评分、准备、点击空白关闭。
5. 公司调研：公司列表 10 条分页，打开公司岗位。
6. 匹配评分：排序队列在面板内滚动。
7. 面试准备：准备队列在面板内滚动，右侧草稿不被拉到页面底部。
8. 跟进任务：新增、改标题、改截止日期、完成、重开、删除、打开关联岗位。
9. 顶部指标卡：岗位总数 / 待调研 / 高潜岗位 / 最高分 / 草稿能跳到对应视图。
10. `stop_app.bat` 后 `status_app.bat` 不再显示运行容器，`http://127.0.0.1:8000/` 不应继续访问到旧前端。
11. 系统配置：来源卡显示 BOSS / beBee / 智联模板状态；Docker 模式下 BOSS/智联提示宿主机采集导入，智联默认禁用。
12. 使用指南：首次进入自动展示一次；关闭后顶栏信息按钮仍可重新打开。
13. 宿主机采集只做人工低频验证，不并发跑多个平台；BOSS/智联登录态过期时先在浏览器重新登录。

### 10. 决策聊天手动验收

AI 可以保持未配置，本节先验证本地规则与交互：

1. 打开 `http://127.0.0.1:5173/`（本地开发）或 `http://127.0.0.1:8000/`（Docker），进入“聊天”。确认聊天页不再显示采集/导入工具栏，主要空间留给消息。
2. 新建通用聊天，发送“这个机会值不值得继续了解？”。点击发送后，自己的消息应立即出现并显示“已发送”，随后出现规则建议。
3. 点击标题旁的铅笔，改名并保存。刷新页面后，新名称应保留，并同步显示在左侧会话列表。
4. 在“可发送草稿”点击“复制”。按钮应立即变为“已复制”；粘贴到记事本核对正文。拒绝浏览器剪贴板权限时应显示“复制失败”。
5. 用系统截图工具把一张 PNG/JPEG/WebP 截图放入剪贴板，聚焦输入框后按 `Ctrl+V`。发送前应出现缩略图和文件名；点击移除后缩略图应消失。
6. 仅粘贴截图、不输入文字并发送。应自动使用“请分析这张截图。”，消息中可查看截图；单张截图不得超过 4 MB。
7. 输入多行文字，用 `Shift+Enter` 换行、`Enter` 发送。分析期间不能重复发送或切换会话，失败后文字和截图应回到输入区。
8. 新建岗位专属聊天，确认左侧只复用同一岗位会话，“查看岗位”能打开对应岗位详情。
9. 把窗口缩到约 620px 宽，确认会话列表转到消息区上方，标题、输入框、发送按钮没有横向溢出。

本地数据库在首次成功启动后才创建，默认位置为 `data/job_one_stop/job_one_stop.sqlite3`。备份命令应在后端完成首次启动后执行；Docker 数据则在 `job_one_stop_data` volume 中，不在这个路径。

## 后续可扩展

- Playwright 端到端测试：覆盖浏览器中的关键交互闭环，适合 UI 稳定后加入。
- 视觉回归截图：对岗位池、公司调研、评分队列、准备队列、任务页做固定视口截图比较。
- 采集 fixture 回放：把真实页面 HTML 样例放入 `tests/fixtures/`，所有来源解析必须离线可测。
