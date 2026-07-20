from __future__ import annotations

import re
from typing import Any

from ..models import Job, UserProfile
from .normalizer import parse_salary


_SPLIT_PATTERN = re.compile(r"[,，、;；/|\n]+")
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_POLICY_KEYWORDS = (
    "B2B",
    "SEO",
    "Organic Search",
    "英文网站",
    "独立站",
    "网站运营",
    "海外推广",
    "Digital Marketing",
    "内容运营",
    "数据分析",
    "页面转化",
    "询盘",
    "GEO",
    "AI 搜索",
    "AI SaaS Growth",
    "Google Ads",
    "Facebook Ads",
    "广告投放",
    "社媒",
    "平台店铺",
    "销售型",
    "一人全包",
)
_RISK_DIRECTION_KEYWORDS = {
    "GEO",
    "AI 搜索",
    "AI SaaS Growth",
    "Google Ads",
    "Facebook Ads",
    "广告投放",
    "社媒",
    "平台店铺",
    "销售型",
    "一人全包",
}
_DIRECTION_ALIASES = (
    {"Google Ads", "Facebook Ads", "广告投放", "信息流", "竞价"},
    {"社媒", "Facebook", "Instagram", "LinkedIn", "TikTok"},
    {"平台店铺", "Amazon", "Shopee", "TikTok Shop", "亚马逊"},
    {"销售型", "销售", "BD", "提成"},
)

_STRING_FIELDS = {"summary", "direction", "priority", "next_action", "action_text", "reply_draft"}
_LIST_FIELDS = {"uncertainties", "reasons", "risks", "hard_conditions"}
_PRIORITIES = {"A", "B", "C", "D", "待确认"}
_DIRECTIONS = {"核心优先", "邻近可接受", "谨慎试探", "机会观察", "尽量避免", "待确认"}


def _terms(value: str | None) -> list[str]:
    return [cleaned for part in _SPLIT_PATTERN.split(value or "") if len(cleaned := part.strip(" 。.\t")) >= 2]


def _contains_any(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _direction_hits(text: str, enabled_terms: list[str]) -> list[str]:
    hits = _contains_any(text, enabled_terms)
    lowered = text.lower()
    enabled_lower = {term.lower() for term in enabled_terms}
    for aliases in _DIRECTION_ALIASES:
        if not enabled_lower.intersection(alias.lower() for alias in aliases):
            continue
        hits.extend(alias for alias in aliases if alias.lower() in lowered)
    return list(dict.fromkeys(hits))


def _check(code: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "detail": detail}


def _policy_section(context: str, start_label: str, end_labels: tuple[str, ...]) -> str:
    start = re.search(rf"(?m)^{re.escape(start_label)}：?\s*$", context)
    if not start:
        return ""
    tail = context[start.end() :]
    ends = [match.start() for label in end_labels if (match := re.search(rf"(?m)^{re.escape(label)}：?\s*$", tail))]
    if heading := re.search(r"(?m)^#{1,6}\s+", tail):
        ends.append(heading.start())
    return tail[: min(ends)] if ends else tail[:3000]


def _keywords_in(text: str) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in _POLICY_KEYWORDS if keyword.lower() in lowered]


def _policy_values(context: str) -> dict[str, Any]:
    salary_match = re.search(r"目标薪资[^\n\d]*(\d+(?:\.\d+)?)\s*[Kk]\s*[-—–~至到]\s*(\d+(?:\.\d+)?)\s*[Kk]", context)
    absolute_match = re.search(r"低于\s*(\d+(?:\.\d+)?)\s*[Kk][^\n]*(?:不建议|不接受|不要)", context)
    location_match = re.search(r"(?m)^-\s*(?:地点|城市)[:：]\s*(.+)$", context)
    direction_match = re.search(r"(?m)^-\s*目标方向[:：]\s*(.+)$", context)
    locations: list[str] = []
    if location_match:
        cleaned = re.sub(r"(优先|可考虑|可以考虑|为主)", "", location_match.group(1))
        locations = [item.strip(" 。") for item in re.split(r"[,，、;；]", cleaned) if item.strip(" 。")]
    preferred = _policy_section(context, "优先推进", ("谨慎试探", "尽量避免", "Resume Positioning"))
    cautious = _policy_section(context, "谨慎试探", ("尽量避免", "Resume Positioning"))
    avoid = _policy_section(context, "尽量避免", ("Resume Positioning", "##"))
    preferred_keywords = _keywords_in(preferred)
    avoid_lines = "\n".join(
        line
        for line in context.splitlines()
        if re.search(r"(?:尽量避免|直接跳过|绕开|回避|纯[^，。；:]*(?:投放|社媒|平台|销售))", line, re.IGNORECASE)
    )
    avoid_keywords = [
        keyword
        for keyword in _keywords_in(f"{avoid}\n{avoid_lines}")
        if keyword not in preferred_keywords and keyword in _RISK_DIRECTION_KEYWORDS
    ]
    return {
        "salary_min": float(salary_match.group(1)) if salary_match else 0,
        "salary_max": float(salary_match.group(2)) if salary_match else 0,
        "absolute_salary_floor": float(absolute_match.group(1)) if absolute_match else 0,
        "target_cities": locations,
        "target_titles": _terms(direction_match.group(1)) if direction_match else [],
        "preferred_keywords": preferred_keywords,
        "cautious_keywords": [
            keyword for keyword in _keywords_in(cautious) if keyword not in preferred_keywords and keyword in _RISK_DIRECTION_KEYWORDS
        ],
        "avoid_keywords": avoid_keywords,
    }


