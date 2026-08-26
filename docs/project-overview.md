# 项目总览

快速了解项目结构和核心概念。

---

## 📁 目录结构

```
one-stop-job/
├── backend/              # Python 后端（FastAPI）
│   └── app/
│       ├── main.py      # 路由入口
│       ├── models.py    # 数据库模型
│       ├── config.py    # 配置加载
│       └── services/    # 业务逻辑
│           ├── normalizer.py    # 数据规范化
│           ├── importer.py      # 统一入库
│           ├── collectors.py    # 采集器基类
│           ├── scoring.py       # 评分引擎
│           ├── prep.py          # 面试准备
│           ├── wechat.py        # 公众号解析
│           ├── bebee.py         # beBee 解析
│           └── ai.py            # AI 兜底（可选）
├── frontend/            # React 前端（Vite + TypeScript）
│   ├── src/
│   │   ├── App.tsx      # 主应用
│   │   ├── api.ts       # 后端 API 调用
│   │   └── components/  # UI 组件
│   └── dist/            # 构建产物
├── tests/               # 后端测试
│   ├── fixtures/        # 测试样例数据
│   └── test_*.py        # 测试用例
├── tools/               # 宿主机采集脚本
│   ├── host_collect_boss.bat       # Windows BOSS 采集
│   ├── host_collect_zhilian.bat    # Windows 智联采集
│   └── host_collect_opencli.sh     # Linux/macOS 采集
├── scripts/             # 运维脚本
│   ├── quality_gate.sh       # 质量门禁
│   ├── deploy_check.sh       # 部署检查
│   ├── docker_doctor.sh      # Docker 诊断
│   └── system_smoke.sh       # 系统冒烟测试
├── docs/                # 文档
│   ├── maintenance-guide.md      # 日常使用指南
│   ├── operations.md             # 运维手册
│   ├── docker-optimization.md    # Docker 优化
│   ├── data-flow.md              # 数据流架构
│   ├── scoring-audit.md          # 当前评分规则审计
│   └── testing-system.md         # 测试体系
├── data/                # 数据目录（本地开发）
│   └── job_one_stop/    # SQLite 数据库
├── config.yaml          # 主配置文件
├── .env                 # 环境变量（密钥）
├── requirements.txt     # Python 依赖（开发+测试）
├── requirements-runtime.txt  # Python 运行时依赖
├── Dockerfile           # Docker 镜像定义
├── docker-compose.yml   # Docker 编排
├── README.md            # 项目主文档
├── QUICKSTART.md        # 快速开始指南
└── CLAUDE.md            # 项目架构标准（AI 指南）
```

---

## 🔄 数据流（核心）

所有岗位来源都汇入同一条管线：

```
┌─────────────────┐
│  岗位来源        │
│  - BOSS 直聘    │
│  - 智联招聘      │
│  - 公众号        │
│  - beBee        │
│  - CSV/Excel    │
│  - 手动录入      │
└────────┬────────┘
         │ 原始数据（dict）
         ▼
┌─────────────────┐
│ Normalizer      │  模糊键映射、薪资/城市解析
│ 数据规范化       │  生成 external_id、canonical_key
└────────┬────────┘
         │ 规范化 dict
         ▼
┌─────────────────┐
│ Importer        │  跨来源去重（canonical_key）
│ 统一入库         │  保留来源链接（JobSourceLink）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ SQLite          │
│ - Job           │  岗位主表
│ - Company       │  公司表
│ - JobSourceLink│  来源链接
│ - FitScore      │  评分
│ - PrepMaterial  │  面试准备
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 业务消费         │
│ - 评分引擎       │  scoring.py（规则驱动）
│ - 面试准备       │  prep.py（模板+AI）
│ - 公司调研       │  手动沉淀证据
│ - 跟进任务       │  本地任务管理
└─────────────────┘
```

**关键原则**：
- 采集器只产出规范化 dict，不直接写数据库
- 新增来源不影响评分、面试准备逻辑
- 跨来源去重基于 `canonical_key = sha1(title|company|city|area)`

---

## 🎯 核心概念

### 1. 岗位去重

- **来源内去重**：`UNIQUE(Job.source, Job.external_id)`
- **跨来源去重**：`Job.canonical_key`（根据岗位内容计算）
- **来源链接**：`JobSourceLink` 表记录每个来源的证据

### 2. 评分系统

- **规则引擎**：`scoring.py` 的 `score_job()` 函数
- **维度**：岗位匹配、薪资/城市、成长性、稳定性、口碑、通勤/作息
- **权重**：在 `config.yaml` 中配置，总和不超过 100
- **AI 边界**：AI 只辅助抽取和解释，不直接写入评分

### 3. 面试准备

- **基于模板**：`prep.py` 中的 `PrepGenerator`
- **输入**：岗位信息 + 个人画像（config.yaml）
- **输出**：JD 摘要、技能差距、STAR 素材、反问问题等

### 4. AI 集成（可选）

- **配置**：`.env` 中的 OpenAI 兼容配置
- **用途**：
  - 公众号多岗位抽取（正则优先，AI 兜底）
  - 面试准备内容生成
  - 公司调研证据摘要（规划中）
- **原则**：AI 增强而非替代规则引擎

---

## 🚪 关键入口

### 用户入口

| 入口 | 说明 |
|------|------|
| `README.md` | 项目介绍、功能概览 |
| `QUICKSTART.md` | 快速开始（本地/Docker/Windows/Linux） |
| `docs/maintenance-guide.md` | 日常使用流程 |
| 系统「使用指南」弹窗 | 首次打开自动展示 |

