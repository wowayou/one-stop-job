from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

from fastapi.encoders import jsonable_encoder

from ..models import ApplicationEvent, Company, Draft, FitScore, FollowUpTask, InterviewLog, InterviewPrep, Job, ResearchItem, SourceRun, UserProfile


def export_jobs_csv(jobs: list[dict]) -> str:
    rows = []
    for job in jobs:
        score = job.get("latest_score", {})
        score_total = score.get("total") if isinstance(score, dict) else None
        rows.append(
            {
                "company_name": job.get("company_name"),
                "title": job.get("title"),
                "status": job.get("status"),
                "favorite": job.get("favorite"),
                "score_total": score_total,
                "hard_blocked": score.get("hard_blocked") if isinstance(score, dict) else None,
                "salary_text": job.get("salary_text"),
                "city": job.get("city"),
                "area": job.get("area"),
                "source": job.get("source"),
                "url": job.get("url"),
            }
        )
    return _to_csv(rows)


def export_archive_json(*, schema_version: str, generated_at: str, payload: dict[str, Any]) -> str:
    return json.dumps({"schema_version": schema_version, "generated_at": generated_at, **payload}, ensure_ascii=False, indent=2)


def encode_json(value: Any) -> str:
    return json.dumps(jsonable_encoder(value), ensure_ascii=False, indent=2)


def build_archive_payload(
    *,
    profile: UserProfile,
    jobs: list[Job],
    companies: list[Company],
    research_items: list[ResearchItem],
    scores: list[FitScore],
    preps: list[InterviewPrep],
    drafts: list[Draft],
    tasks: list[FollowUpTask],
    interviews: list[InterviewLog],
    runs: list[SourceRun],
    events: list[ApplicationEvent],
) -> dict[str, Any]:
    return jsonable_encoder(
        {
            "profile": profile,
            "jobs": jobs,
            "companies": companies,
            "research_items": research_items,
            "fit_scores": scores,
            "interview_prep": preps,
            "drafts": drafts,
            "follow_up_tasks": tasks,
            "interview_logs": interviews,
            "source_runs": runs,
            "application_events": events,
        }
    )


def _to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    headers = list(dict.fromkeys(key for row in rows for key in row))
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
