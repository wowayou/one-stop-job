from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.models import Job, JobSourceLink
from backend.app.services.importer import upsert_job_records
from backend.app.services.normalizer import normalize_record


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_upsert_creates_json_safe_source_link_payload():
    with _session() as session:
        record = normalize_record(
            {
                "title": "SEO运营",
                "company": "示例市增长科技",
                "salary": "8-12K",
                "area": "示例市·示例区",
                "published_at": "2026-06-07",
                "recruitment_status": "在招",
            },
            source="导入文件",
        )

        result = upsert_job_records(session, [record])

        assert result == {"created": 1, "updated": 0}
        link = session.exec(select(JobSourceLink)).first()
        assert link is not None
        assert link.published_at == date(2026, 6, 7)
        assert link.raw_payload["published_at"] == "2026-06-07"


def test_upsert_merges_cross_source_by_canonical_key_and_keeps_primary_link():
    with _session() as session:
        first = normalize_record(
            {
                "title": "SEO运营",
                "company": "示例市增长科技",
                "salary": "8-12K",
                "area": "示例市·示例区",
                "url": "https://mp.weixin.qq.com/s/a",
            },
            source="公众号",
        )
        second = normalize_record(
            {
                "title": "SEO运营",
                "company": "示例市增长科技",
                "salary": "9-13K",
                "area": "示例市·示例区",
                "url": "https://bebee.com/cn/job/abc",
            },
            source="beBee",
        )

        assert upsert_job_records(session, [first]) == {"created": 1, "updated": 0}
        assert upsert_job_records(session, [second]) == {"created": 0, "updated": 1}

        jobs = session.exec(select(Job)).all()
        links = session.exec(select(JobSourceLink)).all()

        assert len(jobs) == 1
        assert jobs[0].source == "公众号"
        assert jobs[0].external_id == first["external_id"]
        assert jobs[0].url == first["url"]
        assert jobs[0].salary_text == "9-13K"
        assert {link.source for link in links} == {"公众号", "beBee"}
