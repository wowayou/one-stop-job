from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..models import Company, Job, ResearchItem, UserProfile


DEFAULT_WEIGHTS = {
    "role_match": 25,
    "salary_city": 15,
    "growth": 15,
    "stability": 15,
    "reputation": 10,
    "commute_rest": 10,
    "interview_roi": 10,
}


@dataclass
class ScoreResult:
    total: float
    hard_blocked: bool
    details: dict


def _tokens(text: str | None) -> list[str]:
    return [t.strip().lower() for t in re.split(r"[,，、;/\s]+", text or "") if t.strip()]


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    lower = haystack.lower()
    return any(needle and needle.lower() in lower for needle in needles)


def _ratio_matches(haystack: str, needles: list[str]) -> float:
    if not needles:
        return 0.5
    matches = sum(1 for needle in needles if needle and needle in haystack.lower())
    return min(1.0, matches / max(1, min(len(needles), 5)))


ROLE_KEYWORD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("seo", ("seo", "搜索优化", "谷歌优化", "google seo", "google优化", "网站优化", "关键词优化")),
    ("site", ("独立站", "官网", "网站", "wordpress", "shopify")),
    ("trade", ("外贸", "跨境", "出海", "海外市场", "国际站")),
    ("operation", ("运营", "推广", "增长", "数据分析")),
)
ROLE_CORE_GROUPS = {"seo", "site", "trade"}
SALES_KEYWORDS = ("销售", "电销", "地推", "客户经理", "商务bd", "招商")


def _role_groups(text: str | None) -> set[str]:
    lower = (text or "").lower()
    return {name for name, keywords in ROLE_KEYWORD_GROUPS if any(keyword in lower for keyword in keywords)}


def _role_domain_ratio(title: str, context: str, profile: UserProfile) -> float:
    profile_text = " ".join(filter(None, [profile.target_titles, profile.skills, profile.strengths]))
    desired_groups = _role_groups(profile_text)
    if not desired_groups:
        return 0.0

    title_hits = _role_groups(title) & desired_groups
    context_hits = _role_groups(context) & desired_groups
    title_core_hits = title_hits & ROLE_CORE_GROUPS
    has_operation = "operation" in title_hits

    if len(title_hits) >= 4:
        title_score = 0.96
    elif len(title_hits) >= 3:
        title_score = 0.9 if title_core_hits else 0.7
    elif len(title_hits) == 2:
        if title_core_hits and has_operation:
            title_score = 0.82
        elif len(title_core_hits) >= 2:
            title_score = 0.78
        else:
            title_score = 0.58
    elif len(title_hits) == 1:
        title_score = 0.58 if title_core_hits else 0.32
    else:
        title_score = 0.0

    context_only_hits = context_hits - title_hits
    context_score = min(0.18, len(context_only_hits) * 0.07 + (0.04 if context_hits & ROLE_CORE_GROUPS else 0.0))
    ratio = min(1.0, title_score + context_score)

    if _contains_any(title, SALES_KEYWORDS) and not (title_hits & {"seo", "site"}):
        ratio = min(ratio, 0.45 if has_operation else 0.32)
    return ratio


def _role_match_ratio(job: Job, profile: UserProfile, target_titles: list[str], profile_skills: list[str]) -> float:
    title = (job.title or "").lower()
    context = " ".join(filter(None, [job.skills, job.description, job.experience, job.degree])).lower()
    job_text = " ".join(filter(None, [title, context, job.company_name, job.area, job.salary_text])).lower()

    exact_title_ratio = _ratio_matches(title, target_titles)
    domain_ratio = _role_domain_ratio(title, context, profile)
    skill_ratio = _ratio_matches(job_text, profile_skills)
    skill_supported_ratio = min(1.0, domain_ratio + skill_ratio * 0.12)
    return max(exact_title_ratio, skill_supported_ratio, skill_ratio * 0.8)


