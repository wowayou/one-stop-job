"""候选（Candidate）字典的单一类型定义（Phase R · R5：候选状态类型收口）。

`ChatMessage.metadata_json["candidates"]`（聊天里挂的候选岗位列表，来自
`services/ingest.run_ingest` → `services/chat_ingest._persist_ingest_to_chat` 落盘，
再被 `routers/chat.py` 的 commit/restore/board-write 端点读写）此前是一串无类型 dict，
字段散落在多个模块的约定里，没有单一定义。本模块提供这个单一来源：一个 `TypedDict`，
纯静态类型标注，**不做运行时校验**（项目 CI 不跑 mypy，这里只是给函数签名/局部变量当
文档 + IDE 提示，不会引入任何运行时强制转换或 TypeError）。

与 `frontend/src/types.ts` 的 `IngestCandidate` 一一对应；改动任一边字段名/含义时，
两处的注释需要同步更新。

字段说明：
- `title` / `company_name` / `salary_text` / `city` / `area` / `source` / `url` /
  `description` / `canonical_key`：由 `services/normalizer.normalize_record` 产出的
  规范化岗位字段。注意实际运行时的候选 dict 往往还带更多 normalizer 字段
  （如 `external_id`/`experience`/`degree`/`skills`/`recruiter`/`salary_min_k` 等）——
  这里只收录候选生命周期管理会直接用到的最小集合，不是 Job 表字段的完整镜像，`total=False`
  也不会拒绝额外字段。
- `status`：候选自身的生命周期状态（`pending`/`committed`/`skipped`，见
  `CandidateStatus`/`CANDIDATE_*` 常量）。**不是** `Job.status`（岗位招聘漏斗状态，如
  "new"/"applied"）——两者字面上都叫 status 但语义完全不同，写入 Job 表前必须剔除。
- `job_id`：候选变成 `committed` 后回填的、真正写入的 `Job.id`。同样不是 Job 表自身的
  字段（Job 模型没有 `job_id` 列），写入前也必须剔除。
- `board_written`：该候选对应的岗位是否已经被写过一行看板卡片（`board_write_candidates`
  端点用，幂等标记，避免重复写入个人操作仓库看板）。
- `existing_job_id`：**纯 UI 字段**。候选的 `canonical_key` 命中岗位池里已有的 Job 时，
  `chat_ingest._persist_ingest_to_chat` 会打上这个标注，供前端展示「已在岗位池」并默认
  不勾选；提交入库前必须剔除（不是 Job 表字段，仅用于展示）。
- `advice`：**纯 UI 字段**。`services/advice.build_candidate_advice` 为前几个候选生成的决策
  建议（优先级/方向/下一步/先问什么），供 Web 候选卡展示、手机回执排版；只读判断结果，
  不承载任何入库语义，提交前必须剔除（Job 表没有这一列）。
- `duplicate_in_thread_id`：**纯 UI 字段**。该候选与最近 ~50 个 ingest 线程里已出现过的
  某候选重复时，`services/ingest.find_duplicate_thread` 会打上匹配到的线程 id，供前端
  标「重复候选」徽标并默认不勾选；提交入库前同样必须剔除（仅用于展示）。
- `score`：**纯 UI 字段**。采集初筛候选的匹配分（`services/jobs.attach_candidate_scores`，
  与岗位池 `FitScore` 同一个 `scoring.score_job`），用于候选卡排序与手机清单展示；
  Job 表没有这一列（分数是 `fit_scores` 的一行流水，入库后由 commit 端点正式评分），
  提交前必须剔除。
- `hard_blocked`：**纯 UI 字段**。`attach_candidate_scores` 同步写入的硬阻断标记
  （命中 dealbreakers / 城市不符 / 薪资过低），供前端候选卡默认折叠被阻断的候选；
  Job 表没有这一列（入库后由 `FitScore.hard_blocked` 正式记录），提交前必须剔除。

`CANDIDATE_UI_ONLY_FIELDS` / `strip_ui_only_fields` 是这些纯 UI 字段的集中剔除点。
注意：写入 Job 表前实际还需要额外剔除 `status`/`job_id`——它们是 candidate 自身的生命周期
记账字段而非「纯 UI 字段」（`existing_job_id`/`duplicate_in_thread_id`/`advice` 从头到尾只被展示，
从不被赋予业务含义；`status`/`job_id` 则是候选自己的状态机），因此不在
`CANDIDATE_UI_ONLY_FIELDS` 里，调用方（如 `commit_candidates`）仍需自行剔除。
"""

from __future__ import annotations

from typing import Literal, TypedDict

# 候选自身的生命周期状态；与 Job.status（招聘漏斗状态）无关，见模块文档字符串。
CandidateStatus = Literal["pending", "committed", "skipped"]

CANDIDATE_PENDING: Literal["pending"] = "pending"
CANDIDATE_COMMITTED: Literal["committed"] = "committed"
CANDIDATE_SKIPPED: Literal["skipped"] = "skipped"


class Candidate(TypedDict, total=False):
    """`metadata_json.candidates` 里一条候选岗位的字段（静态类型标注，非运行时校验）。"""

    # normalizer 产出的规范化岗位字段（最小集合，见模块文档字符串）
    title: str
    company_name: str
    salary_text: str | None
    city: str | None
    area: str | None
    source: str
    url: str | None
    description: str | None
    canonical_key: str | None

    # 候选自身的生命周期字段：不是 Job 表字段，写入 Job 表前需单独剔除
    status: CandidateStatus
    job_id: int | None

    # 看板回写标记
    board_written: bool

    # 纯 UI 字段：仅供前端展示，提交入库前必须剔除，见 CANDIDATE_UI_ONLY_FIELDS
    existing_job_id: int | None
    duplicate_in_thread_id: int | None
    advice: dict
    score: float | None
    hard_blocked: bool
    reach: dict
    application_pack: dict


# 纯 UI 字段：candidate dict 里只用于前端展示、从不承载业务语义的字段。
# 不包含 status/job_id——那两个是候选自身的状态机记账，是否剔除由调用方按场景决定。
CANDIDATE_UI_ONLY_FIELDS: tuple[str, ...] = ("existing_job_id", "duplicate_in_thread_id", "advice", "score", "hard_blocked", "reach", "application_pack")


def strip_ui_only_fields(candidate: dict) -> dict:
    """返回剔除「纯 UI 字段」（`CANDIDATE_UI_ONLY_FIELDS`）后的候选浅拷贝。

    供 commit / board-write 等「即将把候选字段落到别处（如 upsert 记录）」的调用点统一
    调用，避免每处各自手写剔除集合而逐渐漂移。`status`/`job_id` 是否需要额外剔除由
    调用方按场景决定（例如写入 Job 表前，见 `routers/chat.py` 的 `commit_candidates`）。
    """

    return {k: v for k, v in candidate.items() if k not in CANDIDATE_UI_ONLY_FIELDS}
