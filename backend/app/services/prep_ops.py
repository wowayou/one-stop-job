"""面试准备生成 helper（Phase R · R2）。

`ai_ready` / `prep_ai_context` / `build_prep_into_db`：原本堆在 main.py，被
`routers/scoring.py`（`POST /api/jobs/{id}/prep`）与 main.py 自身的 `_create_sprint_payload`
（冲刺包批量生成）共用，下沉到这里让两边都能直接 import，避免循环依赖 main。

纯逻辑、无 FastAPI app 依赖；只碰 session / models / services / config。
"""

from __future__ import annotations

from sqlmodel import Session

from ..config import get_settings
from ..models import Company, Draft, InterviewPrep, Job, UserProfile
from .ai import is_ai_available, tailor_interview_prep_llm
from .prep import build_interview_prep


def ai_ready() -> bool:
    """AI 真正可用 = config.yaml ai.enabled 为真且配置了密钥。"""
    ai_cfg = get_settings().config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    return bool(ai_cfg.get("enabled")) and is_ai_available()


def prep_ai_context(job: Job, company: Company | None, profile: UserProfile) -> dict[str, str]:
    location = " ".join(part for part in [job.city, job.area] if part) or "地点未披露"
    return {
        "title": job.title or "",
        "company_name": (company.name if company else job.company_name) or "",
        "requirements": (job.skills or job.description or "").strip(),
        "salary": job.salary_text or "薪资未披露",
        "location": location,
        "profile_skills": profile.skills or "",
        "profile_strengths": profile.strengths or "",
        "profile_experience": profile.work_experience or "",
        "salary_expectation": f"{profile.salary_min_k:g}-{profile.salary_max_k:g}K",
        "dealbreakers": profile.dealbreakers or "",
    }


def build_prep_into_db(session: Session, job: Job, profile: UserProfile, *, use_ai: bool = True) -> InterviewPrep:
    company = session.get(Company, job.company_id) if job.company_id else None
    payload = build_interview_prep(job, company, profile)
    if use_ai and ai_ready():
        tailored = tailor_interview_prep_llm(prep_ai_context(job, company, profile), payload)
        if tailored:
            payload = tailored
    prep = InterviewPrep(job_id=job.id or 0, **payload)
    session.add(prep)
    session.add(Draft(job_id=job.id, kind="communication_draft", channel="manual", content=payload["communication_draft"]))
    session.add(Draft(job_id=job.id, kind="core_pitch", channel="manual", content=payload["core_pitch"]))
    session.add(Draft(job_id=job.id, kind="tailored_resume", channel="manual", content=payload["tailored_resume"]))
    session.commit()
    session.refresh(prep)
    return prep
