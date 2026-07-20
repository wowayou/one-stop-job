from __future__ import annotations

from ..models import FitScore, Job

# 现状口径(非累计漏斗):直接读 Job.status(事件已驱动状态),展示「当前停留在各阶段的岗位数」。
# 被拒/归档的岗位会自然移出已投/面试计数;累计转化需要事件历史,留待后续分析视图再做。
TOP_SCORE_THRESHOLD = 75
_APPLIED_STATUSES = {"applied", "interview", "offer"}
_INTERVIEW_STATUSES = {"interview", "offer"}


def build_funnel_payload(jobs: list[Job], latest_scores: dict[int, FitScore], stale_jobs: list[dict]) -> dict:
    applied_jobs = sum(1 for job in jobs if job.status in _APPLIED_STATUSES)
    interview_jobs = sum(1 for job in jobs if job.status in _INTERVIEW_STATUSES)
    offer_jobs = sum(1 for job in jobs if job.status == "offer")
    top_score_jobs = sum(1 for job in jobs if _top_score(job, latest_scores))

    return {
        "summary": {
            "top_score_jobs": top_score_jobs,
            "applied_jobs": applied_jobs,
            "interview_jobs": interview_jobs,
            "offer_jobs": offer_jobs,
            "stale_jobs": len(stale_jobs),
        },
    }


def _top_score(job: Job, latest_scores: dict[int, FitScore]) -> bool:
    if job.id is None:
        return False
    score = latest_scores.get(job.id)
    return bool(score and score.total >= TOP_SCORE_THRESHOLD)