def _job_text(job: Job | None, message: str) -> str:
    if job is None:
        return message
    return "\n".join(
        value
        for value in [job.title, job.company_name, job.salary_text, job.city, job.area, job.skills, job.description, message]
        if value
    )


def _is_link_without_evidence(message: str, job: Job | None) -> bool:
    if job is not None:
        return False
    links = _URL_PATTERN.findall(message)
    if not links:
        return False
    remainder = _URL_PATTERN.sub("", message).strip(" \n\t，。！？!?：:")
    return len(remainder) < 12


def build_rule_analysis(
    *,
    message: str,
    profile: UserProfile,
    job: Job | None,
    thread_kind: str,
    context_available: bool,
    image_attached: bool = False,
    policy_context: str = "",
) -> dict[str, Any]:
    """Run the deterministic pass before any model call.

    The pass intentionally uses only explicit profile fields, job facts and the
    submitted material. It never invents facts and remains useful when AI is off.
    """

    combined = _job_text(job, message)
    policy = _policy_values(policy_context)
    checks: list[dict[str, str]] = []
    confirmed_facts: list[dict[str, str]] = []
    uncertainties: list[str] = []
    reasons: list[str] = []
    risks: list[str] = []
    hard_conditions: list[str] = []

    if context_available:
        checks.append(_check("decision_context", "个人决策规则", "pass", "已加载本地只读规则与画像"))
    else:
        checks.append(_check("decision_context", "个人决策规则", "warn", "外部规则未连接，使用应用内画像字段"))
        risks.append("本地决策规则未连接，本次判断只覆盖应用内画像字段。")

    confirmed_facts.append({"text": f"已收到本次材料（{len(message)} 字）", "source": "用户输入"})
    if image_attached:
        confirmed_facts.append({"text": "已附带 1 张截图", "source": "用户上传"})
    if job is not None:
        confirmed_facts.append({"text": f"{job.company_name} · {job.title}", "source": "岗位库"})
        if job.salary_text:
            confirmed_facts.append({"text": f"薪资：{job.salary_text}", "source": "岗位库"})
        if job.city or job.area:
            confirmed_facts.append({"text": f"地点：{' · '.join(filter(None, [job.city, job.area]))}", "source": "岗位库"})

    dealbreaker_hits = _contains_any(combined, _terms(profile.dealbreakers))
    if dealbreaker_hits:
        detail = f"材料命中排除项：{'、'.join(dealbreaker_hits[:3])}"
        checks.append(_check("dealbreakers", "硬性排除", "fail", detail))
        hard_conditions.append(detail)
        risks.append(detail)
    elif profile.dealbreakers:
        checks.append(_check("dealbreakers", "硬性排除", "pass", "未发现已配置排除项的直接命中"))
    else:
        checks.append(_check("dealbreakers", "硬性排除", "unknown", "画像尚未配置排除项"))

    salary_floor = profile.salary_min_k if profile.salary_min_k > 0 else policy["salary_min"]
    absolute_salary_floor = policy["absolute_salary_floor"] or salary_floor
    parsed_salary = parse_salary(message) if job is None else {}
    observed_salary_min = job.salary_min_k if job is not None else parsed_salary.get("salary_min_k")
    observed_salary_max = job.salary_max_k if job is not None else parsed_salary.get("salary_max_k")
    if salary_floor > 0 and observed_salary_max is not None:
        if observed_salary_max < absolute_salary_floor:
            detail = f"岗位薪资上限 {observed_salary_max:g}K 低于当前绝对底线 {absolute_salary_floor:g}K"
            checks.append(_check("salary_floor", "薪资底线", "fail", detail))
            hard_conditions.append(detail)
            risks.append(detail)
        elif observed_salary_max < salary_floor:
            detail = f"岗位薪资上限 {observed_salary_max:g}K 低于正常目标 {salary_floor:g}K，只能按规则中的例外条件评估"
            checks.append(_check("salary_floor", "薪资底线", "warn", detail))
            risks.append(detail)
        elif observed_salary_min is not None:
            checks.append(_check("salary_floor", "薪资底线", "pass", "岗位薪资区间未低于已配置底线"))
        else:
            checks.append(_check("salary_floor", "薪资底线", "unknown", "岗位薪资结构仍需确认"))
            uncertainties.append("确认固定薪资、绩效口径与发薪结构。")
    elif thread_kind == "job":
        checks.append(_check("salary_floor", "薪资底线", "unknown", "画像底线或岗位薪资尚未完整配置"))
        uncertainties.append("确认薪资范围与个人底线。")

    target_cities = _terms(profile.target_cities) or policy["target_cities"]
    if job is not None and target_cities:
        location = " ".join(filter(None, [job.city, job.area]))
        if location and _contains_any(location, target_cities):
            checks.append(_check("target_city", "目标地点", "pass", f"岗位地点与目标地点相符：{location}"))
        elif location:
            checks.append(_check("target_city", "目标地点", "warn", f"岗位地点 {location} 不在已配置目标地点中"))
            risks.append("地点或通勤条件可能不符合当前目标。")
        else:
            checks.append(_check("target_city", "目标地点", "unknown", "岗位地点待确认"))
            uncertainties.append("确认办公地点、通勤与远程安排。")

    target_titles = _terms(profile.target_titles) or policy["target_titles"]
    preferred_hits = _contains_any(combined, policy["preferred_keywords"])
    cautious_hits = _direction_hits(combined, policy["cautious_keywords"])
    avoid_hits = _direction_hits(combined, policy["avoid_keywords"])
    if (target_titles or policy["preferred_keywords"]) and (job is not None or len(message) >= 80):
        role_haystack = " ".join(filter(None, [job.title, job.skills, job.description])) if job else combined
        matched_roles = _contains_any(role_haystack, target_titles)
        matched = list(dict.fromkeys(matched_roles + preferred_hits))
        if avoid_hits and not preferred_hits:
            detail = f"岗位材料命中当前尽量避免方向：{'、'.join(avoid_hits[:3])}"
            checks.append(_check("role_direction", "岗位方向", "fail", detail))
            hard_conditions.append(detail)
            risks.append(detail)
        elif matched:
            detail = f"命中目标方向：{'、'.join(matched[:3])}"
            if cautious_hits or avoid_hits:
                checks.append(_check("role_direction", "岗位方向", "warn", f"{detail}；同时含谨慎职责，需确认工作重心"))
                risks.append("岗位同时包含谨慎方向，需确认网站、SEO 或数据闭环是否为稳定主责。")
            else:
                checks.append(_check("role_direction", "岗位方向", "pass", detail))
            reasons.append("岗位信息与已配置目标方向存在直接匹配。")
        elif cautious_hits:
            checks.append(_check("role_direction", "岗位方向", "warn", f"命中谨慎试探方向：{'、'.join(cautious_hits[:3])}"))
            risks.append("岗位属于谨慎试探方向，只适合低成本确认工作重心。")
        else:
            checks.append(_check("role_direction", "岗位方向", "warn", "未发现与目标岗位名称的直接匹配"))
            risks.append("岗位方向与当前主线的关系需要进一步确认。")

    commute_match = re.search(r"通勤[^\d]{0,8}(\d+(?:\.\d+)?)\s*(小时|分钟)", combined)
    if commute_match:
        minutes = float(commute_match.group(1)) * (60 if commute_match.group(2) == "小时" else 1)
        if minutes > 75:
            detail = f"材料显示单程通勤约 {minutes:g} 分钟，超过 75 分钟警戒线"
            checks.append(_check("commute", "通勤成本", "warn", detail))
            risks.append(detail)

    if "单休" in combined or "大小周" in combined:
        checks.append(_check("work_schedule", "作息", "warn", "材料显示单休或大小周，需要按当前作息底线确认是否继续"))
        risks.append("作息稳定性不符合默认偏好。")

    link_only = _is_link_without_evidence(message, job)
    if image_attached:
        checks.append(_check("image_evidence", "截图材料", "unknown", "截图需要由支持视觉输入的已配置模型读取"))
        uncertainties.append("如果模型无法识别截图，请补充其中的关键文字。")
    elif link_only:
        checks.append(_check("evidence", "材料完整度", "unknown", "仅收到链接，当前版本不会自动抓取任意网页"))
        uncertainties.append("请粘贴 JD 正文、招聘方回复或上传清晰截图。")
    else:
        checks.append(_check("evidence", "材料完整度", "pass", "已有可分析的文字或岗位库事实"))

    failed = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] in {"warn", "unknown"}]
    role_pass = any(item["code"] == "role_direction" and item["status"] == "pass" for item in checks)

    if failed:
        priority, direction, next_action = "D", "尽量避免", "放弃"
        action_text = "先不要投入额外时间；如信息可能有误，只确认最关键的硬条件。"
        summary = "现有材料触发了已配置的硬性条件，当前不建议推进。"
    elif link_only:
        priority, direction, next_action = "待确认", "待确认", "补充信息"
        action_text = "粘贴 JD 正文、招聘方回复，或上传清晰截图后再判断。"
        summary = "目前只有链接，证据不足，暂时不能给出可靠结论。"
    elif job is None:
        priority, direction, next_action = "待确认", "待确认", "补充信息" if warnings else "继续沟通"
        action_text = "围绕你最拿不准的一点补充事实，再决定是否形成行动。"
        summary = "已完成规则检查；通用问题需要结合你的目标和材料继续判断。"
    elif role_pass and len(warnings) <= 2:
        priority, direction, next_action = "A", "核心优先", "主动联系"
        action_text = "先发出一条针对岗位的沟通，再补充调研关键风险。"
        summary = "岗位方向与画像较匹配，且暂未发现硬性冲突，值得优先推进。"
    elif role_pass:
        priority, direction, next_action = "B", "邻近可接受", "继续沟通"
        action_text = "先确认一项关键条件，满足后再继续投入。"
        summary = "岗位方向有匹配，但仍有条件需要确认，建议低成本推进。"
    else:
        priority, direction, next_action = "C", "谨慎试探", "继续沟通"
        action_text = "只做一次低成本确认，不能证明与主线相关就停止投入。"
        summary = "暂未看到足够的主线匹配证据，适合谨慎试探而非重点投入。"

    if not reasons:
        reasons.append("判断基于已确认材料与应用内画像，缺失信息已单列。")

    reply_draft = "你好，我想先确认这个岗位的核心职责、薪资结构和办公地点，方便发一份完整 JD 吗？"
    if failed:
        reply_draft = "感谢沟通，目前岗位的关键条件与我的求职目标不太一致，这次先不继续了，祝招聘顺利。"

    return {
        "summary": summary,
        "confirmed_facts": confirmed_facts,
        "uncertainties": list(dict.fromkeys(uncertainties)),
        "direction": direction,
        "priority": priority,
        "reasons": reasons,
        "risks": list(dict.fromkeys(risks)),
        "hard_conditions": list(dict.fromkeys(hard_conditions))[:3],
        "next_action": next_action,
        "action_text": action_text,
        "reply_draft": reply_draft[:100],
        "pipeline_recommendation": {
            "should_add": bool(job is not None and priority in {"A", "B"}),
            "reason": "只读阶段仅给建议，不会自动写入或修改岗位看板。",
        },
        "rule_checks": checks,
    }


