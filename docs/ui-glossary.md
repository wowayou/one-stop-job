# UI Glossary

本文件统一本项目里的界面、交互和采集管线术语，便于后续改动时保持命名一致。

## 交互组件

| 术语 | 本项目含义 |
|---|---|
| toast / notice | 页面顶部的短消息提示，用于保存成功、采集失败、跳过原因等反馈；当前实现是 `NoticeBanner`。 |
| modal | 居中弹窗，用于新增岗位、公众号导入、今日冲刺包和 AI 配置示例；关闭后不改变页面导航状态。 |
| drawer | 右侧抽屉，用于查看和操作单个岗位详情、评分、调研和面试准备。 |
| spotlight tour | 聚光灯引导（`Tour`）：用高亮框罩住顶栏/导航/指标等首屏元素并浮出说明气泡；首次进入经使用指南弹窗启动，顶栏信息按钮可随时重开，目标缺失时降级为居中气泡。 |
| error boundary | 顶层渲染兜底（`ErrorBoundary`）：捕获子树渲染异常，避免整页白屏，提供“重新加载/尝试继续”入口；数据仍安全留在本机。 |
| card | 单个重复信息块，例如来源卡、岗位列表项、公司项；不用于包裹整页区域。 |
| tab | 配置页中的分区切换，例如运行状态、AI、采集来源、评分权重、高级。 |
| status badge | 小型状态标签，例如 `ok`、`disabled`、`host_import_required`、岗位状态和风险等级。 |
| skeleton | 数据加载时的占位骨架；当前项目主要使用轻量文字加载态，后续可在岗位列表和公司列表补 skeleton。 |
| empty state | 空结果状态，例如没有岗位、没有调研证据、没有跟进任务时的提示。 |
| disabled state | 不可点击状态，例如忙碌中、来源未配置、评分权重合计超过 100 时禁用保存。 |
| validation | 保存前后的校验。前端用于即时阻止明显错误，后端作为最终边界，例如拒绝敏感字段和非法评分权重。 |
| progressive disclosure | 渐进披露；常用做法是把少用配置放进 `details` 或高级分区，只在需要时展开。 |

## 项目语义

| 术语 | 本项目含义 |
|---|---|
| 本地优先 | 数据默认保存在本机 SQLite 和仓库根目录 `config.yaml`；密钥放 `.env`，不上传云端。 |
| 采集管线 | `source -> normalizer -> importer/upsert -> Job/Company/JobSourceLink`，所有来源最终进入同一套岗位模型。 |
| 来源 | 岗位入口类型，例如 BOSS、智联、公众号、beBee、CSV/XLSX、手动新增。 |
| 宿主机采集 | BOSS/智联在 Windows 宿主机运行 OpenCLI，再把 CSV 导入主服务；容器内不直接调用 OpenCLI。 |
| structured pages | 抓公开网页源码并解析结构化数据、Next/RSC payload、microdata 或可见卡片；当前用于 beBee。 |
| skipped reason | 采集成功但某个 URL 没产出岗位时记录的跳过原因，用于判断是页面为空、字段不支持还是 JS 接口渲染。 |
| 权重合计 | `scoring.weights` 的总和；前后端都要求不超过 100，避免评分上限语义失真。 |
| 今日冲刺包 | 一次性补评分、生成面试准备、创建跟进任务并输出 Markdown 的工作流。 |
