"""求职相邻度策略：岗位族、搜索计划与可解释分类。

这是采集与评分共用的唯一职业逻辑入口。个人仓库可以在本机 ``config.yaml`` 的
``reach.policy`` 覆盖默认岗位族；公开模板只包含通用关键词，不包含个人经历或证据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


LEVELS = ("core", "adjacent", "exploratory")
LEVEL_LABELS = {"core": "核心", "adjacent": "相邻", "exploratory": "探索"}
LEVEL_QUOTAS = {
    "core": {"core": 1.0, "adjacent": 0.0, "exploratory": 0.0},
    "adjacent": {"core": 0.7, "adjacent": 0.3, "exploratory": 0.0},
    "exploratory": {"core": 0.5, "adjacent": 0.3, "exploratory": 0.2},
}


DEFAULT_POLICY: tuple[dict[str, Any], ...] = (
    {
        "key": "seo_site_growth",
        "label": "SEO/独立站增长",
        "level": "core",
        "search_terms": ["SEO运营", "独立站运营", "英文网站运营", "B2B网站推广"],
        "keywords": ["seo", "搜索优化", "独立站", "网站运营", "外贸网站", "b2b网站", "谷歌优化"],
        "transferable": ["关键词与页面优化", "内容与流量增长", "数据复盘", "英文市场协作"],
        "required": ["SEO/网站/增长至少一项主职责"],
        "exclude": ["纯销售", "纯投流", "纯社媒", "平台店铺为主"],
    },
    {
        "key": "content_cms_b2b",
        "label": "英文内容/CMS/B2B数字营销",
        "level": "adjacent",
        "search_terms": ["英文内容运营", "CMS运营", "WordPress运营", "B2B数字营销", "技术内容运营"],
        "keywords": ["英文内容", "内容运营", "cms", "wordpress", "技术内容", "b2b数字营销", "页面转化"],
        "transferable": ["内容结构与页面质量", "CMS交付", "SEO基础", "转化路径梳理"],
        "required": ["内容/CMS/页面运营至少一项"],
        "exclude": ["纯销售", "纯投流", "纯社媒"],
    },
    {
        "key": "delivery_support",
        "label": "SaaS实施/应用支持/客户成功",
        "level": "exploratory",
        "search_terms": ["SaaS实施", "应用支持", "客户成功", "网站项目交付"],
        "keywords": ["saas实施", "实施顾问", "应用支持", "客户成功", "项目交付", "系统上线"],
        "transferable": ["需求拆解", "跨团队协作", "交付跟进", "问题定位与文档"],
        "required": ["实施/支持/交付/客户成功主职责"],
        "exclude": ["纯销售", "陌拜", "电话销售"],
    },
    {
        "key": "operations_editor_data",
        "label": "项目/产品内容/数据运营",
        "level": "exploratory",
        "search_terms": ["项目运营", "产品运营", "内容运营", "数据运营", "技术编辑"],
        "keywords": ["项目运营", "产品运营", "数据运营", "技术编辑", "内容质量", "项目管理"],
        "transferable": ["流程推进", "内容质量", "数据分析", "项目协作"],
        "required": ["运营/编辑/项目/数据主职责"],
        "exclude": ["纯销售", "纯投流", "纯社媒"],
    },
)


@dataclass(frozen=True)
class RoleClassification:
    family_key: str | None
    family_label: str
    level: str
    overlap: tuple[str, ...]
    missing_hard: tuple[str, ...]
    short_term_gaps: tuple[str, ...]
    recommendation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_key": self.family_key,
            "family_label": self.family_label,
            "level": self.level,
            "level_label": LEVEL_LABELS.get(self.level, self.level),
            "overlap": list(self.overlap),
            "missing_hard": list(self.missing_hard),
            "short_term_gaps": list(self.short_term_gaps),
            "recommendation": self.recommendation,
        }


def normalize_level(value: Any) -> str:
    normalized = str(value or "core").strip().lower()
    aliases = {"核心": "core", "相邻": "adjacent", "探索": "exploratory", "explore": "exploratory"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in LEVELS else "core"


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def policy_families(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw = (cfg or {}).get("policy") if isinstance(cfg, dict) else None
    if not isinstance(raw, list) or not raw:
        return [dict(item) for item in DEFAULT_POLICY]
    families: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        family = dict(item)
        family.setdefault("key", f"custom_{index + 1}")
        family.setdefault("label", family["key"])
        family["level"] = normalize_level(family.get("level"))
        family["search_terms"] = _as_list(family.get("search_terms"))
        family["keywords"] = _as_list(family.get("keywords"))
        family["transferable"] = _as_list(family.get("transferable"))
        family["required"] = _as_list(family.get("required"))
        family["exclude"] = _as_list(family.get("exclude"))
        if family["keywords"] or family["search_terms"]:
            families.append(family)
    return families or [dict(item) for item in DEFAULT_POLICY]


def _text(title: str | None, context: str | None = None) -> str:
    return f"{title or ''} {context or ''}".strip().lower()


def classify_role(title: str | None, context: str | None = None, cfg: dict[str, Any] | None = None) -> RoleClassification:
    text = _text(title, context)
    families = policy_families(cfg)
    ranked: list[tuple[int, dict[str, Any], list[str]]] = []
    for family in families:
        hits = [keyword for keyword in family.get("keywords", []) if keyword.lower() in text]
        ranked.append((len(set(hits)), family, hits))
    ranked.sort(key=lambda item: item[0], reverse=True)
    hits_count, family, hits = ranked[0] if ranked and ranked[0][0] else (0, None, [])
    if family is None:
        return RoleClassification(None, "未识别岗位族", "exploratory", (), ("岗位族主职责未识别",), ("补充 JD 主职责与工具",), "人工判断")

    required = tuple(family.get("required", []))
    overlap = tuple(family.get("transferable", [])[: max(1, min(len(family.get("transferable", [])), hits_count + 1))])
    missing = () if hits_count >= 2 else required
    gaps = tuple(family.get("transferable", [])[len(overlap) : len(overlap) + 2])
    recommendation = "推荐投递" if hits_count >= 2 else "人工判断"
    if any(ex.lower() in text for ex in family.get("exclude", [])):
        recommendation = "排除"
    return RoleClassification(family.get("key"), str(family.get("label")), normalize_level(family.get("level")), overlap, missing, gaps, recommendation)


def family_level_bonus(classification: RoleClassification, selected_level: str) -> float:
    """返回 role_match 的相邻度调整；硬条件仍由 scoring.score_job 负责。"""
    selected = normalize_level(selected_level)
    order = {"core": 0, "adjacent": 1, "exploratory": 2}
    distance = order.get(classification.level, 2) - order.get(selected, 0)
    if distance < 0:
        return 0.04
    if distance == 0:
        return 0.0
    return -0.06 if selected == "exploratory" else -0.12


def build_search_plan(cfg: dict[str, Any] | None = None, *, daily_limit: int = 30, explicit_terms: Iterable[str] = ()) -> dict[str, Any]:
    reach_cfg = cfg or {}
    level = normalize_level(reach_cfg.get("level"))
    families = policy_families(reach_cfg)
    quotas = LEVEL_QUOTAS[level]
    terms: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 本机已有关键词是用户明确选择的核心方向，必须优先占用核心配额；否则相邻/探索
    # 档位下默认词可能先占满查询槽，造成升级后静默丢失原有搜索词。
    for term in explicit_terms:
        value = str(term).strip()
        if value and value not in seen:
            seen.add(value)
            terms.append({"query": value, "family_level": "core", "quota": quotas["core"]})
    for family_level in LEVELS:
        quota = quotas[family_level]
        if quota <= 0:
            continue
        families_at_level = [family for family in families if normalize_level(family.get("level")) == family_level]
        family_terms = [term for family in families_at_level for term in family.get("search_terms", [])]
        for term in family_terms:
            if term in seen:
                continue
            seen.add(term)
            terms.append({"query": term, "family_level": family_level, "quota": quota})
    max_queries = max(1, int(reach_cfg.get("max_queries_per_scan", 9) or 9))
    selected: list[dict[str, Any]] = []
    for family_level in LEVELS:
        band_terms = [item for item in terms if item["family_level"] == family_level]
        if not band_terms or quotas[family_level] <= 0:
            continue
        band_slots = max(1, round(max_queries * quotas[family_level]))
        selected.extend(band_terms[:band_slots])
    selected = selected[:max_queries]
    for family_level in LEVELS:
        band_items = [item for item in selected if item["family_level"] == family_level]
        if not band_items:
            continue
        band_budget = max(1, round(daily_limit * quotas[family_level]))
        base, remainder = divmod(band_budget, len(band_items))
        for index, item in enumerate(band_items):
            item["budget"] = max(1, base + (1 if index < remainder else 0))
    return {"level": level, "level_label": LEVEL_LABELS[level], "quotas": quotas, "terms": selected}
