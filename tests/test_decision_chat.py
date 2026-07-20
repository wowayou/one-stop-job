from backend.app.models import Job, UserProfile
from backend.app.services.decision_chat import build_rule_analysis, merge_model_analysis


POLICY = """
## Current Job Target

- 目标薪资：税前 8K-12K。
- 地点：示例市优先，可考虑远程。
- 目标方向：SEO、英文网站增长、B2B 出海增长。

优先推进：

- B2B 外贸独立站 SEO。
- 英文网站运营和海外推广。

谨慎试探：

- 独立站 + 广告投放或社媒协作。

尽量避免：

- 纯广告投放。
- 纯社媒账号运营。
- 纯平台店铺运营和销售型岗位。

## Offer Floor

- 低于 7K 的全职岗位不建议接。
"""


def _job(**overrides):
    values = {
        "external_id": "fixture-job",
        "title": "海外社媒运营",
        "company_name": "示例公司",
        "salary_text": "5-6K",
        "salary_min_k": 5,
        "salary_max_k": 6,
        "description": "负责 Facebook 和 LinkedIn 社媒账号运营、涨粉互动。",
    }
    values.update(overrides)
    return Job(**values)


def test_external_policy_drives_rules_when_public_profile_is_blank():
    analysis = build_rule_analysis(
        message="这份 JD 值得聊吗？",
        profile=UserProfile(),
        job=_job(),
        thread_kind="job",
        context_available=True,
        policy_context=POLICY,
    )

    assert analysis["priority"] == "D"
    assert analysis["next_action"] == "放弃"
    failures = [item for item in analysis["rule_checks"] if item["status"] == "fail"]
    assert {item["code"] for item in failures} == {"salary_floor", "role_direction"}


def test_mixed_role_is_warned_instead_of_fast_skipped():
    analysis = build_rule_analysis(
        message="请判断工作重心。",
        profile=UserProfile(),
        job=_job(
            title="独立站 SEO 与广告协作",
            salary_text="9-12K",
            salary_min_k=9,
            salary_max_k=12,
            description="SEO 是主责，同时配合 Google Ads，能看 GSC、询盘和页面转化。",
        ),
        thread_kind="job",
        context_available=True,
        policy_context=POLICY,
    )

    direction_check = next(item for item in analysis["rule_checks"] if item["code"] == "role_direction")
    assert direction_check["status"] == "warn"
    assert analysis["priority"] != "D"


def test_model_output_is_sanitized_and_cannot_override_hard_failure():
    rule_analysis = build_rule_analysis(
        message="判断岗位",
        profile=UserProfile(salary_min_k=8),
        job=_job(),
        thread_kind="job",
        context_available=False,
    )
    merged = merge_model_analysis(
        rule_analysis,
        {
            "summary": ["invalid"],
            "priority": "A",
            "direction": "核心优先",
            "next_action": 123,
            "reply_draft": "可以继续" * 40,
        },
    )

    assert merged["priority"] == "D"
    assert merged["direction"] == "尽量避免"
    assert merged["next_action"] == "放弃"
    assert isinstance(merged["summary"], str)
    assert len(merged["reply_draft"]) <= 100
