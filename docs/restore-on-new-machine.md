# 换机 / 重装还原清单

代码在 Git 里，**配置和数据不在**（`config.yaml`、`.env`、`data/` 都不入库）。所以还原 = 拉代码 + 重建这几样。按顺序做，每步都有验证方法。

前置：新机器已装 WSL（本文按 Ubuntu）、Node（给 OpenCLI）、Python 3.12+。

## 1. 代码与依赖

```bash
git clone <你的仓库地址> one-stop-job && cd one-stop-job
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

验证：`.venv/bin/python -m pytest -q` 全绿（当前基线 245 passed）。测试不联网、不读你的真实配置，所以这一步在配任何东西之前就该通过。

## 2. `.env`

```bash
cp .env.template .env
```

按需填这几项（都可选，缺了只是对应功能关闭）：

| 变量 | 用途 | 缺了会怎样 |
|---|---|---|
| `OPENAI_API_KEY` 或 `ai.providers` 对应的 `*_KEY_*` | 决策聊天、截图抽取、面试准备定制 | 退化为规则引擎/模板，主流程仍可用 |
| `TELEGRAM_BOT_TOKEN` | 手机入库、晨间日清单推送 | 这两个功能整体关闭 |
| `JOB_ONE_STOP_CONTEXT_REPO_PATH` | 读个人看板/决策规则/画像 | 决策聊天少了个人规则；**晨间日清单直接不可用** |
| `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` | 出站访问 `api.telegram.org` | 国内网络下长轮询与推送全部失败，日志报 `[Errno 101] Network is unreachable` |

`JOB_ONE_STOP_CONTEXT_REPO_PATH` 必须写**当前运行环境**能识别的绝对路径：WSL 里是 `/mnt/<盘符>/<目录>`，Windows 上是 `<盘符>:\<目录>`。同一份 .env 在两边通用（代码会把盘符路径转成 `/mnt/...`）。

代理为什么必须进 `.env` 而不是靠 shell：自启是 Windows 启动文件夹的 `.bat` → `wsl.exe` 拉起的**非登录 shell**，`~/.profile` 里的 `proxy_on` 根本不会执行，进程里一个代理变量都没有。`.env` 由 `config.py` 的 `load_dotenv` 灌进 `os.environ`，httpx 自动采用，跟进程怎么启动无关。`NO_PROXY` 里保留国内域名（`aliyuncs.com,deepseek.com,qq.com,zhipin.com`），别让 AI 与公众号绕代理。

验证（不发任何消息）：`curl -s -x "$HTTPS_PROXY" "https://api.telegram.org/bot<token>/getMe"` 返回 `"ok":true`。

验证：`curl -s http://127.0.0.1:8000/api/context/status`（启动后）应返回 `"available": true` 且四个白名单文件全为 true。

## 3. `config.yaml`

```bash
cp config.example.yaml config.yaml
```

必须改的位置（示例文件里都有注释标注）：

- `opencli.boss_cmd`：把 `示例岗位 / 示例市 / 8-20k` 换成真实值；**WSL 场景下第一项要写 Windows 绝对路径**（见下一步），不能只写 `opencli`。
- `opencli.boss_keywords`：取消注释并填真实关键词列表（一个方向常挂在多个标签下，单关键词会漏掉大半岗位池）。
- `telegram.enabled` + `allowed_chat_id`：chat id 找 `@userinfobot` 查自己的数字 id。
- `schedule.digest.enabled`：要晨间日清单就开；`collect_first` 决定是否先采集再推送。
- `scoring.weights`：按自己的取舍调；改完 `PUT /api/config` 会校验。

## 4. OpenCLI（BOSS 采集，可选但推荐）

⚠️ **上游仓库 `KeJunMao/openreader` 已 404**（2026-08 核验），装不到新版本了。当前可用版本是 1.7.1，**换机前先备份**：

```bash
# 老机器上（Windows 侧）
cmd.exe /c "where opencli"        # 看装在哪
cmd.exe /c "opencli --version"    # 记下版本号
```

把整个 npm 全局目录里的 opencli 相关文件拷走，或在新机上试 `npm i -g opencli@1.7.1`（若源仍有缓存）。装完：

```bash
cmd.exe /c "where opencli"   # 拿到路径，填进 config.yaml 的 boss_cmd 第一项
```

BOSS 采集依赖 Windows 侧浏览器登录态，所以 opencli 必须装在 Windows，不是 WSL。若彻底装不上，采集降级为：手机截图经 Telegram 入库 + CSV 导入。

验证：`curl -s -X POST "http://127.0.0.1:8000/api/collect/runs?source=boss"` 返回 `"status": "success"` 且 `fetched_count > 0`。

## 5. 启动与开机自启

```bash
scripts/app.sh start     # 首次会自动建 venv、装依赖、构建前端
scripts/app.sh status
```

开机自启（Windows 登录时静默拉起 WSL + 应用）：在 Windows 启动文件夹
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` 放一个 `one-stop-job.vbs`：

```vbs
' 三处要按新机器改：发行版名、Linux 用户名、项目路径
Set ws = CreateObject("Wscript.Shell")
ws.Run "wsl.exe -d <发行版名> -u <用户名> -- <项目绝对路径>/scripts/app.sh start", 0, False
```

发行版名用 `wsl -l -v` 查。`app.sh start` 是幂等的（已在运行则直接返回），重复触发无副作用；uvicorn 常驻进程会让 WSL 保持存活。删掉这个 vbs 即取消自启。

验证：重启后不做任何操作，`curl -s http://127.0.0.1:8000/api/health` 返回 `"status":"ok"`。

## 6. 数据要不要带过去

`data/job_one_stop/` 里是 SQLite（岗位、评分、聊天、附件）。**岗位池不带也行**——重跑一次采集就回来了，而且更新鲜。真正不可再生的是聊天记录和截图附件；要保留就整目录拷过去（含 `chat_attachments/`）。

`daily_digest_state.json` 记录"最后一次发日清单的日期"，不带会导致新机器当天补发一次，无害。

## 7. 相关但独立的东西

- **个人上下文仓库**（看板、决策规则、岗位卡）：另一个 Git 仓库，独立还原，本项目只读它。
- **job-autopilot**（半自动触达）：另一个本地仓库，还原后改一处——脚本里的城市码常量（BOSS 搜索 URL 的 `city=`）。

## 常见坑（都踩过）

| 现象 | 原因 |
|---|---|
| `找不到 OpenCLI 命令: opencli` | WSL 里没有该命令；`boss_cmd` 第一项要填 Windows 绝对路径 |
| 改了 config.yaml 但行为没变 | 配置在启动时读入；`scripts/app.sh stop` 再 `start`（注意脚本没有 `restart` 子命令） |
| `cmd.exe` 报 UNC 路径不支持 | 从 WSL 目录调用 cmd.exe 的已知限制；调用方需把 cwd 指到 Windows 侧 |
| 晨间清单没来 | 依次查：进程在不在、`schedule.digest.enabled`、token/chat id、`/api/context/status` |
| `database is locked` | 本地开发模式和 Docker 同时在跑，只留一个 |
