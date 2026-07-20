from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from backend.app.models import FollowUpTask, InterviewLog, Job
from backend.app.services.followup import find_stale_jobs, is_stale


_NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)


def test_is_stale_only_flags_active_statuses():
    old = _NOW - timedelta(days=10)
    assert is_stale("interview", old, _NOW, 5)[0] is True
    assert is_stale("fit", old, _NOW, 5)[0] is True
    for status in ("new", "researching", "rejected", "archived"):
        assert is_stale(status, old, _NOW, 5) == (False, 0)


def test_is_stale_threshold_boundary():
    assert is_stale("interview", _NOW - timedelta(days=5), _NOW, 5) == (True, 5)
    assert is_stale("interview", _NOW - timedelta(days=4), _NOW, 5) == (False, 4)


def test_is_stale_without_activity_is_not_stale():
    assert is_stale("interview", None, _NOW, 5) == (False, 0)


def test_is_stale_handles_naive_and_aware_mix():
    # status_changed_at 从 SQLite 取回常为 naive；不应因 aware/naive 相减报错。
    naive_old = datetime(2026, 6, 7)
    overdue, days = is_stale("interview", naive_old, _NOW, 5)
    assert overdue is True
    assert days == 10


def test_find_stale_jobs_uses_latest_done_task_and_interview_in_batch_mode():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        active_job = Job(
            source="manual",
            external_id="stale-1",
            title="SEO运营",
            company_name="示例市增长科技",
            status="interview",
            status_changed_at=datetime(2026, 6, 1),
        )
        fresh_job = Job(
            source="manual",
            external_id="stale-2",
            title="外贸独立站运营",
            company_name="示例市跨境科技",
            status="fit",
            status_changed_at=datetime(2026, 6, 10),
        )
        session.add(active_job)
        session.add(fresh_job)
        session.commit()
        session.refresh(active_job)
        session.refresh(fresh_job)

        session.add(InterviewLog(job_id=active_job.id, round="一面", created_at=datetime(2026, 6, 3)))
        session.add(FollowUpTask(job_id=active_job.id, title="旧待办", status="done", updated_at=datetime(2026, 6, 5)))
        session.add(FollowUpTask(job_id=active_job.id, title="未完成待办", status="todo", updated_at=datetime(2026, 6, 15)))
        session.add(InterviewLog(job_id=fresh_job.id, round="一面", created_at=datetime(2026, 6, 16)))
        session.commit()

        stale = find_stale_jobs(session, now=datetime(2026, 6, 17, tzinfo=timezone.utc), stale_days=5)

    assert stale == [
        {
            "job_id": active_job.id,
            "title": "SEO运营",
            "company_name": "示例市增长科技",
            "status": "interview",
            "days": 12,
            "reason": "interview 状态已 12 天无跟进",
        }
    ]
