# 红线与架构审计 · 2026-07-21

> 分支：`codex/read-only-chat-mvp`（基线 `09d2454`）· 环境：Ubuntu 26.04，全新 clone
> 方法：静态审计全部入库路径 + 运行完整测试（基线 106 passed）+ 对照私有仓库 roadmap（29）
> 结论：**未发现自动入库回归，无红线违背，无阻塞 bug。可直接进入 P0 真机联调。**

## 1. 自动入库红线逐条核对

核心红线（CLAUDE.md §2）：**ingest 默认不写 Job 表，用户在聊天里点「入库选中」才 upsert。**

| 检查项 | 结论 | 证据 |
|---|---|---|
| `run_ingest` 不写库 | ✅ | `services/ingest.py` 无 Session 写入路径，只返回 `candidates`（`status=pending`） |
| `/api/ingest` 不写 Job | ✅ | `_persist_ingest_to_chat` 只写 `ChatThread` / `ChatMessage`（main.py:1602） |
| Telegram 轮询不写 Job | ✅ | `_telegram_poll_loop` 只调 `_persist_ingest_to_chat`；回执文案「未入库」 |
| Telegram 白名单 | ✅ | `chat_id != allowed_chat_id` 直接丢弃（main.py:129）；回执只发白名单 chat |
| 全仓库 upsert 调用点 | ✅ 仅 4 处，全部人工显式触发 | `/api/jobs/import`（上传文件）、`_run_collector`（Web 按钮跑 BOSS/beBee）、`/api/collect/wechat`（粘贴）、`candidates/commit`（勾选确认） |
| 采集器不直接写库 | ✅ | `collectors.py` / `wechat.py` / `bebee.py` / `ai.py` 零 session/importer 引用 |
| commit 端点语义 | ✅ | 显式 `indexes`；空数组 = 全部跳过；越界 400；已 committed 幂等跳过 |
| 前端无自动提交 | ✅ | `CandidateListCard` 必须点「入库选中」按钮；checkbox 只是预选 |
| 无 AI 自动过滤残留 | ✅ | 全仓库无 `worth_storing` / `auto_commit` 引用（锁定决策 #1 成立） |
| 输入校验 | ✅ | `IngestRequest` / `ChatMessageCreate` 均校验 data URL 白名单（png/jpeg/webp）+ base64；附件读取有文件名正则 + 路径限制 |

## 2. 外部上下文只读红线（CLAUDE.md §10）

| 检查项 | 结论 | 证据 |
|---|---|---|
| 只读、无写方法 | ✅ | `ContextRepository` 无任何写接口 |
| 白名单 + 越界防护 | ✅ | 固定 `CORE_DOCUMENTS` 白名单；`_resolve` 用 `resolve()` + `relative_to(root)` 拦截越界与符号链接逃逸；岗位卡名称校验拒绝路径分隔符 |
| 绝对路径不出 API | ✅ | diagnostics 的 `context_repo` 检查只回状态与 message，不回显路径 |

备注（可接受，不改）：Telegram 处理失败时回执 `str(exc)` 可能含内部细节，但只发机主本人白名单 chat，符合 §2 例外；若未来放宽白名单需同步收紧。

## 3. 隐私与仓库卫生

- `.gitignore` 覆盖 `.env`、`*.sqlite3`、`data/`、`*.xlsx`、`.yuanbao/`、`*storage_state*.json`。✅
- 聊天截图落 `data/chat_attachments/`（gitignore 内），SQLite 只存随机附件 ID。✅
- 本次审计新增文档不含任何真实个人信息、路径或密钥。✅

## 4. 新增红线绊线测试（本次代码改动之一，仅测试）

`tests/test_ingest.py` 新增 2 个绊线：

1. `test_ingest_and_telegram_modules_never_import_importer` — AST 级检查 `ingest.py` / `telegram.py` 的 import：出现 `importer` / `upsert_*` 立即翻红。
2. `test_persist_ingest_and_poll_loop_write_chat_only` — `_persist_ingest_to_chat` 与 `_telegram_poll_loop` 源码不得出现 `upsert` 或直接构造 `Job(`。

