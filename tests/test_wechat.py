from pathlib import Path

from backend.app.services import wechat
from backend.app.services.collectors import WeChatPasteCollector

FIXTURE = Path(__file__).parent / "fixtures" / "wechat_article.html"


def test_extract_mp_links_from_yuanbao_answer():
    blob = (
        "根据公众号检索，为你找到以下招聘文章：\n"
        "1. 示例市SEO招聘 https://mp.weixin.qq.com/s/AbC123dEf456\n"
        "2. 外贸运营 https://mp.weixin.qq.com/s?__biz=MzA5&mid=2247&idx=1&sn=abcdef&chksm=xx&scene=27\n"
        "另外这个不是公众号：https://www.zhipin.com/job/123.html"
    )
    links = wechat.extract_mp_links(blob)
    assert "https://mp.weixin.qq.com/s/AbC123dEf456" in links
    assert "https://mp.weixin.qq.com/s?__biz=MzA5&mid=2247&idx=1&sn=abcdef" in links
    assert all("zhipin" not in link for link in links)
    assert len(links) == 2


def test_canonicalize_drops_noise_params():
    url = (
        "https://mp.weixin.qq.com/s?__biz=MzA5&mid=2247&idx=1&sn=abcdef"
        "&chksm=xxx&scene=27&key=zzz#wechat_redirect"
    )
    assert wechat.canonicalize_mp_url(url) == "https://mp.weixin.qq.com/s?__biz=MzA5&mid=2247&idx=1&sn=abcdef"
    # /s/<token> 永久链保持原样
    assert wechat.canonicalize_mp_url("https://mp.weixin.qq.com/s/AbC123?x=1") == "https://mp.weixin.qq.com/s/AbC123"


def test_parse_article_html_extracts_title_and_strips_noise():
    og_title, body = wechat.parse_article_html(FIXTURE.read_text(encoding="utf-8"))
    assert og_title and "招聘" in og_title
    assert "SEO运营专员" in body
    assert "点击上方" not in body  # 样板噪声被剥离
    assert "二维码" not in body


def test_extract_jobs_regex_multi_job():
    og_title, body = wechat.parse_article_html(FIXTURE.read_text(encoding="utf-8"))
    url = "https://mp.weixin.qq.com/s/AbC123dEf456"
    jobs = wechat.extract_jobs_regex(body, url, og_title)
    titles = [j["title"] for j in jobs]
    assert "SEO运营专员" in titles
    assert "外贸运营专员" in titles
    assert len(jobs) >= 2
    assert all(j["url"] == url for j in jobs)
    seo = next(j for j in jobs if j["title"] == "SEO运营专员")
    assert seo["salary_text"] == "8-12K·13薪"
    assert "示例区" in (seo["city"] or "")


def test_extract_jobs_regex_fallback_single_record():
    body = "这是一篇没有明显岗位标题的文章，只是泛泛介绍公司业务与文化。"
    jobs = wechat.extract_jobs_regex(body, "https://mp.weixin.qq.com/s/x", "某公司介绍")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "某公司介绍"


def test_collector_overrides_external_id_per_job(monkeypatch):
    og_title, body = wechat.parse_article_html(FIXTURE.read_text(encoding="utf-8"))
    url = "https://mp.weixin.qq.com/s/AbC123dEf456"

    def fake_fetch(target, cfg=None):
        return wechat.ArticleFetch(url=target, ok=True, og_title=og_title, body_text=body)

    monkeypatch.setattr(wechat, "fetch_article", fake_fetch)
    collector = WeChatPasteCollector(links=[url], cfg={"fetch": {"enabled": True}})
    records = collector.collect()

    ext_ids = [r["external_id"] for r in records]
    assert len(ext_ids) == len(set(ext_ids)), "同一文章多个岗位的 external_id 不能碰撞"
    assert len(records) >= 2
    assert all(r["url"] == url for r in records)  # url 保持干净永久链
    assert collector.report["urls_ok"] == 1
    assert collector.report["jobs"] == len(records)


def test_collector_records_skip_reason_on_fetch_failure(monkeypatch):
    def fake_fetch(target, cfg=None):
        return wechat.ArticleFetch(url=target, ok=False, reason="触发风控验证页（请改用手动粘正文）")

    monkeypatch.setattr(wechat, "fetch_article", fake_fetch)
    collector = WeChatPasteCollector(links=["https://mp.weixin.qq.com/s/Xyz"], cfg={"fetch": {"enabled": True}})
    records = collector.collect()
    assert records == []
    assert collector.report["skipped"] and "验证" in collector.report["skipped"][0]["reason"]


def test_collector_uses_pasted_body_without_fetch():
    url = "https://mp.weixin.qq.com/s/PasteOnly"
    body = "【SEO专员】\n薪资：7-9K\n工作地点：示例市\n岗位职责：SEO优化\n任职要求：熟悉SEO工具"
    collector = WeChatPasteCollector(links=[url], bodies={url: body}, cfg={"fetch": {"enabled": False}})
    records = collector.collect()
    assert len(records) >= 1
    assert any(r["title"] == "SEO专员" for r in records)
