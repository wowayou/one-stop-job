"""海尔招聘来源:解析器纯函数 + 采集器（CLAUDE.md §2/§4）。

用真实抓取的 `tests/fixtures/haier_searchdata.json`(截前 5 条)校对解析,不联网:
抓取函数 `haier.fetch_job_list` 在采集器用例里 monkeypatch 掉。
覆盖:字段抽取、年薪(万)→月薪(K)换算、脏数据回退、external_id 唯一性、
缺字段跳过、分页 + 去重 + report、抓取失败 skip。
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.app.services.haier as haier
from backend.app.services.collectors import HaierCollector

FIXTURE = Path(__file__).parent / "fixtures" / "haier_searchdata.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ==================== 解析器纯函数 ====================


def test_parse_jobs_extracts_core_fields_from_real_fixture():
    jobs = haier.parse_jobs(_payload(), {})
    assert len(jobs) == 5
    first = jobs[0]
    assert first["title"] == "生产技术工程师"
    assert first["company_name"] == "海尔集团"
    assert first["city"] == "上海市"
    assert first["experience"] == "3年以上"
    assert first["degree"] == "本科及以上"
    # 详情页 url 用 id 拼出,天然唯一。
    assert first["url"] == "https://maker.haier.net/client/job/detail/id/10231413/recommend_record/1"
    assert haier.total_count(_payload()) == 600


def test_salary_converts_annual_wan_to_monthly_k():
    # 18~22.8 万/年 → 15~19K/月（×10÷12，四舍五入）。normalizer.parse_salary 把「万」
    # 当月薪,直接塞「万/年」会放大 10 倍,所以这里必须先换算成 K/月。
    assert haier.salary_text({"min_yearly_salary": "18", "yearly_salary": "22.8"}) == "15-19K"
    # 24~36 万/年 → 20~30K/月。
    assert haier.salary_text({"min_yearly_salary": "24", "yearly_salary": "36"}) == "20-30K"
    # 上下界相等 → 单值 K（12 万/年 → 10K/月）。
    assert haier.salary_text({"min_yearly_salary": "12", "yearly_salary": "12"}) == "10K"


def test_salary_falls_back_to_label_on_dirty_or_missing_numbers():
    # 实测脏数据:城市名被塞进薪资字段 → 回退站点自己的 salary_label。
    assert haier.salary_text({"min_yearly_salary": "青岛市", "yearly_salary": "30", "salary_label": "薪资面议"}) == "薪资面议"
    assert haier.salary_text({"salary_label": "薪资面议"}) == "薪资面议"
    assert haier.salary_text({}) is None


def test_parse_jobs_skips_records_missing_id_or_title():
    payload = {
        "status": 1,
        "data": {
            "count": 3,
            "list": [
                {"id": "1", "job_name": "有效岗位"},
                {"id": "", "job_name": "缺 id"},
                {"id": "2", "job_name": ""},
                {"job_name": "没有 id 键"},
            ],
        },
    }
    jobs = haier.parse_jobs(payload, {})
    assert [j["title"] for j in jobs] == ["有效岗位"]


def test_parse_jobs_returns_empty_on_malformed_payload():
    assert haier.parse_jobs({}, {}) == []
    assert haier.parse_jobs({"data": {"list": "not-a-list"}}, {}) == []
    assert haier.parse_jobs({"status": 0}, {}) == []


# ==================== 采集器（monkeypatch 抓取，不联网） ====================


def test_collector_paginates_dedupes_and_reports(monkeypatch):
    payload = _payload()
    calls: list[int] = []

    def fake_fetch(url, page, cfg=None):
        calls.append(page)
        # 第 1 页给真实 fixture,第 2 页重复同一批(验证跨页去重),第 3 页空(触发提前停)。
        if page == 1:
            return payload
        if page == 2:
            return payload
        return {"status": 1, "data": {"count": 600, "list": []}}

    monkeypatch.setattr(haier, "fetch_job_list", fake_fetch)

    collector = HaierCollector(cfg={"max_pages": 3, "rate_limit_seconds": 0}, source="海尔招聘")
    records = collector.collect()

    # 两页相同 → external_id 去重后仍是 5 条。
    assert len(records) == 5
    assert all(r["source"] == "海尔招聘" for r in records)
    assert len({r["external_id"] for r in records}) == 5  # 每条 url 不同 → external_id 唯一
    assert collector.report["jobs"] == 5
    assert collector.report["pages_ok"] >= 2
    assert calls[:3] == [1, 2, 3]  # 空页在第 3 页触发提前停


def test_collector_records_fetch_failure_as_skip(monkeypatch):
    def boom(url, page, cfg=None):
        raise RuntimeError("网络超时")

    monkeypatch.setattr(haier, "fetch_job_list", boom)
    collector = HaierCollector(cfg={"max_pages": 1, "rate_limit_seconds": 0}, source="海尔招聘")
    records = collector.collect()

    assert records == []
    assert len(collector.report["skipped"]) == 1
    assert "网络超时" in collector.report["skipped"][0]["reason"]
