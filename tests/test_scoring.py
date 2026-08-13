from backend.app.models import Company, Job, ResearchItem, UserProfile
from backend.app.services.queries import validate_weights
from backend.app.services.scoring import DEFAULT_WEIGHTS, score_job

# config.yaml / config.example.yaml 里随本次「评分区分度」调整落地的那份权重：
# 权重集中到 role_match / salary_city（完全由岗位自带字段决定），压缩 stability / reputation
# （无公司调研证据时只能取常量，高权重等于给所有岗位加同一个数，只会压缩分数区间）。
SHIPPED_WEIGHTS = {
    "role_match": 40,
    "salary_city": 18,
    "growth": 12,
    "stability": 8,
    "reputation": 4,
    "commute_rest": 10,
    "interview_roi": 8,
}


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


def _same_title_job(external_id: str, company: str, skills: str) -> Job:
    """标题/薪资/城市/经验全部相同，只有技能标签不同——用来隔离方向证据带来的差异。"""
    return Job(
        source="fixture",
        external_id=external_id,
        title="独立站运营",
        company_name=company,
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
        experience="1-3年",
        skills=skills,
    )


def test_role_match_gradient_separates_jobs_sharing_one_title():
    """同标题岗位不再同分：role_match 内部按方向证据分梯度。

    这是「评分区分度」的核心回归点——改动前同一批「独立站运营」的 role_match 完全相同，
    头部排序没有信息量。B2B/SEO/数据/英文证据要把岗位抬上去，平台店铺标签要把岗位压下去。
    """
    profile = _synthetic_profile()
    on_direction = _same_title_job("g1", "示例市工业出海", "搜索引擎优化,数据分析,英语,机械设备,询盘")
    platform_shop = _same_title_job("g2", "示例市店铺代运营", "亚马逊,Listing优化,店铺站内推广,爆款运营,选品")

    on_result = score_job(on_direction, Company(name="示例市工业出海"), [], profile)
    shop_result = score_job(platform_shop, Company(name="示例市店铺代运营"), [], profile)

    # 不再同分，且方向证据充分的那条明显更高（不是靠小数点抖动区分）
    assert _role_match_score(on_result) > _role_match_score(shop_result)
    assert _role_match_score(on_result) - _role_match_score(shop_result) >= 6
    assert on_result.total > shop_result.total
    # 软降权不是硬阻断：店铺标签岗位仍然正常计分，只是排到后面
    assert shop_result.hard_blocked is False
    assert on_result.hard_blocked is False


def test_direction_noise_is_soft_penalty_not_hard_block():
    """方向微调必须走软降权，不能借 dealbreakers 做——后者是总分 ×0.55 的硬阻断。

    「外贸 + 独立站 + 社媒」复合岗在方向上（标题里有独立站和外贸），只该被轻扣；
    纯社媒岗才该被压到尾部。若用 dealbreakers 压社媒，复合岗会一起被硬扣。
    """
    profile = _synthetic_profile()
    composite = Job(
        source="fixture",
        external_id="n1",
        title="外贸独立站社媒运营",
        company_name="示例市跨境品牌",
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
        skills="独立站,社媒运营经验,英语",
    )
    pure_social = Job(
        source="fixture",
        external_id="n2",
        title="海外社媒运营",
        company_name="示例市社媒代运营",
        salary_text="8-12K",
        salary_min_k=8,
        salary_max_k=12,
        salary_avg_k=10,
        city="示例市",
        skills="Facebook,Instagram,短视频,达人合作",
    )

    composite_result = score_job(composite, Company(name="示例市跨境品牌"), [], profile)
    social_result = score_job(pure_social, Company(name="示例市社媒代运营"), [], profile)

    assert composite_result.hard_blocked is False
    assert social_result.hard_blocked is False
    assert composite_result.details["hard_reasons"] == []
    assert _role_match_score(composite_result) > _role_match_score(social_result)
    assert composite_result.total > social_result.total


def test_shipped_weights_pass_validation_and_reach_score_dimensions():
    """权重生效逻辑：config.yaml 落地的那份权重必须通过 validate_weights，
    并且真的改变各维度的可得分上限——把分数从"常量维度"挪到"有真实数据的维度"。
    """
    validate_weights(SHIPPED_WEIGHTS)  # 未知维度 / 负数 / 合计 >100 都会抛 HTTPException
    assert sum(SHIPPED_WEIGHTS.values()) <= 100
    assert set(SHIPPED_WEIGHTS) == set(DEFAULT_WEIGHTS)

    job = Job(
        source="fixture",
        external_id="w1",
        title="外贸独立站SEO运营",
        company_name="示例市出海制造",
        salary_text="9-14K",
        salary_min_k=9,
        salary_max_k=14,
        salary_avg_k=11.5,
        city="示例市",
        experience="1-3年",
        skills="搜索引擎优化,数据分析,英语,机械设备",
    )
    company = Company(name="示例市出海制造")

    default_result = score_job(job, company, [], _synthetic_profile())
    shipped_profile = _synthetic_profile()
    shipped_profile.weights = SHIPPED_WEIGHTS
    shipped_result = score_job(job, company, [], shipped_profile)

    default_dims = default_result.details["dimensions"]
    shipped_dims = shipped_result.details["dimensions"]
    # 权重原样透出，供前端解释分数
    assert default_dims["role_match"]["weight"] == DEFAULT_WEIGHTS["role_match"]
    assert shipped_dims["role_match"]["weight"] == 40
    # 有真实数据的维度可得分变大；缺公司调研时取常量的维度被压缩
    assert shipped_dims["role_match"]["score"] > default_dims["role_match"]["score"]
    constant_dims = ("stability", "reputation")
    assert sum(shipped_dims[key]["score"] for key in constant_dims) < sum(
        default_dims[key]["score"] for key in constant_dims
    )
