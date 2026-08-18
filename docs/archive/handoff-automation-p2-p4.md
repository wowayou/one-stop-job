# 交接规格：求职自动化 P2 收尾 + P4 半自动触达（2026-08-13）

> 制定：Fable 5（规格与验收）｜执行：Opus 5（或任何按本规格工作的 AI）｜验收：Fable 5 或 GPT 5.6 按 §验收清单逐项核对
> 执行前必读：本仓库 `CLAUDE.md`（架构红线，尤其 §2 不自动化对外动作、§3.3 抓取合规、§3.10 上下文写入唯一通道）

## 已完成状态（不要重做）

- P3 晨间日清单已上线：`services/board_sla.py`（看板日期解析）、`services/daily_digest.py`（组装+新岗位段+补发状态）、`/api/follow-ups/board-sla` 端点、`main.py _daily_digest_loop`（15 分钟轮询+状态文件 `data/job_one_stop/daily_digest_state.json`，每日 08:20，`collect_first: true` 时先采集后推送）。
- P2 采集已打通：`OpenCLIMultiCommandCollector` 4 关键词（独立站运营/海外推广运营/谷歌优化/外贸网站运营 × 目标城市 × limit 30）实测 `fetched 112 / created 93 / updated 19 / skipped []`。OpenCLI 路径必须用 Windows 绝对路径（形如 `<盘符>:\Users\<用户名>\AppData\Roaming\npm\opencli.cmd`，真实值只在本机未入库的 config.yaml 里），WSL 后端经 cmd.exe 代理调用。
- 测试：`tests/test_board_sla.py`（13）+ `tests/test_opencli_multi.py`（3）全过。全量套件存在 **10 个既有失败**（真实 config.yaml/.env 泄漏进测试，与本次改动无关，已用 git stash 对照确认）——不要求修复，但**不得新增失败**。
- Windows 开机自启：`Startup\one-stop-job.vbs` 已建。
- 用户上下文仓库（`/mnt/d/006-Overseas`）：看板 23 已收口铠狮/峰铭达；LOG.md 已有 2026-08-13 条目。

## 任务 A（P2 收尾，约 1h，可立即执行）

1. **验证摘要新岗位段**：`curl -s http://127.0.0.1:8000/api/follow-ups/board-sla` 应返回非空 `new_jobs`（今日刚入库 93 条，26 小时窗口内）且 `digest_text` 含「🆕 新入库岗位」段。若 new_jobs 为空，排查 `collect_new_jobs` 的时间窗比较（naive/aware datetime）。**禁止向 Telegram 发送任何测试消息**（用户已明确反感重复推送；一切验证走本地 API 文本）。
2. **评分噪音抽查**：查看 new_jobs 前 8 名，若明显偏离方向的岗位（纯 Amazon 店铺、纯社媒、国内 SEM）进入前列，在「匹配评分」画像（`GET/PUT /api/profile`，或 Web 匹配评分页字段）补充目标关键词与排除词后重跑一次评分核对。允许结论为「画像未配置导致评分同质化，留给用户在 Web 填画像」，如此则在交接说明里写明。
3. **全量测试**：`.venv/bin/python -m pytest -q`，通过数 ≥ 213、失败 ≤ 10 且失败集合与既有一致（见上）。
4. **文档**：README 数据来源/配置段补 `boss_keywords` 与 `schedule.digest.collect_first` 说明；CLAUDE.md「当前数据源」表 BOSS 行注明多关键词与每日一次定时采集。
5. **用户上下文仓库日志**：向 `/mnt/d/006-Overseas/toolkit/job-pipeline/LOG.md` 的 2026-08-13 小节追加一行：P2 完成（多关键词采集数字 + 摘要并入新岗位 + collect_first）。不改 STATUS.md 的岗位事实。
6. 重启应用（`scripts/app.sh stop && scripts/app.sh start`）使全部改动生效，`/api/health` 200。

## 任务 B（P4 半自动触达，需用户在场演练，可与任务 A 分开）

新建独立目录 `/home/forbackup/Dev/my-projects/job-autopilot`（**不并入 one-stop-job**，保持其宪章干净）：

