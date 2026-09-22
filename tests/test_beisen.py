"""北森(Beisen)门户通用来源:解析器纯函数 + 采集器（CLAUDE.md §2/§4）。

用真实抓取的 `tests/fixtures/beisen_joblist.json`(海信门户,截前 5 条)校对解析,不联网:
抓取函数 `beisen.fetch_job_list` 在采集器用例里 monkeypatch 掉。
覆盖:字段抽取、职责+要求拼描述、省·市地点、薪资恒空、per-门户 company/source、
external_id 唯一性、缺字段跳过、非 200 返回空、多门户遍历 + 去重 + report、
缺 list_url 跳过、抓取失败 skip。
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.app.services.beisen as beisen
from backend.app.services.collectors import BeisenPortalCollector

FIXTURE = Path(__file__).parent / "fixtures" / "beisen_joblist.json"

# 海信门户的单门户配置（parse_jobs/collector 都靠它拿 company_name + detail_url_template）。
HISENSE = {
    "label": "海信招聘",
    "company_name": "海信集团",
    "list_url": "https://jobs.hisense.com/api/Jobad/GetJobAdPageList",
    "detail_url_template": "https://jobs.hisense.com/social/detail?jobAdId={id}",
    "category": "1",
}


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ==================== 解析器纯函数 ====================


def test_parse_jobs_extracts_core_fields_from_real_fixture():
    jobs = beisen.parse_jobs(_payload(), HISENSE)
    assert len(jobs) == 5
    first = jobs[0]
    assert first["title"] == "视觉算法工程师(J43082)"
    assert first["company_name"] == "海信集团"
    assert first["city"] == "山东省·青岛市"
    assert first["skills"] == "空气事业部"  # ClassificationTwo = 事业部
    # 详情页 url 用 GUID（Id），不是整数 JobAdId。
    assert first["url"] == "https://jobs.hisense.com/social/detail?jobAdId=4dad2174-f35e-4b14-a727-59a930971969"
    assert beisen.total_count(_payload()) == 655


def test_description_combines_duty_and_require():
    desc = beisen.parse_jobs(_payload(), HISENSE)[0]["description"]
    assert "岗位职责" in desc and "任职要求" in desc


def test_salary_is_empty_because_portal_does_not_publish_it():
    # 北森列表通常不公布薪资（Salary null）——照实留空，不硬造。
    assert all(j["salary_text"] is None for j in beisen.parse_jobs(_payload(), HISENSE))


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
    jobs = beisen.parse_jobs(payload, HISENSE)
    assert [j["title"] for j in jobs] == ["有效岗位"]


def test_parse_jobs_returns_empty_on_non_200_or_malformed():
    assert beisen.parse_jobs({"Code": 500, "Data": []}, HISENSE) == []
    assert beisen.parse_jobs({"Code": 200, "Data": "not-a-list"}, HISENSE) == []
    assert beisen.parse_jobs({}, HISENSE) == []


# ==================== 采集器（monkeypatch 抓取，不联网） ====================


def test_collector_iterates_portals_tags_source_and_dedupes(monkeypatch):
    payload = _payload()

    def fake_fetch(url, page, page_size, cfg=None):
        # 每个门户第 0 页给真实数据，其余页空（触发提前停）。
        return payload if page == 0 else {"Code": 200, "Count": 655, "Data": []}

    monkeypatch.setattr(beisen, "fetch_job_list", fake_fetch)

    # 两个门户共用同一批数据（url 相同 → 跨门户去重后仍是 5 条），验证 per-门户 source + 去重。
    portal_b = {**HISENSE, "label": "示例北森公司", "company_name": "示例集团"}
    collector = BeisenPortalCollector(
        portals=[HISENSE, portal_b],
        cfg={"max_pages": 2, "rate_limit_seconds": 0},
        source="北森门户",
    )
    records = collector.collect()

    assert len(records) == 5  # 两门户相同 url → 去重后 5 条
    assert {r["source"] for r in records} == {"海信招聘"}  # 先到的门户占位，重复的被去掉
    assert len({r["external_id"] for r in records}) == 5
    assert collector.report["portals_total"] == 2
    assert collector.report["portals_ok"] == 2
    assert collector.report["jobs"] == 5


def test_collector_skips_portal_without_list_url(monkeypatch):
    monkeypatch.setattr(beisen, "fetch_job_list", lambda *a, **k: _payload())
    collector = BeisenPortalCollector(
        portals=[{"label": "缺配置", "company_name": "X"}],  # 没有 list_url
        cfg={"max_pages": 1, "rate_limit_seconds": 0},
    )
    records = collector.collect()
    assert records == []
    assert any("list_url" in s["reason"] for s in collector.report["skipped"])


def test_collector_records_fetch_failure_as_skip(monkeypatch):
    def boom(url, page, page_size, cfg=None):
        raise RuntimeError("网络超时")

    monkeypatch.setattr(beisen, "fetch_job_list", boom)
    collector = BeisenPortalCollector(portals=[HISENSE], cfg={"max_pages": 1, "rate_limit_seconds": 0})
    records = collector.collect()

    assert records == []
    assert any("网络超时" in s["reason"] for s in collector.report["skipped"])