def score_job(job: Job, company: Company | None, research_items: list[ResearchItem], profile: UserProfile) -> ScoreResult:
    weights = {**DEFAULT_WEIGHTS, **(profile.weights or {})}
    job_text = " ".join(
        filter(
            None,
            [job.title, job.company_name, job.skills, job.description, job.experience, job.degree, job.area, job.salary_text],
        )
    ).lower()
    target_titles = _tokens(profile.target_titles)
    target_cities = _tokens(profile.target_cities)
    profile_skills = _tokens(profile.skills)
    dealbreakers = _tokens(profile.dealbreakers)

    hard_reasons = []
    if target_cities and job.city and not _contains_any(job.city + " " + (job.area or ""), target_cities):
        hard_reasons.append("城市不在目标范围")
    if profile.salary_min_k and job.salary_max_k and job.salary_max_k < profile.salary_min_k:
        hard_reasons.append("薪资上限低于最低期望")
    if dealbreakers and _contains_any(job_text, dealbreakers):
        hard_reasons.append("命中硬性排除项")

    role_ratio = _role_match_ratio(job, profile, target_titles, profile_skills)
    role_match = round(weights["role_match"] * role_ratio, 1)

    salary_ok = 0.5
    has_salary_preference = profile.salary_min_k > 0 or profile.salary_max_k > 0
    if job.salary_avg_k and has_salary_preference:
        salary_max_k = profile.salary_max_k or float("inf")
        if profile.salary_min_k <= job.salary_avg_k <= salary_max_k:
            salary_ok = 1.0
        elif job.salary_max_k and job.salary_max_k >= profile.salary_min_k:
            salary_ok = 0.75
        else:
            salary_ok = 0.25
    city_ok = 1.0 if not target_cities or _contains_any((job.city or "") + " " + (job.area or ""), target_cities) else 0.25
    salary_city = round(weights["salary_city"] * ((salary_ok + city_ok) / 2), 1)

    research_text = " ".join([f"{item.title} {item.summary} {item.sentiment}" for item in research_items]).lower()
    positive = sum(1 for item in research_items if item.sentiment == "positive")
    negative = sum(1 for item in research_items if item.sentiment == "negative")
    evidence_count = len(research_items)

    growth_keywords = ["增长", "出海", "独立站", "seo", "品牌", "新业务", "数据", "内容", "跨境"]
    growth_ratio = max(_ratio_matches(job_text, growth_keywords), 0.55 if evidence_count == 0 else 0.45 + positive * 0.12)
    growth = round(min(weights["growth"], weights["growth"] * growth_ratio), 1)

    stability_base = 0.65
    if company and company.risk_level == "low":
        stability_base = 0.9
    elif company and company.risk_level == "high":
        stability_base = 0.35
    if negative:
        stability_base -= min(0.3, negative * 0.12)
    stability = round(max(0, weights["stability"] * stability_base), 1)

    reputation_base = 0.6 if evidence_count == 0 else 0.65 + positive * 0.08 - negative * 0.15
    if any(k in research_text for k in ["拖欠", "裁员", "加班严重", "pua", "避雷"]):
        reputation_base -= 0.25
    reputation = round(max(0, min(weights["reputation"], weights["reputation"] * reputation_base)), 1)

    commute_keywords = ["双休", "不加班", "弹性", "五险一金", "地铁", "远程"]
    commute_ratio = 0.55 + min(0.45, sum(1 for k in commute_keywords if k in job_text) * 0.12)
    if _contains_any(job_text, ["大小周", "单休", "经常加班"]):
        commute_ratio = 0.25
    commute_rest = round(weights["commute_rest"] * commute_ratio, 1)

    roi_base = 0.55 + role_ratio * 0.25 + (salary_ok - 0.5) * 0.2
    if job.recruiter_is_hr:
        roi_base += 0.05
    interview_roi = round(max(0, min(weights["interview_roi"], weights["interview_roi"] * roi_base)), 1)

    subtotal = role_match + salary_city + growth + stability + reputation + commute_rest + interview_roi
    hard_blocked = bool(hard_reasons)
    total = round(min(100, subtotal * (0.55 if hard_blocked else 1)), 1)

    return ScoreResult(
        total=total,
        hard_blocked=hard_blocked,
        details={
            "hard_reasons": hard_reasons,
            "dimensions": {
                "role_match": {"score": role_match, "weight": weights["role_match"], "note": "标题优先识别 SEO/独立站/外贸/运营组合，技能和 JD 作为补充"},
                "salary_city": {"score": salary_city, "weight": weights["salary_city"], "note": "薪资均值/上限与期望区间、城市与目标区域的匹配"},
                "growth": {"score": growth, "weight": weights["growth"], "note": "岗位内容里的增长、出海、独立站、数据等成长信号"},
                "stability": {"score": stability, "weight": weights["stability"], "note": "公司风险等级和负面调研证据修正"},
                "reputation": {"score": reputation, "weight": weights["reputation"], "note": "公司调研证据中的正负口碑倾向"},
                "commute_rest": {"score": commute_rest, "weight": weights["commute_rest"], "note": "双休、弹性、通勤便利或单休加班等生活质量信号"},
                "interview_roi": {"score": interview_roi, "weight": weights["interview_roi"], "note": "岗位匹配、薪资收益与准备投入的综合估计"},
            },
        },
    )
