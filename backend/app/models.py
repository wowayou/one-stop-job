from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Column, Float, JSON, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("name", name="uq_company_name"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    website: Optional[str] = None
    industry: Optional[str] = None
    size: Optional[str] = None
    stage: Optional[str] = None
    location: Optional[str] = None
    risk_level: str = Field(default="unknown", index=True)
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_external"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(default="manual", index=True)
    external_id: str = Field(index=True)
    url: Optional[str] = Field(default=None, index=True)
    title: str = Field(index=True)
    company_id: Optional[int] = Field(default=None, foreign_key="companies.id", index=True)
    company_name: str = Field(index=True)
    salary_text: Optional[str] = None
    salary_min_k: Optional[float] = None
    salary_max_k: Optional[float] = None
    salary_avg_k: Optional[float] = None
    annual_salary_w: Optional[float] = None
    city: Optional[str] = Field(default=None, index=True)
    area: Optional[str] = None
    experience: Optional[str] = None
    degree: Optional[str] = None
    skills: Optional[str] = Field(default=None, sa_column=Column(Text))
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    recruiter: Optional[str] = None
    recruiter_title: Optional[str] = None
    recruiter_is_hr: bool = False
    status: str = Field(default="new", index=True)
    recruitment_status: str = Field(default="unknown", index=True)
    favorite: bool = Field(default=False, index=True)
    published_at: Optional[date] = Field(default=None, index=True)
    last_seen_at: datetime = Field(default_factory=utc_now, index=True)
    canonical_key: Optional[str] = Field(default=None, index=True)
    collected_at: datetime = Field(default_factory=utc_now, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    status_changed_at: Optional[datetime] = Field(default=None)  # status 最近一次变更时间，用于跟进过期检测


class JobSourceLink(SQLModel, table=True):
    __tablename__ = "job_source_links"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_link"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    source: str = Field(index=True)
    external_id: str = Field(index=True)
    url: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = None
    company_name: Optional[str] = Field(default=None, index=True)
    published_at: Optional[date] = Field(default=None, index=True)
    raw_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    first_seen_at: datetime = Field(default_factory=utc_now, index=True)
    last_seen_at: datetime = Field(default_factory=utc_now, index=True)


class SourceRun(SQLModel, table=True):
    __tablename__ = "source_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    status: str = Field(default="running", index=True)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    fetched_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    raw_config: dict = Field(default_factory=dict, sa_column=Column(JSON))


class ResearchItem(SQLModel, table=True):
    __tablename__ = "research_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    source_type: str = Field(index=True)
    source_url: str = "manual://local-note"
    title: str
    summary: str = Field(sa_column=Column(Text))
    sentiment: str = Field(default="neutral", index=True)
    confidence: float = Field(default=0.6, sa_column=Column(Float))
    captured_at: datetime = Field(default_factory=utc_now, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class FitScore(SQLModel, table=True):
    __tablename__ = "fit_scores"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    total: float = Field(index=True)
    hard_blocked: bool = Field(default=False, index=True)
    details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class InterviewPrep(SQLModel, table=True):
    __tablename__ = "interview_prep"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    jd_summary: str = Field(sa_column=Column(Text))
    skill_gaps: str = Field(sa_column=Column(Text))
    resume_points: str = Field(sa_column=Column(Text))
    star_stories: str = Field(sa_column=Column(Text))
    questions_to_ask: str = Field(sa_column=Column(Text))
    core_pitch: str = Field(default="", sa_column=Column(Text))
    communication_draft: str = Field(sa_column=Column(Text))
    tailored_resume: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class Draft(SQLModel, table=True):
    __tablename__ = "drafts"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = Field(default=None, foreign_key="jobs.id", index=True)
    kind: str = Field(default="message", index=True)
    channel: str = Field(default="manual", index=True)
    content: str = Field(sa_column=Column(Text))
    status: str = Field(default="draft", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profile"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Public template defaults stay empty so the repository does not encode one
    # person's location, salary, role, or experience profile.
    target_titles: str = Field(default="")
    target_cities: str = Field(default="")
    salary_min_k: float = 0
    salary_max_k: float = 0
    skills: str = Field(default="")
    strengths: str = Field(default="")
    work_experience: str = Field(
        default="请填写真实公司、项目、指标和成果。",
        sa_column=Column(Text),
    )
    dealbreakers: str = Field(default="")
    commute_preferences: str = Field(default="")
    weights: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utc_now)


class FollowUpTask(SQLModel, table=True):
    __tablename__ = "follow_up_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = Field(default=None, foreign_key="jobs.id", index=True)
    title: str
    status: str = Field(default="todo", index=True)
    due_date: Optional[date] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ApplicationEvent(SQLModel, table=True):
    __tablename__ = "application_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    event_type: str = Field(index=True)
    event_date: date = Field(index=True)
    channel: Optional[str] = None
    note: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class InterviewLog(SQLModel, table=True):
    """单场面试的复盘记录：按岗位、按轮次累积，构成可追溯、可迭代的面试闭环。

    机会评分是面试后的人工自评（6 维明细存 score_details，前端求和写入
    opportunity_score 与 conclusion），与面试前的 JD 评分 FitScore 互不影响。
    """

    __tablename__ = "interview_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    round: str = Field(default="一面", index=True)  # 轮次：一面/二面/HR面/复试……
    interview_date: Optional[date] = Field(default=None, index=True)
    interviewer: Optional[str] = None  # 面试官身份
    real_picture: str = Field(default="", sa_column=Column(Text))  # 岗位真实画像 + 对方想招什么人
    opportunity_score: Optional[float] = Field(default=None, index=True)  # 6 维自评求和 /100
    conclusion: str = Field(default="", index=True)  # 重点推进/继续观察/保底/放弃
    score_details: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 6 维明细
    qa_review: str = Field(default="", sa_column=Column(Text))  # 面试问题复盘
    weaknesses: str = Field(default="", sa_column=Column(Text))  # 暴露的短板
    next_actions: str = Field(default="", sa_column=Column(Text))  # 下一步：简历改哪句/补案例/追问
    follow_up: str = Field(default="", sa_column=Column(Text))  # 跟进话术/动作
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)
