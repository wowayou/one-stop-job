from pathlib import Path

from backend.app.services import bebee
from backend.app.services.collectors import BeBeeCollector

FIXTURE = Path(__file__).parent / "fixtures" / "bebee_listing.html"
CARD_FIXTURE = Path(__file__).parent / "fixtures" / "bebee_cards.html"
NEXT_FIXTURE = Path(__file__).parent / "fixtures" / "bebee_next_rsc.html"
BASE = "https://bebee.com/cn/jobs/role/seo"


def test_parse_jobs_jsonld_itemlist():
    jobs = bebee.extract_jobs(FIXTURE.read_text(encoding="utf-8"), base_url=BASE)
    titles = [j["title"] for j in jobs]
    assert "SEO专家" in titles
    assert "外贸运营专员" in titles
    assert len(jobs) == 2

    seo = next(j for j in jobs if j["title"] == "SEO专家")
    assert seo["company_name"] == "示例市某网络科技有限公司"
    assert seo["city"] == "示例市"
    assert "8000-12000" in (seo["salary_text"] or "")
    assert seo["url"] == "https://bebee.com/cn/job/abc123"
    assert "<b>" not in (seo["description"] or "")  # HTML 标签被剥离


def test_relative_url_resolved_against_base():
    jobs = bebee.extract_jobs(FIXTURE.read_text(encoding="utf-8"), base_url=BASE)
    other = next(j for j in jobs if j["title"] == "外贸运营专员")
    assert other["url"] == "https://bebee.com/cn/job/def456"


def test_extract_jobs_empty_without_structured_data():
    assert bebee.extract_jobs("<html><body>no structured data</body></html>", BASE) == []


def test_parse_jobs_next_rsc_payload():
    jobs = bebee.extract_jobs(NEXT_FIXTURE.read_text(encoding="utf-8"), base_url=BASE)

    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "SEO增长专家"
    assert job["company_name"] == "示例市飞轮科技有限公司"
    assert job["url"] == "https://bebee.com/cn/job/next123"
    assert job["city"] == "示例市 北区"
    assert job["published_at"] == "2026-06-10"
    assert job["skills"] == "SEO, 独立站, Google Analytics"
    assert "关键词研究" in (job["description"] or "")


def test_diagnose_next_jobs_parse_failure():
    html = '<script>self.__next_f.push([1, "{jobs:[{title:}]}"])</script>'

    assert "Next/RSC jobs 数据块" in bebee.diagnose_empty_html(html)
    assert "解析失败" in bebee.diagnose_empty_html(html)


def test_parse_jobs_visible_cards_without_structured_data():
    jobs = bebee.extract_jobs(CARD_FIXTURE.read_text(encoding="utf-8"), base_url=BASE)

    assert len(jobs) == 2
    seo = jobs[0]
    assert seo["title"] == "SEO专家"
    assert seo["company_name"] == "示例市卡片科技有限公司"
    assert seo["url"] == "https://bebee.com/cn/job/card123"
    assert seo["city"] == "示例市 北区"
    assert seo["salary_text"] == "8-12K"
    assert seo["published_at"] == "2026-06-09"
    assert "关键词研究" in (seo["description"] or "")


def test_collector_normalizes_and_dedups(monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(bebee, "fetch_listing", lambda url, cfg=None: html)
    collector = BeBeeCollector(urls=[BASE], cfg={})
    records = collector.collect()

    assert len(records) == 2
    assert all(r["source"] == "beBee" for r in records)
    assert len({r["external_id"] for r in records}) == 2  # 每岗位独立详情 url → external_id 唯一
    assert collector.report["urls_ok"] == 1
    assert collector.report["jobs"] == 2


def test_collector_records_skip_when_no_jobs(monkeypatch):
    monkeypatch.setattr(bebee, "fetch_listing", lambda url, cfg=None: "<html></html>")
    collector = BeBeeCollector(urls=[BASE], cfg={})
    records = collector.collect()
    assert records == []
    assert collector.report["skipped"] and "页面源码" in collector.report["skipped"][0]["reason"]
