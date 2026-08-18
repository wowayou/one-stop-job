"""个人画像 + 评分 + 面试准备 + 漏斗分析路由（Phase R · R2）。

/api/profile、/api/analytics/funnel、/api/jobs/{id}/score、/api/jobs/{id}/prep。
从 main.py 原样搬出，行为逐字不变；依赖仅来自 deps/models/schemas/services/config，
不 import main。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from ..config import get_settings
from ..deps import SessionDep
from ..models import FitScore, InterviewPrep, Job, UserProfile, utc_now
from ..schemas import DealbreakerAdd, ProfileUpdate
from ..services.analytics import build_funnel_payload
from ..services.followup import find_stale_jobs
from ..services.jobs import latest_score_map
from ..services.prep_ops import build_prep_into_db
from ..services.queries import get_profile as _get_profile, score_job_into_db, validate_weights

router = APIRouter()


@router.get("/api/profile")
async def get_profile(session: SessionDep) -> UserProfile:
    return _get_profile(session)


@router.put("/api/profile")
async def update_profile(payload: ProfileUpdate, session: SessionDep) -> UserProfile:
    profile = _get_profile(session)
    updates = payload.model_dump(exclude_unset=True)
    # weights 是 score_job() 实际读取、真正影响评分的那份（config.yaml 的 scoring.weights 只在
    # 首次建画像时当种子默认值，之后编辑不会再生效）——校验规则和 _validate_scoring_config 共用
    # 同一个 validate_weights，两边口径必须一致。
    if "weights" in updates and updates["weights"] is not None:
        validate_weights(updates["weights"])
    for key, value in updates.items():
        setattr(profile, key, value)
    profile.updated_at = utc_now()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@router.post("/api/profile/dealbreakers")
async def add_dealbreaker(payload: DealbreakerAdd, session: SessionDep) -> dict:
    """追加一个排除词到 dealbreakers（逗号分隔、去重、去空白）。

    供候选卡「排除」快捷按钮调用：看到不想要的岗位时一键加词，不用跳到配置页填表单。
    返回更新后的完整排除词列表，前端据此刷新标签。
    """
    word = (payload.word or "").strip()
    if not word:
        raise HTTPException(status_code=422, detail="排除词不能为空")
    profile = _get_profile(session)
    existing = [w.strip() for w in (profile.dealbreakers or "").split(",") if w.strip()]
    if word not in existing:
        existing.append(word)
        profile.dealbreakers = ",".join(existing)
        profile.updated_at = utc_now()
        session.add(profile)
        session.commit()
    return {"dealbreakers": existing}


@router.delete("/api/profile/dealbreakers")
async def remove_dealbreaker(word: str, session: SessionDep) -> dict:
    """从 dealbreakers 里删一个词（供配置页标签编辑器的 × 按钮）。"""
    word = (word or "").strip()
    profile = _get_profile(session)
    existing = [w.strip() for w in (profile.dealbreakers or "").split(",") if w.strip()]
    if word in existing:
        existing = [w for w in existing if w != word]
        profile.dealbreakers = ",".join(existing)
        profile.updated_at = utc_now()
        session.add(profile)
        session.commit()
    return {"dealbreakers": existing}


@router.get("/api/analytics/funnel")
async def analytics_funnel(session: SessionDep) -> dict:
    jobs = session.exec(select(Job).where(Job.deleted_at.is_(None)).order_by(Job.collected_at.desc())).all()
    scores = latest_score_map(session, [job.id for job in jobs if job.id])
    stale_jobs = find_stale_jobs(session, now=utc_now(), stale_days=get_settings().followup_stale_days)
    return build_funnel_payload(jobs, scores, stale_jobs)


@router.get("/api/jobs/{job_id}/score")
async def list_scores(job_id: int, session: SessionDep) -> list[FitScore]:
    return session.exec(select(FitScore).where(FitScore.job_id == job_id).order_by(FitScore.created_at.desc())).all()


@router.post("/api/jobs/{job_id}/score")
async def create_score(job_id: int, session: SessionDep) -> FitScore:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return score_job_into_db(session, job, _get_profile(session))


@router.get("/api/jobs/{job_id}/prep")
async def get_prep(job_id: int, session: SessionDep) -> InterviewPrep | None:
    return session.exec(select(InterviewPrep).where(InterviewPrep.job_id == job_id).order_by(InterviewPrep.created_at.desc())).first()


@router.post("/api/jobs/{job_id}/prep")
async def create_prep(job_id: int, session: SessionDep, ai: bool = Query(default=True)) -> InterviewPrep:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return build_prep_into_db(session, job, _get_profile(session), use_ai=ai)
