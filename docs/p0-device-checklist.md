# P0 真机联调清单（人工执行，约 20 分钟）

> 目标：手机发 链接 / 文本 / 截图 → Web 聊天出现候选 → 勾选确认入库 / 跳过,全链路走通一次。
> 全程不需要写代码。每步都有「预期」;不符合就跳到底部故障速查。

## 0. 前置配置（一次性）

1. `.env`（项目根,不入 Git）:
   - `OPENAI_API_KEY=...`（截图/自由文本抽取需要）
   - `TELEGRAM_BOT_TOKEN=...`（@BotFather 创建）
   - `JOB_ONE_STOP_CONTEXT_REPO_PATH=` **当前运行 OS** 的绝对路径
     - WSL/Linux: `/mnt/d/006-Overseas` 这种形式
     - Windows: `D:\006-Overseas` 这种形式
2. `config.yaml`:
   - `telegram.enabled: true`
   - `telegram.allowed_chat_id: <你的数字 chat id>`（整数或数字字符串均可;给 bot 发条消息后访问
     `https://api.telegram.org/bot<TOKEN>/getUpdates` 从 `message.chat.id` 读）
   - `ai.enabled: true`（若要测截图/文本抽取）
3. **完整重启后端**（env 变更不能只靠 reload）。

## 1. 健康检查（2 分钟）

| # | 操作 | 预期 |
|---|---|---|
| 1.1 | 打开 `GET /api/diagnostics/deployment` | `database` ok;`context_repo` ok(或明确的缺文件提示);无 error 级条目 |
| 1.2 | Web 侧栏点「测试连接」(`POST /api/ai/test`) | 成功;失败时文案能区分 401(key 错)/404(base_url 或模型错)/429(限流) |

## 2. Telegram 三种输入（10 分钟）

| # | 手机发送 | 预期回执 | 预期 Web |
|---|---|---|---|
| 2.1 | 纯文字 `hello`（无链接无 JD） | 「未从链接、文本或截图中认出岗位。原料已保留…」 | 聊天列表出现「入库候选 · hello」线程,无候选卡 |
| 2.2 | 一条公众号招聘文章链接 | 「识别到 N 个候选岗位,已写入本地聊天(**未入库**)…」 | 线程内出现候选卡,来源=公众号 |
| 2.3 | 复制一段 BOSS JD 文本 | 识别到候选(需 AI 启用;未启用则提示「AI 未启用」) | 候选来源=manual |
| 2.4 | 一张招聘截图(拍屏/截屏) | 同上,走截图抽取 | 截图缩略图保留在聊天里 |

红线自查:每条回执都**只说「未入库」**;此时打开岗位列表,**不应**出现任何新岗位。

## 3. Web 确认入库 / 跳过（5 分钟）

| # | 操作 | 预期 |
|---|---|---|
| 3.1 | 打开 2.2 的线程,勾选 1-2 个候选,点「入库选中」 | 候选变「已入库 · #id」;岗位列表出现新 Job,来源=公众号(不是 Telegram) |
| 3.2 | 打开 2.3 的线程,点「全部跳过」(空选) | 候选全部变「已跳过」;岗位列表**无**新增;原文仍在聊天 |
| 3.3 | 对 3.1 已入库的候选再点一次入库 | 幂等,不重复建 Job |

## 4. 通过标准

- [ ] 1.1–1.2 绿
- [ ] 2.1–2.4 回执与 Web 线程都符合预期,且全程零自动入库
- [ ] 3.1 入库成功且来源正确;3.2 跳过零写入;3.3 幂等
- [ ] 完成后在 `docs/progress-*.md` 记一行结果

## 故障速查

| 症状 | 先查 |
|---|---|
| 手机发消息无任何回执 | `telegram.enabled` 是否 true;`TELEGRAM_BOT_TOKEN` 是否在 `.env`;`allowed_chat_id` 是否你自己的数字 chat id(整数或数字字符串均可);后端是否完整重启;后端日志——启用但配置缺失时会打 `Telegram 已启用但轮询未启动：<原因>`,拉取失败会打 `Telegram getUpdates 失败` |
| 回执「AI 未启用」 | `config.yaml ai.enabled: true` + `.env OPENAI_API_KEY`,重启 |
| 「测试连接」失败 | 按文案分流:401 换 key;404 查 `base_url`/模型名;429 稍后重试 |
| 回执有了但 Web 看不到线程 | 刷新聊天列表;确认前后端连的是同一个 SQLite(`JOB_ONE_STOP_DATABASE_URL`) |
| context_repo 警告 | 路径必须按**当前运行 OS**写绝对路径(WSL 用 `/mnt/d/...`,Windows 用 `D:\...`);缺白名单文件时警告会列出缺哪个 |
| 公众号链接 0 候选 | 文章可能被风控/删除;回执与线程里会带 skipped 原因;换文本/截图路径 |
