from __future__ import annotations

from collections.abc import Iterable


JOB_STATUSES = ("new", "researching", "fit", "applied", "interview", "offer", "rejected", "archived")
RECRUITMENT_STATUSES = ("active", "closed", "unknown")
FOLLOWUP_STATUSES = ("todo", "done")
RESEARCH_SENTIMENTS = ("positive", "neutral", "negative")
COMPANY_RISK_LEVELS = ("low", "medium", "high", "unknown")
APPLICATION_EVENT_TYPES = ("applied", "reply", "interview_invite", "rejected", "offer", "withdrawn")


def validate_choice(name: str, value: str, allowed: Iterable[str]) -> str:
    normalized = value.strip()
    if normalized not in allowed:
        choices = ", ".join(allowed)
        raise ValueError(f"{name} must be one of: {choices}")
    return normalized


def validate_optional_choice(name: str, value: str | None, allowed: Iterable[str]) -> str | None:
    if value is None:
        return None
    return validate_choice(name, value, allowed)


def validate_required_text(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} cannot be empty")
    return normalized


def validate_optional_text(_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
