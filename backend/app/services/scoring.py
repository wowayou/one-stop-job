from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..models import Company, Job, ResearchItem, UserProfile
from .reach_policy import RoleClassification, classify_role, family_level_bonus, normalize_level


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

# ==== 方向证据信号（role_match 内部梯度）====
# 为什么需要：100 分里 growth/stability/reputation/commute_rest/interview_roi 在缺公司调研证据
# 时取近似常量，头部岗位会挤在极窄的分数带里（实测同一批「独立站运营」全部同分），排序没有信息量。
# 解法：把标题基准档位调低，留出空间，再用岗位**自带字段**（标题 / 技能标签 / 经验要求）的方向证据
# 加减分。只读岗位自身数据，不虚构公司调研、不发外部请求，也不看 source（红线 §3.8）。
# 注意：偏离主线的信号走**软降权**，绝不塞进 dealbreakers —— 后者是总分 ×0.55 的硬阻断，
# 会连带误伤「外贸 + 社媒」这类复合但仍在方向上的岗位。
SEO_SIGNALS = (
    "seo", "谷歌优化", "google优化", "搜索优化", "搜索引擎优化",
    "关键词", "自然流量", "外链", "tdk", "站内优化", "网站优化",
)
SITE_OWNERSHIP_SIGNALS = ("独立站", "官网", "网站运营", "网站维护", "wordpress", "shopify", "建站")
B2B_INDUSTRIAL_SIGNALS = (
    "b2b", "to b", "工业", "机械", "制造", "化工", "建材", "建筑材料",
    "设备", "询盘", "进出口", "工厂", "供应链", "企业服务",
)
DATA_TOOL_SIGNALS = (
    "ga4", "google analytics", "analytics", "gsc", "search console",
    "数据分析", "数据复盘", "转化率", "埋点", "报表",
)
ENGLISH_MARKET_SIGNALS = ("英语", "英文", "english", "欧洲", "北美", "海外市场")

# 软降权信号：平台店铺 / 纯社媒达人 / 投放竞价。命中越多、越出现在标题里，扣得越重。
PLATFORM_SHOP_NOISE = (
    "亚马逊", "amazon", "temu", "速卖通", "shein", "ebay", "lazada", "shopee",
    "阿里国际站", "listing", "店铺", "爆款", "选品", "上架", "平台运营", "电商运营",
)
SOCIAL_INFLUENCER_NOISE = (
    "社媒", "社交媒体", "tiktok", "抖音", "facebook", "instagram", "youtube",
    "达人", "kol", "红人", "短视频", "直播", "剪辑", "拍摄", "新媒体",
)
AD_BUY_NOISE = (
    "广告投放", "信息流", "竞价", "百度推广", "360推广", "sem",
    "google ads", "投放优化", "roi", "gmv",
)
# 经验要求梯度：目标带是 1-3 年（见个人决策规则「当前 offer 概率更高的岗位带」）；
# 5 年以上通常伴随预算 / ROI / 从 0 全盘的经理职责，冷投转化明显更低。
SENIOR_EXPERIENCE_SIGNALS = ("5-10年", "10年以上", "8年以上", "5年以上")
JUNIOR_EXPERIENCE_SIGNALS = ("1-3年", "经验不限", "1年以内", "3-5年", "在校", "应届")


def _signal_hits(text: str, needles: tuple[str, ...]) -> int:
    """命中的**不同**信号个数（不是出现次数），避免同一个词重复堆叠分数。"""
    return sum(1 for needle in needles if needle in text)


def _direction_bonus(title: str, context: str) -> float:
    """主线方向的正向证据加成，按证据强度分层，全部上限封顶。

    层级参照个人决策规则的 Direction Bands：SEO 是主职责 > B2B 工业出海 >
    数据/工具可见性 > 英文市场。标题里的证据比技能标签里的证据更强（标题代表主职责）。
    """
    text = f"{title} {context}"
    bonus = 0.0
    if _signal_hits(title, SEO_SIGNALS):
        bonus += 0.12
    elif _signal_hits(context, SEO_SIGNALS):
        bonus += 0.06
    if _signal_hits(title, SITE_OWNERSHIP_SIGNALS):
        bonus += 0.05
    bonus += min(0.10, _signal_hits(text, B2B_INDUSTRIAL_SIGNALS) * 0.04)
    bonus += min(0.08, _signal_hits(text, DATA_TOOL_SIGNALS) * 0.04)
    bonus += min(0.05, _signal_hits(text, ENGLISH_MARKET_SIGNALS) * 0.025)
    if _contains_any(context, JUNIOR_EXPERIENCE_SIGNALS):
        bonus += 0.03
    return bonus


