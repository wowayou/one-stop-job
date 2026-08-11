from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Optional

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from .services.domain import (
    APPLICATION_EVENT_TYPES,
    COMPANY_RISK_LEVELS,
    FOLLOWUP_STATUSES,
    JOB_STATUSES,
    RECRUITMENT_STATUSES,
    RESEARCH_SENTIMENTS,
    validate_choice,
    validate_optional_choice,
    validate_optional_text,
    validate_required_text,
)


class JobCreate(SQLModel):
    title: str
    company_name: str
    source: str = "manual"
    url: Optional[str] = None
    salary_text: Optional[str] = None
    city: Optional[str] = None
    area: Optional[str] = None
    experience: Optional[str] = None
    degree: Optional[str] = None
    skills: Optional[str] = None
    description: Optional[str] = None
    recruiter: Optional[str] = None
    published_at: Optional[date] = None
    recruitment_status: Optional[str] = None

    _validate_title = field_validator("title")(lambda cls, value: validate_required_text("title", value))
    _validate_company_name = field_validator("company_name")(lambda cls, value: validate_required_text("company_name", value))
    _normalize_optional = field_validator(
        "source",
        "url",
        "salary_text",
        "city",
        "area",
        "experience",
        "degree",
        "skills",
        "description",
        "recruiter",
        mode="before",
    )(lambda cls, value: validate_optional_text("text", value) if isinstance(value, str) or value is None else value)
    _validate_recruitment_status = field_validator("recruitment_status")(
        lambda cls, value: validate_optional_choice("recruitment_status", value, RECRUITMENT_STATUSES)
    )


class JobUpdate(SQLModel):
    title: Optional[str] = None
    company_name: Optional[str] = None
    url: Optional[str] = None
    salary_text: Optional[str] = None
    city: Optional[str] = None
    area: Optional[str] = None
    experience: Optional[str] = None
    degree: Optional[str] = None
    skills: Optional[str] = None
    description: Optional[str] = None
    recruiter: Optional[str] = None
    published_at: Optional[date] = None
    recruitment_status: Optional[str] = None
    status: Optional[str] = None
    favorite: Optional[bool] = None

    _normalize_optional = field_validator(
        "url",
        "salary_text",
        "city",
        "area",
        "experience",
        "degree",
        "skills",
        "description",
        "recruiter",
        mode="before",
    )(lambda cls, value: validate_optional_text("text", value) if isinstance(value, str) or value is None else value)
    _strip_title = field_validator("title", "company_name", mode="before")(
        lambda cls, value: value.strip() if isinstance(value, str) else value
    )
    _validate_recruitment_status = field_validator("recruitment_status")(
        lambda cls, value: validate_optional_choice("recruitment_status", value, RECRUITMENT_STATUSES)
    )
    _validate_status = field_validator("status")(lambda cls, value: validate_optional_choice("status", value, JOB_STATUSES))


class JobBulkUpdate(SQLModel):
    ids: list[int]
    status: Optional[str] = None
    favorite: Optional[bool] = None

    _validate_status = field_validator("status")(lambda cls, value: validate_optional_choice("status", value, JOB_STATUSES))


class WeChatCollectRequest(SQLModel):
    # text：元宝整段回答 / 链接列表（自动正则抽链）；urls：已拆好的链接；
    # bodies：抓取失败时按 {url: 正文} 手动粘贴兜底。
    text: Optional[str] = None
    urls: Optional[list[str]] = None
    bodies: Optional[dict[str, str]] = None


