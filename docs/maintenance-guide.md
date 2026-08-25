# 维护与使用指南

本指南用于日常接手、运行、验收和持续使用。项目边界保持不变：本地优先、单用户、SQLite，不自动投递，不自动发送消息。

面向日常使用者的完整产品操作手册见 [user-manual.md](user-manual.md)。本文件保留运行、维护和排障细节。

## 日常使用路径

1. 启动系统（口径见 [QUICKSTART.md](../QUICKSTART.md) / [operations.md](operations.md)）。
   - 日常使用（推荐）：`scripts/app.sh start` 单进程部署，访问 `http://127.0.0.1:8000/`。
   - 改代码/调试：本地开发热更新，后端 `.venv/bin/python -m uvicorn ...` + 前端 `npm run dev`，访问 `http://127.0.0.1:5173/`。
   - 备用：Windows 无 WSL 时用 Docker（双击 `start_app.bat` 或 `docker compose up -d`，:8000）。
   - 配置了 `JOB_ONE_STOP_CONTEXT_REPO_PATH` 时，先访问 `/api/context/status`，确认核心白名单文件齐全；状态接口不应出现宿主机绝对路径。

2. 校准个人画像。
   - 进入“匹配评分”。
   - 更新目标岗位、城市、薪资、技能、优势、真实工作经历和排除项。
   - 保存后，新评分会使用新画像；历史评分不会自动重算。

3. 补充岗位池。
   - BOSS / 智联：保持主服务运行，在宿主机运行 `tools\host_collect_boss.bat` 或 `tools\host_collect_zhilian.bat`。
   - CSV / XLSX：用顶栏上传入口导入。
   - 公众号 / 元宝：复制提示词到元宝，把回答或公众号链接粘回系统导入。
   - beBee：在 `config.yaml bebee.role_urls` 配置角色页后运行采集。

4. 调研与筛选。
   - 从岗位池打开岗位。
   - 补公司官网、招聘页、搜索、小红书、脉脉、看准或手动笔记证据。
   - 刷新评分，标记“待调研 / 合适 / 面试 / 拒绝 / 归档”。

5. 生成行动清单。
   - 对高潜岗位生成面试准备。
   - 在岗位抽屉记录投递/回复/约面/Offer 事件，保持漏斗数据真实。
   - 需要对外复用材料时，从“导出中心”导出画像、岗位、调研、准备、待办、复盘或完整归档。
   - 点击“生成今日求职冲刺包”。
   - 把冲刺包输出和跟进任务作为当天执行清单。

## 应用内指南

前端提供“使用指南”弹窗：

- 首次进入系统时自动展示一次。
- 顶栏 `i` 按钮可随时重新打开。
- 弹窗只记录在当前浏览器的 `localStorage`，不会写入数据库。

这样做的取舍是：新接手用户能看到闭环路径，日常用户不会每次被打断。

## 维护入口

常用命令：

```bash
scripts/quality_gate.sh
.venv/bin/python -m pytest -q
cd frontend && npm run build
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/api/health
docker compose down
```

Windows 对应入口：

- `start_app.bat`：日常启动，复用已有镜像。
- `rebuild_app.bat`：代码或依赖变化后强制重建。
- `status_app.bat`：查看容器状态。
- `stop_app.bat`：停止并移除容器。
- `run_quality_check.bat`：从 Windows 调 WSL 质量门禁。

提交前默认门禁：

```bash
scripts/quality_gate.sh
```

它覆盖后端测试、前端构建、真实 HTTP 系统冒烟和 Alembic 旧库迁移烟测。改过 Dockerfile、Compose 或启动脚本后，再额外跑一次 Docker 重建和健康检查。

## 变更红线

- 新岗位来源必须走 `Collector -> normalizer -> importer -> SQLite`。
- 导入、批量更新、批量删除必须显式由用户触发，不能做静默清理或后台自动删库。
- 抓取或解析失败必须写入 `report.skipped`，不能静默返回空。
- 密钥只放 `.env` 或环境变量，不能写入 `config.yaml` 或前端。
- 外部个人操作仓库：读只走 `ContextRepository` 白名单，不能从路由或其他服务直接拼路径。写只有一条通道——本人在已入库候选卡点「写入看板」，经 `ContextWriter`（`board_write.py`）在白名单 `board` 文件「收集箱」列插入一行（点击前原样预览）；除此之外不得新增任何写入路径（AST 绊线锁定）。
- 导出只能基于本地 SQLite 数据生成文件，不能把求职数据自动上传到第三方云端。
- 导出内容不能包含 `.env`、运行时配置密钥、宿主机路径或登录态目录。
- 现有表新增字段必须写 Alembic 迁移。
- 不提交 `.env`、SQLite、日志、Excel、登录态目录。
- 不自动投递、不自动发送消息、不自动外发联系方式。

## 故障定位顺序

1. 本地开发先确认后端 `http://127.0.0.1:8000/api/health`、前端 `http://127.0.0.1:5173/` 是否可访问；Docker 模式再看 `docker compose ps`。
2. 再看“系统配置”和“最近采集”，确认来源是否启用、是否有 skipped reason。
3. BOSS / 智联失败时，优先检查宿主机 OpenCLI 是否在 PATH、浏览器登录态是否过期。
4. 公众号 / beBee 返回 0 岗位时，先保留跳过原因；如果是页面结构变化，补 HTML fixture 或 Network JSON 后再改解析器。
5. 数据异常时先备份 Docker volume，再做迁移或清理。

## 推荐下一步维护

- UI 稳定后补 Playwright E2E，覆盖岗位抽屉、配置页、任务和冲刺包。
- 增加“画像保存后重算全部可行动岗位”入口。
- 持续沉淀真实 beBee / 公众号 HTML fixture，避免解析器靠猜。
- 给评分增加本地同义词表，例如 `SEO = 搜索优化 = 网站优化`。
