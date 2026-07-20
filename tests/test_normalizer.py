from backend.app.services.normalizer import (
    dataframe_from_csv_text,
    normalize_dataframe,
    normalize_record,
    parse_published_at,
    parse_recruitment_status,
    parse_salary,
)


def test_parse_salary_range_and_months():
    parsed = parse_salary("8-12K·13薪")
    assert parsed["salary_min_k"] == 8
    assert parsed["salary_max_k"] == 12
    assert parsed["salary_avg_k"] == 10
    assert parsed["annual_salary_w"] == 13


def test_clean_opencli_noise_and_normalize():
    raw = "OpenCLI log line\nname,company,salary,area,url\nSEO运营,示例市增长科技,8-12K,示例市·示例区,https://example.com/a"
    df = dataframe_from_csv_text(raw)
    records = normalize_dataframe(df, source="BOSS直聘")
    assert len(records) == 1
    assert records[0]["title"] == "SEO运营"
    assert records[0]["company_name"] == "示例市增长科技"
    assert records[0]["city"] == "示例市"
    assert records[0]["area"] == "示例区"


def test_normalize_record_uses_explicit_city_and_area_together():
    record = normalize_record({"title": "SEO运营", "company": "示例市增长科技", "city": "示例市", "area": "示例区"}, source="manual")
    assert record["city"] == "示例市"
    assert record["area"] == "示例区"


def test_parse_published_at_ignores_invalid_dates():
    assert parse_published_at("2026-13-99") is None


def test_parse_recruitment_status_closed_synonyms():
    assert parse_recruitment_status("已招满") == "closed"
    assert parse_recruitment_status("岗位在招") == "active"


def test_normalize_record_skips_canonical_key_for_unknown_identity():
    record = normalize_record({"title": "未命名岗位", "company": "未知公司"}, source="导入文件")
    assert record["canonical_key"] is None