def merge_model_analysis(rule_analysis: dict[str, Any], model_analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Merge model wording without allowing it to erase deterministic evidence."""

    if not model_analysis:
        return rule_analysis
    merged = dict(rule_analysis)
    for key in _STRING_FIELDS:
        value = model_analysis.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    for key in _LIST_FIELDS:
        values = _string_list(model_analysis.get(key))
        if values:
            merged[key] = values
    if merged.get("priority") not in _PRIORITIES:
        merged["priority"] = rule_analysis["priority"]
    if merged.get("direction") not in _DIRECTIONS:
        merged["direction"] = rule_analysis["direction"]
    pipeline = model_analysis.get("pipeline_recommendation")
    if isinstance(pipeline, dict) and isinstance(pipeline.get("should_add"), bool):
        merged["pipeline_recommendation"] = {
            "should_add": pipeline["should_add"],
            "reason": str(pipeline.get("reason") or rule_analysis["pipeline_recommendation"]["reason"]).strip(),
        }
    merged["confirmed_facts"] = rule_analysis["confirmed_facts"]
    merged["rule_checks"] = rule_analysis["rule_checks"]
    merged["hard_conditions"] = list(dict.fromkeys(rule_analysis["hard_conditions"] + _string_list(merged.get("hard_conditions"))))[:3]
    if rule_analysis["priority"] == "D":
        merged["priority"] = "D"
        merged["direction"] = "尽量避免"
        merged["next_action"] = "放弃"
    merged["reply_draft"] = str(merged.get("reply_draft") or "")[:100]
    return merged


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def assistant_content(analysis: dict[str, Any], *, ai_used: bool) -> str:
    suffix = "" if ai_used else "（规则模式：AI 未启用或本次调用不可用）"
    return f"{analysis.get('summary', '已完成分析')}\n\n下一步：{analysis.get('action_text', '补充关键信息。')}{suffix}"


def mark_image_processed(analysis: dict[str, Any]) -> None:
    for check in analysis.get("rule_checks", []):
        if check.get("code") == "image_evidence":
            check["status"] = "pass"
            check["detail"] = "已由本次配置的模型读取截图"