class IngestRequest(SQLModel):
    # 文本和/或截图 → 抽取候选写入聊天；**默认不入库**，用户在聊天里点确认才写 Job 表。
    text: Optional[str] = Field(default=None, max_length=20000)
    image_data_url: Optional[str] = Field(default=None, max_length=6_000_000)

    _normalize_text = field_validator("text", mode="before")(
        lambda cls, value: validate_optional_text("text", value) if isinstance(value, str) or value is None else value
    )

    @field_validator("image_data_url")
    @classmethod
    def validate_ingest_image(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            if not (info.data.get("text") or "").strip():
                raise ValueError("必须提供 text 或 image_data_url 之一")
            return None
        allowed = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
        if not value.startswith(allowed):
            raise ValueError("image_data_url must be a PNG, JPEG, or WebP data URL")
        try:
            decoded = base64.b64decode(value.split(",", 1)[1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image_data_url contains invalid base64 data") from exc
        if not decoded:
            raise ValueError("image_data_url cannot be empty")
        return value


class CandidatesCommitRequest(SQLModel):
    """把聊天消息里的候选岗位真正写入 Job 表（仅用户明确选中的 indexes）。"""

    message_id: int
    indexes: list[int] = Field(default_factory=list)


class ResearchItemCreate(SQLModel):
    source_type: str
    title: str
    summary: str
    source_url: Optional[str] = None
    sentiment: str = "neutral"
    confidence: float = 0.6
    captured_at: Optional[datetime] = None

    _validate_source_type = field_validator("source_type")(lambda cls, value: validate_required_text("source_type", value))
    _validate_title = field_validator("title")(lambda cls, value: validate_required_text("title", value))
    _validate_summary = field_validator("summary")(lambda cls, value: validate_required_text("summary", value))
    _normalize_source_url = field_validator("source_url", mode="before")(
        lambda cls, value: validate_optional_text("source_url", value) if isinstance(value, str) or value is None else value
    )
    _validate_sentiment = field_validator("sentiment")(lambda cls, value: validate_choice("sentiment", value, RESEARCH_SENTIMENTS))


class CompanyUpdate(SQLModel):
    website: Optional[str] = None
    industry: Optional[str] = None
    size: Optional[str] = None
    stage: Optional[str] = None
    location: Optional[str] = None
    risk_level: Optional[str] = None
    notes: Optional[str] = None

    _normalize_optional = field_validator("website", "industry", "size", "stage", "location", "notes", mode="before")(
        lambda cls, value: validate_optional_text("text", value) if isinstance(value, str) or value is None else value
    )
    _validate_risk_level = field_validator("risk_level")(
        lambda cls, value: validate_optional_choice("risk_level", value, COMPANY_RISK_LEVELS)
    )


class ProfileUpdate(SQLModel):
    target_titles: Optional[str] = None
    target_cities: Optional[str] = None
    salary_min_k: Optional[float] = None
    salary_max_k: Optional[float] = None
    skills: Optional[str] = None
    strengths: Optional[str] = None
    work_experience: Optional[str] = None
    dealbreakers: Optional[str] = None
    commute_preferences: Optional[str] = None
    weights: Optional[dict] = None

    _normalize_optional = field_validator(
        "target_titles",
        "target_cities",
        "skills",
        "strengths",
        "work_experience",
        "dealbreakers",
        "commute_preferences",
        mode="before",
    )(lambda cls, value: validate_optional_text("text", value) if isinstance(value, str) or value is None else value)


class ChatThreadCreate(SQLModel):
    kind: str = "general"
    job_id: Optional[int] = None
    title: Optional[str] = None

    _validate_kind = field_validator("kind")(lambda cls, value: validate_choice("kind", value, {"general", "job", "ingest"}))
    _normalize_title = field_validator("title", mode="before")(
        lambda cls, value: validate_optional_text("title", value) if isinstance(value, str) or value is None else value
    )


class ChatThreadUpdate(SQLModel):
    title: str = Field(max_length=120)

    _validate_title = field_validator("title")(
        lambda cls, value: validate_required_text("title", value)
    )


class ChatThreadBatchDeleteRequest(SQLModel):
    """批量删除聊天线程（连同消息与截图附件），一次最多 100 个。"""

    ids: list[int] = Field(default_factory=list)


class ChatMessageCreate(SQLModel):
    content: str = Field(max_length=12000)
    image_data_url: Optional[str] = Field(default=None, max_length=6_000_000)
    image_name: Optional[str] = Field(default=None, max_length=180)
    # 默认 True：沿用「配置了 AI 就用」的现状；False 时这一条只走规则引擎，即便全局 ai.enabled=true。
    use_ai: bool = True
    # 在入库候选线索里指名「这条问的是第几个候选」（0 基）。候选没入库前没有 Job 记录，
    # 线程本身挂不住岗位，只能靠它锚定；省略时单候选直接用它、多候选默认第一个。
    candidate_index: Optional[int] = Field(default=None, ge=0, le=49)

    _validate_content = field_validator("content")(lambda cls, value: validate_required_text("content", value))
    _normalize_image_name = field_validator("image_name", mode="before")(
        lambda cls, value: validate_optional_text("image_name", value) if isinstance(value, str) or value is None else value
    )

    @field_validator("image_data_url")
    @classmethod
    def validate_image_data_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        allowed = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
        if not value.startswith(allowed):
            raise ValueError("image_data_url must be a PNG, JPEG, or WebP data URL")
        try:
            decoded = base64.b64decode(value.split(",", 1)[1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image_data_url contains invalid base64 data") from exc
        if not decoded:
            raise ValueError("image_data_url cannot be empty")
        return value


class DraftCreate(SQLModel):
    job_id: Optional[int] = None
    kind: str = "message"
    channel: str = "manual"
    content: str
    status: str = "draft"


class FollowUpTaskCreate(SQLModel):
    job_id: Optional[int] = None
    title: str
    status: str = "todo"
    due_date: Optional[date] = None

    _validate_title = field_validator("title")(lambda cls, value: validate_required_text("title", value))
    _validate_status = field_validator("status")(lambda cls, value: validate_choice("status", value, FOLLOWUP_STATUSES))


class FollowUpTaskUpdate(SQLModel):
    title: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[date] = None

    _normalize_title = field_validator("title", mode="before")(
        lambda cls, value: validate_optional_text("title", value) if isinstance(value, str) or value is None else value
    )
    _validate_status = field_validator("status")(lambda cls, value: validate_optional_choice("status", value, FOLLOWUP_STATUSES))


class InterviewLogCreate(SQLModel):
    round: str = "一面"
    interview_date: Optional[date] = None
    interviewer: Optional[str] = None
    real_picture: str = ""
    opportunity_score: Optional[float] = None
    conclusion: str = ""
    score_details: dict = Field(default_factory=dict)
    qa_review: str = ""
    weaknesses: str = ""
    next_actions: str = ""
    follow_up: str = ""


class InterviewLogUpdate(SQLModel):
    round: Optional[str] = None
    interview_date: Optional[date] = None
    interviewer: Optional[str] = None
    real_picture: Optional[str] = None
    opportunity_score: Optional[float] = None
    conclusion: Optional[str] = None
    score_details: Optional[dict] = None
    qa_review: Optional[str] = None
    weaknesses: Optional[str] = None
    next_actions: Optional[str] = None
    follow_up: Optional[str] = None


class ApplicationEventCreate(SQLModel):
    event_type: str
    event_date: date
    channel: Optional[str] = None
    note: str = ""

    _validate_event_type = field_validator("event_type")(lambda cls, value: validate_choice("event_type", value, APPLICATION_EVENT_TYPES))
    _normalize_channel = field_validator("channel", mode="before")(
        lambda cls, value: validate_optional_text("channel", value) if isinstance(value, str) or value is None else value
    )
    _normalize_note = field_validator("note", mode="before")(
        lambda cls, value: (validate_optional_text("note", value) or "") if isinstance(value, str) or value is None else value
    )


class AppConfigUpdate(SQLModel):
    config: dict


class AiCredentialUpdate(SQLModel):
    """写入本机 `.env` 的 AI provider 密钥。

    刻意不加 field_validator/长度约束：value 的格式校验（ASCII、无控制字符、非空）全部放在
    端点函数体内手工做，用 `HTTPException(400, detail=...)` 报错——绝不用 pydantic 校验器，
    因为校验失败时 FastAPI 的 RequestValidationError 处理器会把 `exc.errors()`（含原始
    input 明文）编码进响应体，那会把密钥泄露回客户端/日志。
    """

    env_name: str
    value: str