def _direction_penalty(title: str, context: str) -> float:
    """偏离主线的软降权（**不是**硬阻断）：分档累加，每档封顶。

    标题命中权重远高于标签命中：标题写「亚马逊运营」是主职责，标签里出现「亚马逊」
    往往只是"顺带了解"。这样「外贸 + 社媒」复合岗只被轻扣，纯店铺 / 纯达人岗才被压到尾部。
    """
    penalty = 0.0
    for needles, title_weight, context_weight, cap in (
        (PLATFORM_SHOP_NOISE, 0.12, 0.035, 0.20),
        (SOCIAL_INFLUENCER_NOISE, 0.10, 0.030, 0.18),
        (AD_BUY_NOISE, 0.10, 0.030, 0.15),
    ):
        penalty += min(cap, _signal_hits(title, needles) * title_weight + _signal_hits(context, needles) * context_weight)
    if _contains_any(context, SENIOR_EXPERIENCE_SIGNALS):
        penalty += 0.06
    return penalty


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

    # 档位刻意压低（原先 0.96/0.90/0.82…），把顶部空间让给 _direction_bonus：
    # 只靠标题命中数最高只能到中上游，要进前列必须有 SEO / B2B / 数据 / 英文的具体证据。
    if len(title_hits) >= 4:
        title_score = 0.86
    elif len(title_hits) >= 3:
        title_score = 0.78 if title_core_hits else 0.6
    elif len(title_hits) == 2:
        if title_core_hits and has_operation:
            title_score = 0.56
        elif len(title_core_hits) >= 2:
            title_score = 0.66
        else:
            title_score = 0.46
    elif len(title_hits) == 1:
        title_score = 0.46 if title_core_hits else 0.26
    else:
        title_score = 0.0

    context_only_hits = context_hits - title_hits
    context_score = min(0.18, len(context_only_hits) * 0.07 + (0.04 if context_hits & ROLE_CORE_GROUPS else 0.0))
    ratio = min(1.0, title_score + context_score)

    # 只给"标题已经落在方向上"的岗位加成，避免无关岗位靠几个技能标签翻身进前列。
    if title_hits:
        ratio = min(1.0, ratio + _direction_bonus(title, context))

    # 销售型岗位压低。豁免条件收紧为"既有 SEO/独立站，又有运营类主职责词"——
    # 只有 SEO/独立站还不够：「外贸独立站、Google、Yandex销售代表」这类建站商销售岗
    # 标题里带独立站，但主职责是获客，属于"尽量避免"带，不能靠关键词蹭进前列。
    if _contains_any(title, SALES_KEYWORDS) and not (title_hits & {"seo", "site"} and has_operation):
        ratio = min(ratio, 0.4 if has_operation else 0.26)
    return ratio


def _role_match_ratio(
    job: Job,
    profile: UserProfile,
    target_titles: list[str],
    profile_skills: list[str],
    *,
    reach_cfg: dict | None = None,
) -> tuple[float, RoleClassification | None]:
    title = (job.title or "").lower()
    context = " ".join(filter(None, [job.skills, job.description, job.experience, job.degree])).lower()
    job_text = " ".join(filter(None, [title, context, job.company_name, job.area, job.salary_text])).lower()

    exact_title_ratio = _ratio_matches(title, target_titles)
    domain_ratio = _role_domain_ratio(title, context, profile)
    skill_ratio = _ratio_matches(job_text, profile_skills)
    skill_supported_ratio = min(1.0, domain_ratio + skill_ratio * 0.12)
    base_ratio = max(exact_title_ratio, skill_supported_ratio, skill_ratio * 0.8)
    classification = None
    if reach_cfg:
        classification = classify_role(title, context, reach_cfg)
        evidence = 0.72 if classification.recommendation == "推荐投递" else 0.54
        if classification.family_key is None:
            evidence = 0.28
        if classification.recommendation == "排除":
            evidence = 0.08
        evidence += family_level_bonus(classification, normalize_level(reach_cfg.get("level")))
        base_ratio = max(evidence, exact_title_ratio * 0.9, skill_supported_ratio)
    # 软降权放在取 max 之后，才能同时约束"靠 target_titles 精确命中"和"靠技能命中"两条路径。
    return max(0.0, min(1.0, base_ratio - _direction_penalty(title, context))), classification


def score_job(
    job: Job,
    company: Company | None,
    research_items: list[ResearchItem],
    profile: UserProfile,
    *,
    reach_cfg: dict | None = None,
) -> ScoreResult:
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

    role_ratio, reach = _role_match_ratio(job, profile, target_titles, profile_skills, reach_cfg=reach_cfg)
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
            "reach": reach.as_dict() if reach else None,
            "dimensions": {
                "role_match": {"score": role_match, "weight": weights["role_match"], "note": "标题优先识别 SEO/独立站/外贸/运营组合；再按 SEO 主职责、B2B 工业出海、数据工具、英文市场、经验带做梯度加成，平台店铺/纯社媒达人/投放竞价软降权"},
                "salary_city": {"score": salary_city, "weight": weights["salary_city"], "note": "薪资均值/上限与期望区间、城市与目标区域的匹配"},
                "growth": {"score": growth, "weight": weights["growth"], "note": "岗位内容里的增长、出海、独立站、数据等成长信号"},
                "stability": {"score": stability, "weight": weights["stability"], "note": "公司风险等级和负面调研证据修正"},
                "reputation": {"score": reputation, "weight": weights["reputation"], "note": "公司调研证据中的正负口碑倾向"},
                "commute_rest": {"score": commute_rest, "weight": weights["commute_rest"], "note": "双休、弹性、通勤便利或单休加班等生活质量信号"},
                "interview_roi": {"score": interview_roi, "weight": weights["interview_roi"], "note": "岗位匹配、薪资收益与准备投入的综合估计"},
            },
        },
    )


def score_job_configured(job: Job, company: Company | None, research_items: list[ResearchItem], profile: UserProfile) -> ScoreResult:
    """生产评分入口：在纯 ``score_job`` 外薄薄接入本机相邻度配置。"""
    from ..config import get_settings

    return score_job(job, company, research_items, profile, reach_cfg=get_settings().reach_config)
