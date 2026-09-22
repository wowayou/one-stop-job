# 机主配置待办清单

> 部署方式是**单进程部署模式**(`scripts/app.sh`);不装开发环境就用桌面安装包(见 [QUICKSTART.md](../QUICKSTART.md) 方式三)。本清单只列「你要补的配置项」和「怎么验证跑起来了」,详细的真机联调步骤见 [docs/p0-device-checklist.md](p0-device-checklist.md)。

## 你要补的

### 1. `.env`(项目根目录,不入 Git,可从 `.env.template` 复制)

| 变量 | 用途 | 从哪拿 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 手机发链接/文本/截图入 Telegram 通道 | 找 [@BotFather](https://t.me/BotFather) 创建 bot 获取 |
| `JOB_ONE_STOP_CONTEXT_REPO_PATH` | 个人操作仓库(检查入口/决策规则/画像等只读白名单文件) | **当前运行 OS** 的绝对路径,例如 WSL 下 `/mnt/d/xxx`、Windows 下 `D:\xxx` |

AI 的 API Key **不建议**直接手改 `.env`——见下面「AI 配置」走设置页,更不容易出错;两种方式最终都是写同一个 `.env`。以上都是可选功能;不配置,相关功能自动降级/关闭,不影响核心的岗位管理与评分。

### 2. `config.yaml`

| 配置项 | 值 | 说明 |
|---|---|---|
| `telegram.enabled` | `true` | 启用 Telegram 长轮询(需要先填好 `TELEGRAM_BOT_TOKEN`) |
| `telegram.allowed_chat_id` | 你自己的数字 chat id | 获取方法见 [docs/p0-device-checklist.md](p0-device-checklist.md) 「0. 前置配置」 |
| `ai.enabled` | `true` | 启用 AI 兜底抽取与面试准备定制(需要先配好至少一个 Provider,见下面「AI 配置」) |

改完 `telegram.*` 等 `config.yaml` 项或手动改的 `.env` 后需要**完整重启**后端(`scripts/app.sh stop && scripts/app.sh start`,或 `update` 会自动重启),`--reload` 热更新和环境变量读取不是一回事;但通过设置页保存 AI Provider/Key 是例外,单进程部署模式下同进程内即时生效,不需要重启（见下方「AI 配置」）。

### 3. AI 配置(可选,推荐走设置页)

1. 启动后打开 `http://127.0.0.1:8000/`(或 `--reload` 模式下的前端地址),进入「设置 → AI」。
2. 点「添加 Provider」,弹窗里填:
   - 名称(可选,自己认得就行)
   - Base URL:国内可用示例(阿里百炼 Qwen,兼容 OpenAI 协议)填 `https://dashscope.aliyuncs.com/compatible-mode/v1`
   - Model:视觉任务(截图分析)填 `qwen-vl-max`,纯文本任务填 `qwen-plus`
   - API Key:从服务商控制台复制(如阿里云 DashScope)
3. 点「保存」——Key 只写入本机 `.env`,界面不会回显已保存的 Key,只显示「已配置/未配置」徽标。
4. 回到「设置 → AI」勾选「启用 AI 兜底」并保存。
5. 点「测试连接」验证真的能调用成功(不是只看 Key 字符串是否存在)。

需要多个 Provider 做容错时重复上面步骤添加多张卡,列表会按顺序尝试,失败退避重试后换下一张。

## 启动与验证

```bash
scripts/app.sh start
```

1. 打开 `http://127.0.0.1:8000/`,应正常展示应用首页(不是空白或报错)。
2. 打开 `GET /api/diagnostics/deployment`,`database` / `context_repo` 等条目应为 `ok`(未配置 `JOB_ONE_STOP_CONTEXT_REPO_PATH` 时 `context_repo` 会给出明确提示,不算失败)。
3. 常用命令:

```bash
scripts/app.sh status  # 进程 + 健康检查
scripts/app.sh logs    # 跟踪日志
scripts/app.sh stop    # 停止
scripts/app.sh backup  # 备份 SQLite + 聊天附件到 data/backups/
```

## 之后做什么

配置完 Telegram/AI 后,按 [docs/p0-device-checklist.md](p0-device-checklist.md) 走一遍真机联调(手机发链接/文本/截图 → Web 聊天出候选 → 勾选入库,约 20 分钟),确认全链路符合预期,尤其是「零自动入库」「回执只发本人」这两条红线。

配置了 `JOB_ONE_STOP_CONTEXT_REPO_PATH` 后,聊天里确认入库的候选岗位卡上会多一个「写入看板」按钮:点击前先看到将要写入的那一行预览,点击后才把这一行追加到个人看板的「收集箱」列,不点不写一字节。

## 旧 Docker 卷里的数据怎么取出来

Docker 部署方式已于 2026-09-05 移除。**删掉配置文件不会删卷**——`docker volume rm` 才会,所以历史数据仍留在原处。
只有当年真的用 Docker 录入过岗位/聊天记录时才需要这一节;没用过直接跳过。

```bash
# 1. 确认卷还在（compose 会加项目名前缀）
docker volume ls | grep job_one_stop

# 2. 先备份本地现有库——两个数据库不会自动合并,直接覆盖会丢现有数据
cp ./data/job_one_stop/job_one_stop.sqlite3 ./data/job_one_stop/job_one_stop.sqlite3.bak

# 3. 用一次性容器把库读出来
docker run --rm -v one-stop-job_job_one_stop_data:/data -v "$PWD/data/job_one_stop":/out \
  alpine cp /data/job_one_stop.sqlite3 /out/from-docker.sqlite3
```

取出的是完整的 sqlite 文件。要用它替换现有库就停掉后端再改名;想保留两边就只从里面挑数据。
确认不再需要之后 `docker volume rm one-stop-job_job_one_stop_data` 回收空间(**不可逆**)。