### 开发者入口

| 入口 | 说明 |
|------|------|
| `CLAUDE.md` | 架构标准、数据流、红线 |
| `docs/data-flow.md` | 数据流详细设计 |
| `docs/testing-system.md` | 测试体系和规范 |
| `docs/handoff.md` | 项目交接清单 |

### 运维入口

| 入口 | 说明 |
|------|------|
| `docs/operations.md` | 运行部署（单进程/本地开发/Docker）、数据备份、运行排障 |
| `docs/docker-optimization.md` | 构建优化、故障排查 |
| `scripts/deploy_check.sh` | 部署前自检 |
| `scripts/quality_gate.sh` | 质量门禁 |

---

## 🧩 扩展点

### 新增岗位来源

1. 在 `services/<source>.py` 实现解析逻辑
2. 在 `collectors.py` 添加 `<Source>Collector` 类
3. 在 `main.py` 添加端点
4. 在 `config.yaml` 添加配置段
5. 编写测试（`tests/test_<source>.py`）

详见 [CLAUDE.md](../CLAUDE.md) § 2。

### 调整评分权重

编辑 `config.yaml`：

```yaml
scoring:
  weights:
    job_match: 35      # 岗位匹配度
    salary_city: 25    # 薪资和城市
    growth: 15         # 成长性
    stability: 10      # 稳定性
    reputation: 10     # 口碑
    commute: 5         # 通勤/作息
```

总和不超过 100。

### 自定义面试准备模板

编辑 `backend/app/services/prep.py` 中的 `PrepGenerator` 类。

---

## 📦 依赖管理

| 文件 | 用途 |
|------|------|
| `requirements.txt` | **本地开发**：包含测试、代码检查等工具 |
| `requirements-runtime.txt` | **运行时依赖集合**：被 `requirements.txt` 引用，Dockerfile 也直接使用 |
| `requirements-automation.txt` | **可选**：Playwright 等重依赖 |
| `frontend/package.json` | 前端依赖（React、Vite、TypeScript） |

---

## 🔒 安全与隐私

- **不提交**：`.env`、`data/`、`*.sqlite3`、日志、Excel 报表
- **本地优先**：数据不出本机，无账号体系
- **不自动化**：不自动投递、不自动发消息
- **密钥管理**：只在 `.env` 配置，后端不返回明文

---

## 🧪 测试策略

| 测试类型 | 工具 | 覆盖 |
|---------|------|------|
| 单元测试 | pytest | 解析器、规范化、评分 |
| 集成测试 | pytest + AsyncClient | API 端点 + 数据库 |
| 系统冒烟 | `system_smoke.sh` | HTTP 全流程（临时数据库） |
| 质量门禁 | `quality_gate.sh` | 测试 + 构建 + 冒烟 + 迁移 |

**原则**：测试不联网、不访问真实平台、使用 fixtures。

---

## 🚀 发布流程

1. 修改代码
2. 运行 `scripts/quality_gate.sh`（必须全绿）
3. 定版本号：`python3 scripts/sync_version.py <X.Y.Z>`
   - 唯一事实源是根目录 `VERSION`；脚本把它同步到 `frontend/package.json`、
     `src-tauri/Cargo.toml`、`src-tauri/Cargo.lock`、`src-tauri/tauri.conf.json`
     和 `backend/app/version.py`，`tests/test_version_sync.py` 锁住一致性
4. 写发布说明：新增 `docs/releases/v<X.Y.Z>.md`
   - release workflow 直接读这个文件当 Release body；缺失时只写通用兜底文案
5. 更新 `frontend/src/lib/whatsNew.ts` 的 `version` 与三段文案（升级后首次启动的说明弹窗）
   - 由 `tests/test_version_sync.py` 锁住：`version` 与 `VERSION` 不等时测试直接翻红
   - **刻意不自动同步**——自动改会让旧版功能列表挂上新版号，比"弹窗不出现"更糟
6. 提交代码，打标签 `v<X.Y.Z>` 并推送
   - workflow 会先校验「标签 == VERSION == 各清单」，不一致直接失败
   - 产物含各平台安装包、每个包的 `.sha256` 与按平台汇总的 `SHA256SUMS-<平台>.txt`
7. 在 GitHub 上把草稿 Release 转为正式发布
   - **只有正式 Release 会被应用内升级检查识别**（draft / pre-release 一律跳过）
8. 部署时运行 `scripts/deploy_check.sh`

---

## 📖 文档地图

```
README.md ──────────┬──> QUICKSTART.md（快速开始）
                    │
                    ├──> CLAUDE.md（架构标准）
                    │
                    └──> docs/
                         ├── maintenance-guide.md（日常使用）
                         ├── operations.md（运维手册）
                         ├── docker-optimization.md（Docker 构建排障）
                         ├── data-flow.md（数据流）
                         ├── testing-system.md（测试体系）
                         ├── handoff.md（项目交接）
                         ├── 12-hour-sprint-playbook.md（求职冲刺）
                         ├── scoring-audit.md（评分审计）
                         └── ui-glossary.md（界面术语）
```

---

需要更多细节？根据场景选择：

- **快速开始** → [QUICKSTART.md](../QUICKSTART.md)
- **日常使用** → [docs/maintenance-guide.md](maintenance-guide.md)
- **开发扩展** → [CLAUDE.md](../CLAUDE.md)
- **故障排查** → 应用内 **设置 → 诊断**（版本/进程/.env/config.yaml/AI/网络 + 备份与脱敏日志）；再看 [docs/operations.md](operations.md) + [docs/docker-optimization.md](docker-optimization.md)
