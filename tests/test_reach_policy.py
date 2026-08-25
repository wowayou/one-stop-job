from backend.app.services.reach_policy import (
    LEVEL_QUOTAS,
    build_search_plan,
    classify_role,
    normalize_level,
)


def test_normalize_level_is_safe_and_supports_ui_labels():
    assert normalize_level("核心") == "core"
    assert normalize_level("adjacent") == "adjacent"
    assert normalize_level("garbage") == "core"


def test_core_plan_only_contains_core_terms():
    plan = build_search_plan({"level": "core"}, daily_limit=30)
    assert plan["quotas"] == LEVEL_QUOTAS["core"]
    assert plan["terms"]
    assert {item["family_level"] for item in plan["terms"]} == {"core"}
    assert sum(int(item["budget"]) for item in plan["terms"]) == 30


def test_adjacent_plan_uses_seventy_thirty_budget_split():
    plan = build_search_plan({"level": "adjacent", "max_queries_per_scan": 6}, daily_limit=30)
    budgets = {level: sum(item["budget"] for item in plan["terms"] if item["family_level"] == level) for level in ("core", "adjacent", "exploratory")}
    assert budgets == {"core": 21, "adjacent": 9, "exploratory": 0}


def test_explicit_core_terms_take_priority_over_default_terms():
    explicit = ["独立站运营", "海外推广运营", "谷歌优化", "外贸网站运营"]
    plan = build_search_plan(
        {"level": "adjacent", "max_queries_per_scan": 6},
        daily_limit=30,
        explicit_terms=explicit,
    )
    core_queries = [item["query"] for item in plan["terms"] if item["family_level"] == "core"]
    assert core_queries == explicit


def test_exploratory_role_keeps_transferable_evidence_without_fake_experience():
    cfg = {"level": "exploratory"}
    result = classify_role("SaaS实施顾问", "负责系统上线、客户培训和问题跟进", cfg)
    assert result.level == "exploratory"
    assert result.recommendation == "推荐投递"
    assert result.overlap
    assert "正式经验" not in "；".join(result.overlap)


def test_pure_sales_is_not_recommended_by_adjacent_policy():
    result = classify_role("海外销售", "电话销售、陌拜、客户开发", {"level": "exploratory"})
    assert result.recommendation in {"排除", "人工判断"}
