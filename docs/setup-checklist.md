# 机主配置待办清单

> 部署方式已从 Docker 切换为**单进程部署模式**(`scripts/app.sh`);Docker 保留作为备用方案(见 [QUICKSTART.md](../QUICKSTART.md) 方式三)。本清单只列「你要补的配置项」和「怎么验证跑起来了」,详细的真机联调步骤见 [docs/p0-device-checklist.md](p0-device-checklist.md)。

## 你要补的

### 1. `.env`(项目根目录,不入 Git,可从 `.env.template` 复制)

| 变量 | 用途 | 从哪拿 |
|---|---|---|
| `OPENAI_API_KEY` | 截图/自由文本抽取候选岗位、面试准备按 JD 定制 | 你的 OpenAI(或兼容协议)服务商控制台 |
| `TELEGRAM_BOT_TOKEN` | 手机发链接/文本/截图入 Telegram 通道 | 找 [@BotFather](https://t.me/BotFather) 创建 bot 获取 |
| `JOB_ONE_STOP_CONTEXT_REPO_PATH` | 个人操作仓库(检查入口/决策规则/画像等只读白名单文件) | **当前运行 OS** 的绝对路径,例如 WSL 下 `/mnt/d/xxx`、Windows 下 `D:\xxx` |

以上都是可选功能;不填对应变量,相关功能自动降级/关闭,不影响核心的岗位管理与评分。

### 2. `config.yaml`

| 配置项 | 值 | 说明 |
|---|---|---|
| `telegram.enabled` | `true` | 启用 Telegram 长轮询(需要先填好 `TELEGRAM_BOT_TOKEN`) |
| `telegram.allowed_chat_id` | 你自己的数字 chat id | 获取方法见 [docs/p0-device-checklist.md](p0-device-checklist.md) 「0. 前置配置」 |
| `ai.enabled` | `true` | 启用 AI 兜底抽取与面试准备定制(需要先填好 `OPENAI_API_KEY`) |

改完 `.env` 或 `config.yaml` 后需要**完整重启**后端(`scripts/app.sh stop && scripts/app.sh start`,或 `update` 会自动重启),`--reload` 热更新和环境变量读取不是一回事。

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

## Docker 试用数据怎么办

Docker 模式的数据存在独立 volume `job_one_stop_data` 里,与本地(单进程部署 / 本地开发模式使用的 `./data/job_one_stop/`)**不互通**。

- 如果 Docker 试用期间没有录入过真实数据(岗位、聊天记录等),可以直接忽略,改用单进程部署即可。
- 如果录入过想保留,可以从容器里把 sqlite 文件拷贝出来,再手动导入到本地路径。示例(容器名按 `docker-compose.yml` 里的 `container_name: one-stop-job`):

```bash
# 容器还在跑时,直接拷贝出来
docker cp one-stop-job:/data/job_one_stop.sqlite3 ./data/job_one_stop/job_one_stop.sqlite3

# 或者容器已经停了,用一次性容器挂载 volume 读出来。
# 注意:compose 会给 volume 加项目名前缀(通常是 one-stop-job_job_one_stop_data),
# 先用 docker volume ls 确认实际名称,再替换下面的 <volume名>:
docker volume ls | grep job_one_stop
docker run --rm -v <volume名>:/data -v "$PWD/data/job_one_stop":/out \
  alpine cp /data/job_one_stop.sqlite3 /out/job_one_stop.sqlite3
```

拷贝前建议先备份本地已有的 `./data/job_one_stop/job_one_stop.sqlite3`,两个数据库不会自动合并,直接覆盖会丢已有本地数据。