1. 形态：单个 Python 脚本 + README。读取 `http://127.0.0.1:8000/api/follow-ups/board-sla` 的 `due_send`/`due_follow` 队列，逐条在用户浏览器打开对应 BOSS 岗位页（经宿主机 `opencli browser navigate <url>`，或 `cmd.exe /c start <url>` 兜底），每打开一条等待用户回车确认后再开下一条。
2. 话术不自动填充第一版不做（opencli browser type 有风控风险）；脚本只负责「打开+排队+计数」，把对应卡片的话术路径打印在终端供人复制。
3. 硬编码纪律：单次运行打开上限 10 条；条间随机 8-20 秒延时；检测到页面为验证/风控（navigate 返回异常或用户按 s 跳过）立即终止整批。
4. 不进 Git 的内容：无（脚本无登录态、无隐私）；仍建 `.gitignore` 备用。
5. 验收以一次真实演练为准（用户在场，打开 ≤3 条即可）。

## 验收清单（逐项可独立核对，供 GPT 5.6 或任何验收者使用）

- [ ] `curl -s http://127.0.0.1:8000/api/follow-ups/board-sla | python3 -c "import json,sys;d=json.load(sys.stdin);assert d['new_jobs'],'new_jobs 为空';print('OK', len(d['new_jobs']))"` 输出 OK
- [ ] `digest_text` 包含三段标题（今日必发/今日跟进/今日收口，视看板当日到期情况可少）且包含「🆕 新入库岗位」
- [ ] `.venv/bin/python -m pytest -q` 失败数 ≤ 10 且全部属于既有失败清单（test_prep 5 项、test_ingest 配置回环 1 项、test_write_board 503 1 项、其余 3 项为同类环境泄漏）
- [ ] README 与 CLAUDE.md 含 `boss_keywords`、`collect_first` 的说明；CLAUDE.md 红线 §3.3 保持「每日一次」上限措辞
- [ ] 应用重启后 `data/app/backend.log` 无 ERROR 级新日志；`GET /api/health` 200
- [ ] Telegram 当日没有因验证产生的额外推送（状态文件 last_sent 仍为当日首次值）
- [ ] （任务 B）job-autopilot 演练：打开 ≤3 条、每条等待人工确认、终端显示计数与话术路径、无自动发送任何消息

## 任务 C（评分区分度，无需用户在场）

**问题**：日清单「新入库岗位」前 22 名分数挤在 79.4→76.5（2.9 分区间），排序无信息量。成因：100 分里 60 分（growth/stability/reputation/commute_rest/interview_roi）在缺公司调研数据时取近似常量，只有 role_match(25)+salary_city(15) 真正区分岗位。

**目标**：让前 20 名分数有可用区分度，且不把方向噪音抬进前列。

**做法自选**（先调查 `services/scoring.py` 再决定，允许组合）：
- 调 `config.yaml scoring.weights`：把权重集中到有真实数据的维度（role_match/salary_city），压缩常量维度占比；
- 或在 role_match 内部增加梯度（如命中主线关键词数量、是否命中排除词的软降权），使同类岗位不再同分。

**硬约束**：
- 不虚构公司调研数据、不引入外部请求；
- 不使用 `dealbreakers` 做方向微调（它是硬阻断 ×0.55，会连带误伤"外贸+社媒"复合岗）；
- `scoring.py` 保持 source-agnostic（红线 §3.8）；
- 权重变更必须走 `config.yaml`，并通过既有 `validate_weights` 校验。

**验收**：
- [ ] 给出调整前后当日新岗位 **top-20 分数分布对比**（最高、最低、跨度），跨度从 2.9 提升到 **≥ 10 分**
- [ ] 前 15 名中**没有**纯社媒运营、纯 TikTok 店铺/达人、纯亚马逊店铺、国内百度/360 SEM 类岗位
- [ ] 前 5 名逐条列出并说明为何该岗位应当在前列（对照 24 文件方向层级）
- [ ] `.venv/bin/python -m pytest -q` 失败 ≤ 10 且集合与既有一致（test_ai_client 3 / test_ingest 1 / test_prep 5 / test_write_board 1）
- [ ] 新增至少 1 项测试锁定"同类岗位不再同分"或权重生效逻辑

## 任务 D（看板对账，无需用户在场）

**问题**：日清单「新入库岗位」不与看板对账，已收口公司（如发多维）会作为"新岗位"再次出现，浪费注意力。

**做法**：在 `services/daily_digest.py` 的新岗位段生成前，读取看板（只读，经 `ContextRepository`）建立两个集合：
1. 已结束/归档列出现过的公司 → 这些岗位**从新岗位段剔除**，并在 payload 里单独给一个 `filtered_closed` 计数（摘要文本可用一行说明"已过滤 N 条已收口公司"）；
2. 活跃列（待沟通/已投递/已沟通/面试）出现过的公司 → **保留但标注**「看板已有」。

