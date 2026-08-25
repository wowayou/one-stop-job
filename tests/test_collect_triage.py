"""采集初筛：区域白名单 + 「采集不落盘，勾选才入库」的流程锁定。

背景（真机）：一次 `/collect` 抓回 89 条、直接新建 31 条 Job，大半落在根本不考虑的区。
现在采集分三路——区域挡掉的只记数、已在池中的照旧刷新、全新的进候选等人工勾选。
这些用例锁住的正是那条分叉，别让谁顺手把 upsert 挪回采集里。
"""

from __future__ import annotations

import pytest
import yaml
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.candidates import strip_ui_only_fields
from backend.app.models import ChatMessage, ChatThread, Job
from backend.app.services.collect_filter import (
    DEFAULT_MAX_PENDING,
    DEFAULT_MIN_SCORE,
    apply_area_filter,
    apply_score_gate,
    area_allowed,
    normalize_area,
)
from backend.app.services.collect_ops import collect_run_summary, run_collector
from backend.app.services.importer import split_known_records, upsert_job_records
from backend.app.services.normalizer import normalize_record

_AREAS = {"enabled": True, "cities": ["青岛"], "areas": ["市北", "市南", "崂山"]}


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _record(title: str, area: str = "市南区", salary: str = "8-12K", company: str = "示例科技") -> dict:
    return normalize_record(
        {"title": title, "company": company, "salary": salary, "city": "青岛", "area": area, "url": f"https://x/{title}"},
        source="BOSS直聘",
    )


class _StubCollector:
    """桩采集器：不联网（CLAUDE.md §4），只把给定记录原样交出来。"""

    def __init__(self, records: list[dict]):
        self._records = records
        self.report = {"jobs": len(records)}

    def collect(self) -> list[dict]:
        return [dict(record) for record in self._records]


