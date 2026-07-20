from backend.app.models import Company, Job, ResearchItem, UserProfile
from backend.app.services.scoring import score_job


def _synthetic_profile() -> UserProfile:
    """Explicit fixture profile; production defaults intentionally stay blank."""
    return UserProfile(
        target_titles="SEO运营,外贸独立站运营,独立站建设维护,外贸SEO",
        target_cities="示例市",
        salary_min_k=8,
        salary_max_k=20,
        skills="SEO,独立站,数据分析,外贸运营",
        strengths="搜索流量增长,内容优化,数据复盘",
        dealbreakers="单休,纯销售",
    )


def test_public_profile_defaults_do_not_encode_personal_preferences():
    profile = UserProfile()

    assert profile.target_titles == ""
    assert profile.target_cities == ""
    assert profile.salary_min_k == 0
    assert profile.salary_max_k == 0
    assert profile.skills == ""
    assert profile.strengths == ""
    assert profile.dealbreakers == ""
    assert profile.commute_preferences == ""


def test_score_job_with_positive_evidence():
    job = Job(
        source="fixture",
        external_id="1",
        title="SEO运营",
        company_name="示例市增长科技",
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
        area="示例区",
        skills="SEO,独立站,数据分析,双休",
    )
    company = Company(name="示例市增长科技", risk_level="low")
    profile = _synthetic_profile()
    research = [
        ResearchItem(company_id=1, source_type="manual_note", title="官网", summary="出海增长业务扩张", sentiment="positive", confidence=0.8)
    ]

    result = score_job(job, company, research, profile)
    assert result.total >= 70
    assert result.hard_blocked is False
    assert "role_match" in result.details["dimensions"]


def test_score_job_hard_blocks_dealbreaker():
    job = Job(
        source="fixture",
        external_id="2",
        title="SEO销售",
        company_name="测试公司",
        salary_text="4-5K",
        salary_max_k=5,
        city="北京",
        skills="纯销售,单休",
    )
    result = score_job(job, Company(name="测试公司"), [], _synthetic_profile())
    assert result.hard_blocked is True
    assert result.total < 60


def test_score_job_is_independent_from_ai_env(monkeypatch):
    job = Job(
        source="fixture",
        external_id="3",
        title="外贸SEO运营",
        company_name="示例市出海科技",
        salary_text="10-15K",
        salary_min_k=10,
        salary_max_k=15,
        salary_avg_k=12.5,
        city="示例市",
        area="北区",
        skills="SEO,独立站,数据分析,双休",
    )
    profile = _synthetic_profile()
    company = Company(name="示例市出海科技", risk_level="low")

    without_ai = score_job(job, company, [], profile)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    with_ai_env = score_job(job, company, [], profile)

    assert with_ai_env.total == without_ai.total
    assert with_ai_env.details == without_ai.details


def _role_match_score(result):
    return result.details["dimensions"]["role_match"]["score"]


def test_role_match_recognizes_trade_independent_site_operation():
    profile = _synthetic_profile()

    operation_job = Job(
        source="fixture",
        external_id="4",
        title="外贸独立站运营",
        company_name="示例市跨境科技",
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
    )
    seo_build_job = Job(
        source="fixture",
        external_id="5",
        title="外贸独立站建设维护与SEO推广",
        company_name="示例市品牌出海",
        salary_text="9-14K",
        salary_min_k=9,
        salary_max_k=14,
        salary_avg_k=11.5,
        city="示例市",
    )

    operation_result = score_job(operation_job, Company(name="示例市跨境科技"), [], profile)
    seo_build_result = score_job(seo_build_job, Company(name="示例市品牌出海"), [], profile)

    assert _role_match_score(operation_result) >= 20
    assert operation_result.total >= 70
    assert _role_match_score(seo_build_result) >= 22
    assert seo_build_result.total >= 70


def test_role_match_does_not_uplift_unrelated_or_sales_roles():
    profile = _synthetic_profile()
    info_flow_job = Job(
        source="fixture",
        external_id="6",
        title="信息流优化师",
        company_name="示例市广告科技",
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
        skills="巨量引擎,广告投放,素材测试",
    )
    sales_job = Job(
        source="fixture",
        external_id="7",
        title="外贸销售",
        company_name="示例市贸易公司",
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
        skills="客户开发,询盘转化,成交",
    )

    info_flow_result = score_job(info_flow_job, Company(name="示例市广告科技"), [], profile)
    sales_result = score_job(sales_job, Company(name="示例市贸易公司"), [], profile)

    assert _role_match_score(info_flow_result) <= 8
    assert info_flow_result.total < 65
    assert _role_match_score(sales_result) <= 8
    assert sales_result.total < 65


def test_low_salary_hard_block_still_applies_to_relevant_role():
    job = Job(
        source="fixture",
        external_id="8",
        title="外贸独立站运营",
        company_name="示例市低薪岗位",
        salary_text="4-5K",
        salary_min_k=4,
        salary_max_k=5,
        salary_avg_k=4.5,
        city="示例市",
    )

    result = score_job(job, Company(name="示例市低薪岗位"), [], _synthetic_profile())

    assert _role_match_score(result) >= 20
    assert result.hard_blocked is True
    assert "薪资上限低于最低期望" in result.details["hard_reasons"]
    assert result.total < 60
