"""冲刺包生成 helper（Phase R · R2）。

`job_markdown_row` / `build_sprint_markdown` / `create_sprint_payload`：原本堆在
main.py，只被 `routers/misc.py` 的 `POST /api/sprint/brief` 使用；下沉到这里让路由
文件保持薄。纯逻辑、无 FastAPI app 依赖；只碰 session / models / services / config。
"""

from __future__ import annotations

from sqlmodel import Session, select

from ..config import get_settings
from ..models import FitScore, FollowUpTask, InterviewPrep, Job, UserProfile, utc_now
from .followup import find_stale_jobs
from .jobs import latest_prep_map, latest_score_map
from .prep_ops import build_prep_into_db
from .queries import get_profile, score_job_into_db


def job_markdown_row(rank: int, job: Job, score: FitScore) -> str:
    location = " ".join([part for part in [job.city, job.area] if part]) or "-"
    status = "硬阻断" if score.hard_blocked else job.status
    next_step = "补公司调研" if not job.company_id else "准备沟通/投递"
    link = job.url or ""
    title = f"[{job.title}]({link})" if link else job.title
    return (
        f"| {rank} | {score.total:g} | {job.company_name} | {title} | "
        f"{job.salary_text or '-'} | {location} | {status} | {next_step} |"
    )


def build_sprint_markdown(
    *,
    profile: UserProfile,
    ranked: list[tuple[Job, FitScore]],
    prepared: list[tuple[Job, InterviewPrep]],
    tasks: list[FollowUpTask],
    stale: list[dict],
) -> str:
    lines = [
        "# 今日求职冲刺包",
        "",
        "## 个人画像",
        f"- 目标岗位：{profile.target_titles}",
        f"- 目标城市：{profile.target_cities}",
        f"- 薪资期望：{profile.salary_min_k:g}-{profile.salary_max_k:g}K",
        f"- 核心技能：{profile.skills}",
        f"- 排除项：{profile.dealbreakers}",
        "",
        "## Top 岗位清单",
        "| 排名 | 分数 | 公司 | 岗位 | 薪资 | 地点 | 状态 | 下一步 |",
        "|---:|---:|---|---|---|---|---|---|",
    ]
    lines.extend(job_markdown_row(index, job, score) for index, (job, score) in enumerate(ranked, start=1))
    if not ranked:
        lines.append("| - | - | - | 暂无可用岗位 | - | - | - | 先采集或导入岗位 |")

    lines.extend(["", "## 面试准备重点"])
    if prepared:
        for index, (job, prep) in enumerate(prepared, start=1):
            lines.extend(
                [
                    "",
                    f"### {index}. {job.company_name} - {job.title}",
                    f"- JD 摘要：{prep.jd_summary}",
                    f"- 核心优势话术：{prep.core_pitch}",
                    f"- 简历强调点：{prep.resume_points}",
                    f"- 岗位定制简历：{prep.tailored_resume}",
                    f"- 反问问题：{prep.questions_to_ask}",
                    f"- 沟通草稿：{prep.communication_draft}",
                ]
            )
    else:
        lines.append("- 暂无可准备岗位；先导入岗位并生成评分。")

    lines.extend(["", "## 需跟进（fit/interview 久无进展）"])
    if stale:
        lines.extend(
            f"- {item['company_name']} - {item['title']}：{item['reason']}" for item in stale
        )
    else:
        lines.append("- 暂无久无进展的岗位。")

    lines.extend(["", "## 待办"])
    if tasks:
        lines.extend(f"- [ ] {task.title}" for task in tasks)
    else:
        lines.append("- [ ] 采集岗位并筛出 Top 5")
    return "\n".join(lines)


def create_sprint_payload(
    session: Session,
    *,
    top_n: int,
    prep_n: int,
    create_tasks: bool,
    rescore: bool,
) -> dict:
    profile = get_profile(session)
    jobs = session.exec(select(Job).where(Job.deleted_at.is_(None)).order_by(Job.favorite.desc(), Job.collected_at.desc())).all()
    actionable_jobs = [job for job in jobs if job.status not in {"rejected", "archived"}]

    latest_scores = latest_score_map(session, [job.id for job in actionable_jobs if job.id])
    ranked: list[tuple[Job, FitScore]] = []
    for job in actionable_jobs:
        score = latest_scores.get(job.id or 0)
        if rescore or score is None:
            score = score_job_into_db(session, job, profile)
        ranked.append((job, score))

    ranked.sort(key=lambda item: (not item[1].hard_blocked, item[1].total, item[0].favorite), reverse=True)
    top_jobs = ranked[:top_n]
    prep_jobs = top_jobs[:prep_n]

    latest_preps = latest_prep_map(session, [job.id for job, _ in prep_jobs if job.id])
    prepared: list[tuple[Job, InterviewPrep]] = []
    for job, _score in prep_jobs:
        prep = latest_preps.get(job.id or 0) or build_prep_into_db(session, job, profile)
        prepared.append((job, prep))

    created_tasks: list[FollowUpTask] = []
    if create_tasks:
        prep_job_ids = [job.id for job, _ in prep_jobs if job.id]
        existing_tasks = (
            session.exec(select(FollowUpTask).where(FollowUpTask.job_id.in_(prep_job_ids))).all() if prep_job_ids else []
        )
        existing_keys = {(task.job_id, task.title) for task in existing_tasks}
        for job, _score in prep_jobs:
            title = f"待办 {job.company_name} - {job.title}"
            if (job.id, title) in existing_keys:
                continue
            task = FollowUpTask(job_id=job.id, title=title)
            session.add(task)
            created_tasks.append(task)
        if created_tasks:
            session.commit()
            for task in created_tasks:
                session.refresh(task)

    stale_jobs = find_stale_jobs(session, now=utc_now(), stale_days=get_settings().followup_stale_days)
    markdown = build_sprint_markdown(profile=profile, ranked=top_jobs, prepared=prepared, tasks=created_tasks, stale=stale_jobs)
    return {
        "generated_at": utc_now().isoformat(),
        "profile": profile.model_dump(),
        "top_jobs": [{**job.model_dump(), "latest_score": score.model_dump()} for job, score in top_jobs],
        "prepared": [{"job": job.model_dump(), "prep": prep.model_dump()} for job, prep in prepared],
        "tasks_created": [task.model_dump() for task in created_tasks],
        "stale_jobs": stale_jobs,
        "markdown": markdown,
    }
