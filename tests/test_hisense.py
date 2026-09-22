"""海信招聘来源:解析器纯函数 + 采集器（CLAUDE.md §2/§4）。

用真实抓取的 `tests/fixtures/hisense_joblist.json`(截前 5 条)校对解析,不联网:
抓取函数 `hisense.fetch_job_list` 在采集器用例里 monkeypatch 掉。
覆盖:字段抽取、职责+要求拼描述、省·市地点、薪资恒空、external_id 唯一性、
缺字段跳过、分页 + 去重 + report、抓取失败 skip、非 200 响应返回空。
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.app.services.hisense as hisense
from backend.app.services.collectors import HisenseCollector

FIXTURE = Path(__file__).parent / "fixtures" / "hisense_joblist.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ==================== 解析器纯函数 ====================


def test_parse_jobs_extracts_core_fields_from_real_fixture():
    jobs = hisense.parse_jobs(_payload(), {})
    assert len(jobs) == 5
    first = jobs[0]
    assert first["title"] == "视觉算法工程师(J43082)"
    assert first["company_name"] == "海信集团"
    assert first["city"] == "山东省·青岛市"
    assert first["skills"] == "空气事业部"  # ClassificationTwo = 事业部
    # 详情页 url 用 GUID（Id），不是整数 JobAdId。
    assert first["url"] == "https://jobs.hisense.com/social/detail?jobAdId=4dad2174-f35e-4b14-a727-59a930971969"
    assert hisense.total_count(_payload()) == 655


def test_description_combines_duty_and_require():
    desc = hisense.parse_jobs(_payload(), {})[0]["description"]
    assert "岗位职责" in desc and "任职要求" in desc


def test_salary_is_empty_because_hisense_does_not_publish_it():
    # 海信列表不公布薪资（Salary 恒 null）——照实留空，不硬造。
    assert all(j["salary_text"] is None for j in hisense.parse_jobs(_payload(), {}))


def test_parse_jobs_skips_records_missing_guid_or_title():
    payload = {
        "Code": 200,
        "Count": 3,
        "Data": [
            {"Id": "guid-1", "JobAdName": "有效岗位"},
            {"Id": "", "JobAdName": "缺 GUID"},
            {"Id": "guid-2", "JobAdName": ""},
            {"JobAdName": "没有 Id 键"},
        ],
    }
    jobs = hisense.parse_jobs(payload, {})
    assert [j["title"] for j in jobs] == ["有效岗位"]


def test_parse_jobs_returns_empty_on_non_200_or_malformed():
    assert hisense.parse_jobs({"Code": 500, "Data": []}, {}) == []
    assert hisense.parse_jobs({"Code": 200, "Data": "not-a-list"}, {}) == []
    assert hisense.parse_jobs({}, {}) == []


# ==================== 采集器（monkeypatch 抓取，不联网） ====================


def test_collector_paginates_dedupes_and_reports(monkeypatch):
    payload = _payload()
    calls: list[int] = []

    def fake_fetch(url, page, page_size, cfg=None):
        calls.append(page)
        if page in (0, 1):  # 前两页给同一批（验证跨页去重）
            return payload
        return {"Code": 200, "Count": 655, "Data": []}  # 空页触发提前停

    monkeypatch.setattr(hisense, "fetch_job_list", fake_fetch)
    collector = HisenseCollector(cfg={"max_pages": 3, "rate_limit_seconds": 0}, source="海信招聘")
    records = collector.collect()

    assert len(records) == 5  # 两页相同 → 去重后 5 条
    assert all(r["source"] == "海信招聘" for r in records)
    assert len({r["external_id"] for r in records}) == 5
    assert collector.report["jobs"] == 5
    assert calls[:3] == [0, 1, 2]  # PageIndex 从 0 开始，空页在第 3 次触发提前停


def test_collector_records_fetch_failure_as_skip(monkeypatch):
    def boom(url, page, page_size, cfg=None):
        raise RuntimeError("网络超时")

    monkeypatch.setattr(hisense, "fetch_job_list", boom)
    collector = HisenseCollector(cfg={"max_pages": 1, "rate_limit_seconds": 0}, source="海信招聘")
    records = collector.collect()

    assert records == []
    assert len(collector.report["skipped"]) == 1
    assert "网络超时" in collector.report["skipped"][0]["reason"]