意图：把「不要把 ingest 改回自动 upsert」从交接文档口头约定升级为 CI 可执行约束。想推翻必须先改产品决策 + CLAUDE.md + 这两个测试，三处同时动，藏不住。

## 4b. 审计中发现并修复的阻塞性小 bug（行为修复，非功能扩张）

| 问题 | 影响 | 修复 |
|---|---|---|
| `CONFIG_TOP_LEVEL_ALLOWLIST` 漏了 `telegram` | `GET /api/config` → `PUT` 回环 400，Web 设置保存与 `scripts/system_smoke.sh` 双双被卡（质量门禁在基线上就是红的） | main.py:183 白名单加 `telegram`；新增回环测试 `test_config_roundtrip_keeps_telegram_section` |
| `allowed_chat_id` 类型不容错 | `config.yaml` 注释引导写字符串，但轮询要求 `isinstance(int)`——写成 `"12345"` 时**静默不启动**，真机联调只会看到「无回执」 | 轮询接受整数或数字字符串；`enabled=true` 但缺 token/chat id 时打出明确 `logger.warning`；新增测试 `test_poll_loop_accepts_numeric_string_chat_id`；config.yaml 注释同步 |
| 轮询读 `poll_timeout`，config.yaml 写 `poll_timeout_seconds` | 键名不一致，用户改超时不生效（当前值恰好等于默认 30 才没暴露） | 兼容读取 `poll_timeout_seconds`（优先）与 `poll_timeout` |

三处修复均不改变「候选→人工确认→入库」语义；白名单/回执红线不受影响（token 仍只进 `.env`，敏感键拦截 `_contains_sensitive_key` 继续生效）。

## 5. 对照私有 roadmap（29）的架构评估

私有仓库 roadmap 的关键分工：**Markdown = 唯一长期事实源；SQLite = 运行时工作区（聊天、候选、附件索引、派生评分、待确认写入建议）**。

当前分支与之一致的点：

- 聊天是默认入口，候选挂在聊天消息上（无 Draft 表、无独立候选表）；
- 规则先行、AI 失败降级（三态标记）；
- 旧评分只在 commit 后「尽力评分」，仅作展示/排序辅助，不做入库门槛；
- 不自动对外动作；Telegram 仅传输层 + 机主回执；
- Phase 0 只读 `ContextRepository`，无任何写回路径 —— roadmap Phase 2 的 `WriteProposal`（写入预览 + 本人确认 + diff 审计）尚未开始，符合阶段顺序。

**Watchpoints（Phase 2 之前不要动，动之前先回读 roadmap）：**

1. **`Job.status` 不得演化成第二求职状态事实源。** commit 产生的 Job 行是运行时投影；投递/面试/offer 的状态推进要等 Phase 2 的 Markdown 写入建议机制，不要先在 SQLite 里单方面记录决策，否则形成双事实源（roadmap 明确列为失败模式）。
2. **评分仅排序辅助。** 不要把 FitScore 变成过滤或自动决策输入。
3. **BOSS / 元宝 / beBee 主动采集端点保留但不是 MVP 主干。** roadmap 到 Phase 5 才逐源验证；当前粘贴/截图/链接 ingest 优先，够用。
4. **聊天隐私增强（本条不保存/只保存摘要/会话删除/附件清理）是 roadmap 已知欠账**，属于后续隐私增强，不阻塞 P0。
5. **多设备/公网暴露继续推迟**（长轮询无需暴露端口，维持现状）。

## 6. 结论

- 代码改动：2 个绊线测试 + §4b 的 3 处阻塞性小修（含 2 个配套测试）；无功能扩张、无红线语义变化。
- 测试：全量 **110 passed**（基线 106 + 4）；`scripts/quality_gate.sh` **全绿**（基线上 System Smoke 是红的，修复后通过）。
- 下一步：人工 P0 真机联调，见 `docs/p0-device-checklist.md`。