@pytest.fixture
def collect_settings(monkeypatch, tmp_path):
    """把 get_settings 指到 tmp 配置。

    真实 config.yaml 里区域过滤是开着的（机主自己的市北/市南/崂山），用例若读它就会
    随本机配置飘。用完必须 cache_clear，否则 tmp 配置会漏给后面的用例。
    """
    from backend.app import config

    def _apply(**collect_cfg):
        path = tmp_path / "collect-config.yaml"
        path.write_text(
            yaml.safe_dump(
                {"general": {"data_dir": str(tmp_path / "data")}, "collect": collect_cfg},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(path))
        config.get_settings.cache_clear()

    yield _apply
    config.get_settings.cache_clear()


# ==================== 区域白名单（纯函数） ====================


def test_normalize_area_strips_administrative_suffix():
    assert normalize_area("市南区") == "市南"
    assert normalize_area("青岛市") == "青岛"
    # 「新区」要排在「区」前面，否则「黄岛新区」会被削成「黄岛新」。
    assert normalize_area("黄岛新区") == "黄岛"
    assert normalize_area("  崂山  ") == "崂山"
    assert normalize_area(None) == ""


def test_area_allowed_matches_whitelist_regardless_of_suffix():
    assert area_allowed({"city": "青岛", "area": "市南区"}, _AREAS) == (True, "")
    assert area_allowed({"city": "青岛", "area": "市南"}, _AREAS) == (True, "")
    assert area_allowed({"city": "青岛", "area": "黄岛区"}, _AREAS)[0] is False
    # 城市不符直接出局，不必再看区。
    assert area_allowed({"city": "济南", "area": "市南区"}, _AREAS) == (False, "city")


def test_area_allowed_treats_city_echo_as_unknown_area():
    """`parse_city_area` 对单段输入会把 city/area 填成同一个值——那是城市，不是区。

    默认放行：未知 ≠ 不符，公众号/beBee 常常没有区，默认挡掉等于整批吃掉那两个来源。
    """
    record = {"city": "青岛", "area": "青岛"}
    assert area_allowed(record, _AREAS) == (True, "")
    assert area_allowed(record, {**_AREAS, "keep_unknown_area": False}) == (False, "unknown")


def test_apply_area_filter_reports_counts_and_samples():
    records = [
        {"title": "A", "city": "青岛", "area": "市南区"},
        {"title": "B", "city": "青岛", "area": "黄岛区"},
        {"title": "C", "city": "青岛", "area": "青岛"},
    ]
    kept, report = apply_area_filter(records, {**_AREAS, "keep_unknown_area": False})
    assert [item["title"] for item in kept] == ["A"]
    # §7 不静默丢数据：挡掉多少、其中多少是区域未知、样例长什么样，都要能查。
    assert report == {
        "enabled": True,
        "kept": 1,
        "filtered": 2,
        "unknown_area": 1,
        "samples": ["B · 青岛 · 黄岛区", "C · 青岛 · 青岛"],
    }

    # 默认（未知放行）只挡明确不符的那条。
    default_kept, default_report = apply_area_filter(records, _AREAS)
    assert [item["title"] for item in default_kept] == ["A", "C"]
    assert (default_report["filtered"], default_report["unknown_area"]) == (1, 0)

    # 关掉（或没配区）时原样放行，报告标明未启用。
    assert apply_area_filter(records, {"enabled": False})[0] == records
    assert apply_area_filter(records, {"enabled": True, "areas": []})[1]["enabled"] is False
    assert apply_area_filter(records, None)[1]["enabled"] is False


# ==================== 已知/全新分流 ====================


def test_split_known_records_is_read_only():
    """分流只读：不许因为「看了一眼」就先把公司行建出来。"""
    from backend.app.models import Company

    with _session() as session:
        known_record = _record("已入库岗位")
        upsert_job_records(session, [known_record])
        companies_before = len(session.exec(select(Company)).all())

        known, fresh = split_known_records(session, [known_record, _record("全新岗位", company="新公司")])

        assert [item["title"] for item in known] == ["已入库岗位"]
        assert [item["title"] for item in fresh] == ["全新岗位"]
        assert len(session.exec(select(Company)).all()) == companies_before  # 没有偷偷建「新公司」


# ==================== 采集主流程 ====================


def test_run_collector_stages_new_jobs_instead_of_importing(collect_settings):
    collect_settings(area_filter={**_AREAS, "keep_unknown_area": False})
    with _session() as session:
        run = run_collector(
            session,
            "BOSS直聘",
            _StubCollector([_record("独立站运营"), _record("外贸业务员", area="黄岛区")]),
        )

        # 核心不变量：采集不再新建 Job，一条都不进池子。
        assert session.exec(select(Job)).all() == []
        assert run["created_count"] == 0
        assert run["status"] == "success"

        report = run["raw_config"]
        assert report["area_filter"]["filtered"] == 1
        assert report["pending"] == 1
        assert report["jobs"] == 2  # collector.report 仍原样并进来

        thread = session.exec(select(ChatThread)).one()
        assert thread.kind == "collect"
        assert thread.title.startswith("采集 · BOSS直聘 · ")
        message = session.exec(select(ChatMessage)).one()
        candidates = message.metadata_json["candidates"]
        assert [item["title"] for item in candidates] == ["独立站运营"]
        assert candidates[0]["status"] == "pending" and candidates[0]["job_id"] is None
        assert candidates[0]["score"] is not None  # 排序要用，手机清单也要显示
        assert "待筛 1 条" in message.content


def test_run_collector_refreshes_known_jobs_without_new_candidates(collect_settings):
    """已在池子里的岗位是你早就筛过的：照旧刷新快照，不再塞回候选让你重筛一遍。"""
    collect_settings(area_filter={"enabled": False})
    with _session() as session:
        upsert_job_records(session, [_record("独立站运营", salary="8-12K")])

        run = run_collector(session, "BOSS直聘", _StubCollector([_record("独立站运营", salary="12-18K")]))

        job = session.exec(select(Job)).one()
        assert job.salary_text == "12-18K"  # 刷新到位
        assert run["created_count"] == 0 and run["updated_count"] == 1
        assert run["raw_config"]["known_refreshed"] == 1
        assert run["raw_config"]["pending"] == 0
        assert session.exec(select(ChatThread)).all() == []  # 没有待筛项就不建空线索


def test_run_collector_does_not_resurface_pending_or_skipped(collect_settings):
    """早上采过的中午别再列一遍；你明确跳过的，明天也别再冒出来。"""
    collect_settings(area_filter={"enabled": False})
    with _session() as session:
        first = run_collector(session, "BOSS直聘", _StubCollector([_record("独立站运营")]))
        assert first["raw_config"]["pending"] == 1

        second = run_collector(session, "BOSS直聘", _StubCollector([_record("独立站运营")]))
        assert second["raw_config"]["pending"] == 0
        assert second["raw_config"]["already_pending"] == 1
        assert len(session.exec(select(ChatThread)).all()) == 1  # 没有第二条空线索

        # 用户点了「全部跳过」之后，同一岗位再采到也不该复活。
        message = session.exec(select(ChatMessage)).first()
        message.metadata_json = {
            **message.metadata_json,
            "candidates": [{**message.metadata_json["candidates"][0], "status": "skipped"}],
        }
        session.add(message)
        session.commit()

        third = run_collector(session, "BOSS直聘", _StubCollector([_record("独立站运营")]))
        assert third["raw_config"]["pending"] == 0
        assert third["raw_config"]["already_pending"] == 1


def test_collect_candidate_is_upsertable_after_stripping_ui_fields(collect_settings):
    """候选勾选入库那一步（commit 端点）拿到的记录必须能直接 upsert。

    `score` 是纯 UI 字段，Job 表没有这一列——漏剔除就会在真正入库时炸。
    """
    collect_settings(area_filter={"enabled": False})
    with _session() as session:
        run_collector(session, "BOSS直聘", _StubCollector([_record("独立站运营")]))
        candidate = session.exec(select(ChatMessage)).one().metadata_json["candidates"][0]
        assert "score" in candidate

        record = strip_ui_only_fields(candidate)
        record.pop("status", None)
        record.pop("job_id", None)
        assert "score" not in record

        assert upsert_job_records(session, [record]) == {"created": 1, "updated": 0}
        assert session.exec(select(Job)).one().title == "独立站运营"


def test_pending_candidate_rows_feed_digest_sorted_by_score(collect_settings):
    """晨间清单的「待筛岗位」段读的就是这些候选：按分排序，已勾/已跳的不再出现。"""
    from backend.app.services.daily_digest import pending_candidate_rows

    collect_settings(area_filter={"enabled": False})
    with _session() as session:
        run_collector(
            session,
            "BOSS直聘",
            _StubCollector([_record("独立站运营"), _record("前台文员", company="别的公司")]),
        )
        message = session.exec(select(ChatMessage)).one()
        candidates = message.metadata_json["candidates"]
        assert [item["score"] for item in candidates] == sorted(
            [item["score"] for item in candidates], reverse=True
        )

        rows = pending_candidate_rows(session)
        assert len(rows) == 2
        assert rows[0]["score"] >= rows[1]["score"]
        assert rows[0]["title"] in {"独立站运营", "前台文员"}

        message.metadata_json = {
            **message.metadata_json,
            "candidates": [{**candidates[0], "status": "committed"}, {**candidates[1], "status": "skipped"}],
        }
        session.add(message)
        session.commit()
        assert pending_candidate_rows(session) == []


# ==================== 候选闸门（纯函数） ====================


def _cand(title: str, score: float | None, *, hard_blocked: bool = False) -> dict:
    return {"title": title, "score": score, "hard_blocked": hard_blocked}


def test_score_gate_drops_hard_blocked_and_below_threshold():
    """硬排除 + 低分一律不进待筛：漏斗必须逐级收窄，不是把同一批原样端出来。

    硬排除项是本人早就写下的底线（dealbreakers/城市/薪资），不该每次采集都摆到眼前
    重新否决一遍；低分是评分已经判定不合格。两者都只记数，不静默丢（§7）。
    """
    kept, report = apply_score_gate(
        [
            _cand("独立站运营", 72.5),
            _cand("广告优化师", 61.0, hard_blocked=True),
            _cand("线上课程优化", 24.4),
            _cand("外贸网站运营", 45.0),  # 恰好等于门槛：放行（下界包含）
        ],
        {"enabled": True},
    )

    assert [item["title"] for item in kept] == ["独立站运营", "外贸网站运营"]
    assert (report["hard_blocked"], report["below_score"], report["truncated"]) == (1, 1, 0)
    assert report["kept"] == 2
    # 挡掉的要能在 Web 面板上认出是哪类岗位，且带原因。
    assert report["samples"] == ["广告优化师 · 命中硬性排除", "线上课程优化 · 24.4 分低于 45"]


def test_score_gate_keeps_unscored_candidates():
    """`score is None` 是我们自己的评分故障，不能因此静默吃掉一个可能合适的岗位。"""
    kept, report = apply_score_gate([_cand("评分失败的岗位", None)], {"enabled": True})
    assert [item["title"] for item in kept] == ["评分失败的岗位"]
    assert (report["hard_blocked"], report["below_score"]) == (0, 0)


def test_score_gate_truncates_tail_by_max_pending():
    """超出单次上限的按分截断（输入已降序），尾部只记数——注意力优先给头部。"""
    candidates = [_cand(f"岗位{i}", 90.0 - i) for i in range(6)]
    kept, report = apply_score_gate(candidates, {"enabled": True, "max_pending": 3})
    assert [item["title"] for item in kept] == ["岗位0", "岗位1", "岗位2"]
    assert report["truncated"] == 3
    assert report["kept"] == 3


def test_score_gate_disabled_passes_everything_through():
    """关掉闸门 = 回到加它之前的行为，一条都不挡。"""
    candidates = [_cand("低分", 10.0), _cand("硬排除", 80.0, hard_blocked=True)]
    kept, report = apply_score_gate(candidates, {"enabled": False})
    assert kept == candidates
    assert report["enabled"] is False


def test_score_gate_falls_back_to_defaults_on_invalid_config():
    """配置值非法时退回默认门槛，绝不因为读配置失败就整批放行或整批挡掉。"""
    _, report = apply_score_gate([_cand("岗位", 50.0)], {"enabled": True, "min_score": "abc", "max_pending": None})
    assert report["min_score"] == DEFAULT_MIN_SCORE
    assert report["max_pending"] == DEFAULT_MAX_PENDING


# ==================== 闸门接入采集主流程 ====================


def test_run_collector_narrows_candidates_by_score_gate(collect_settings):
    """采集主流程里闸门要真的生效，且挡掉的条数出现在 SourceRun 报告与线索正文里。"""
    collect_settings(area_filter={"enabled": False}, score_gate={"enabled": True, "min_score": 55})
    with _session() as session:
        run = run_collector(
            session,
            "BOSS直聘",
            _StubCollector([_record("独立站运营"), _record("前台文员", company="别的公司")]),
        )

        gate = run["raw_config"]["score_gate"]
        assert gate["enabled"] is True
        # 两条都是 60 分基线（测试画像为空），55 分线下全放行；关键是报告结构落进了 raw_config。
        assert gate["kept"] == run["raw_config"]["pending"]

        collect_settings(area_filter={"enabled": False}, score_gate={"enabled": True, "min_score": 99})
        second = run_collector(session, "BOSS直聘", _StubCollector([_record("谷歌SEO", company="第三家")]))
        second_gate = second["raw_config"]["score_gate"]
        assert second_gate["below_score"] == 1
        assert second["raw_config"]["pending"] == 0
        # 全被挡掉时不建空线索（与「没有待筛项」同一口径）。
        assert len(session.exec(select(ChatThread)).all()) == 1


def test_collect_run_summary_reports_gate_counts():
    """计数摘要必须报出闸门挡掉的条数，否则「抓了 30 条只剩 4 条」看着像丢数据。"""
    from backend.app.services.collect_ops import collect_run_summary

    text = collect_run_summary(
        30,
        {
            "area_filter": {"enabled": True, "filtered": 8},
            "score_gate": {"enabled": True, "hard_blocked": 5, "below_score": 10, "truncated": 3, "min_score": 45.0},
            "pending": 4,
        },
    )
    assert "本次采集 30 条" in text
    assert "区域过滤 8 条" in text
    assert "硬排除 5 条" in text
    assert "低于 45 分 10 条" in text
    assert "超出单次上限暂缓 3 条" in text
    assert text.endswith("待筛 4 条。")


def test_digest_excludes_hard_blocked_pending_candidates(collect_settings):
    """手机清单不推硬阻断候选：Web 里它们是折叠的，推到手机上等于重新要一次注意力。

    闸门关闭或历史遗留时兜底——`pending_candidate_rows` 自己也要过滤。
    """
    from backend.app.services.daily_digest import pending_candidate_rows

    collect_settings(area_filter={"enabled": False}, score_gate={"enabled": False})
    with _session() as session:
        run_collector(session, "BOSS直聘", _StubCollector([_record("独立站运营"), _record("广告优化师", company="别家")]))
        message = session.exec(select(ChatMessage)).one()
        candidates = message.metadata_json["candidates"]
        message.metadata_json = {
            **message.metadata_json,
            "candidates": [{**candidates[0], "hard_blocked": True}, {**candidates[1], "hard_blocked": False}],
        }
        session.add(message)
        session.commit()

        rows = pending_candidate_rows(session)
        assert [row["title"] for row in rows] == [candidates[1]["title"]]