**公司名匹配边界**（必须明确处理并写进注释与测试）：
- 看板行是"公司 - 岗位 - 薪资"形态，公司名可能是简称（"发多维" vs 岗位库里的"青岛发多维化妆品"）；
- 采用**包含式双向匹配 + 去除常见前后缀**（青岛/山东/有限公司/科技/国际贸易 等）后比较，避免"青岛七联洲际贸易"漏配；
- 明确**不做**模糊相似度（避免误杀不同公司），宁漏配不误杀；漏配只是多显示一条，误杀会丢真机会。

**硬约束**：只读看板（红线 §3.10），不写不改；解析复用 `board_sla` 已有的列/卡片识别，不复制第二套解析器。

**验收**：
- [ ] `curl -s http://127.0.0.1:8000/api/follow-ups/board-sla` 的 `new_jobs` 中**不含**"发多维"（当日岗位库确实采到了它，验证前先确认存在，否则换一个已结束列公司验证）
- [ ] payload 含 `filtered_closed` 计数；`digest_text` 有对应说明行
- [ ] 活跃列公司若出现在新岗位段，条目带「看板已有」标注
- [ ] 单测覆盖：全称 vs 简称匹配成功、不同公司不被误杀、看板不可读时降级为"不过滤"而非报错
- [ ] pytest 失败 ≤ 10 且集合与既有一致

## 边界（违反即验收不通过）

- 不向任何招聘方/第三方发送消息；Telegram 只发机主本人且本次任务内禁止测试推送。
- 不改 `normalizer.py` 的既有键语义；不绕过 `upsert_job_records` 写库。
- 上下文仓库只允许追加 LOG.md 一行；不触碰看板/STATUS/卡片。
- 平台被风控时跳过并记录，不重试、不破解。

## 执行记录：任务 A 第 2 步「评分噪音抽查」结论（2026-08-13）

**结论：不改画像。** 抽查依据是当日 123 条新入库岗位的完整评分排名（非只看前 8）：

- 画像**已配置**（`target_titles` = SEO/网站优化/运营/独立站SEO/外贸SEO，`skills`、`strengths`、`dealbreakers` 均非空），因此「画像未配置导致同质化」这个允许结论**不成立**。
- 规格点名的三类噪音**没有进入前列**，评分器已把它们正确压到尾部：纯社媒（海外社媒运营）第 81/84/91/95/96/97/98 名，60.3-62.7 分；TikTok 店铺/达人/投放第 69-123 名，30.7-65.2 分；亚马逊店铺第 80 名，62.9 分；国内 SEM 类当日未采到。前 22 名全部是独立站／外贸／SEO 运营。
- 前 8 里唯二偏社媒的（星米包装「海外社媒运营｜PS·剪辑·独立站｜外贸B2B」79.3、欧蒂莉丝「独立站海外社媒运营」79.1）标题里确实同时带 独立站 + 外贸，命中 3-4 个角色组属**规则的正确行为**。若为压它们而把「社媒」加进 `dealbreakers`，会连带硬扣第 5/6/7 名（欧科瓦「外贸出口运营 网站SEO 社媒推广」、宝利弗、罗梅森「谷歌独立站社媒外贸运营」）——这些是**在方向上**的岗位，净损失大于收益。且 `dealbreakers` 是硬阻断（总分 ×0.55），不是软降权，不适合做方向微调。
- **真正的遗留问题不是噪音，是头部分数压缩**：前 22 名只散布在 79.4→76.5，2.9 分区间内，排名次序基本无信息量。成因是结构性的——100 分里有 60 分（growth/stability/reputation/commute_rest/interview_roi）在没有公司调研证据时取近似常量默认值，只有 role_match(25) + salary_city(15) 真正区分岗位。要改善得补公司调研数据或调权重，不是补关键词能解决的，**留给用户决策**。

### 附带发现（未修，超出任务 A 范围）

`services/queries.py::score_job_into_db` 每次调用都 `session.add(FitScore(...))` **追加新行**而非 upsert。P3 的 `collect_new_jobs` 会对窗口内每条新岗位调用它，因此**每请求一次 `/api/follow-ups/board-sla` 就新增约 123 行 `fit_scores`**（当前 223 个 job_id 对应 346 行）。晨间推送每天至少触发一次，长期会持续膨胀。修它要决定 `FitScore` 到底是历史流水还是当前快照（影响其他消费端），已超出本次收尾范围，单独提给用户。

