from datetime import date

from backend.app.services.board_sla import (
    BoardAction,
    BoardCompanyIndex,
    companies_match,
    format_digest,
    normalize_company,
    parse_board_actions,
    parse_board_companies,
    split_due,
)

_TODAY = date(2026, 8, 13)

# 合成看板样例：结构复刻 Obsidian Kanban（frontmatter + `## 列` + 卡片行 + 设置块），
# 内容全部虚构（红线：公开仓库只含合成测试数据）。
_BOARD = """---

kanban-plugin: board

---

## 收集箱

- [ ] 新岗位线索 - 薪资未知 - 渠道/日期 - 未判断 - 下一步：补齐主行并新建详情卡 - [模板](job-pipeline/_template.md)


## 待沟通

- [ ] 示例机械 - 外贸独立站运营 - 7-14K - 智联/0807 - 纸面命中三项 - 下一步：**0808 发出**，一条消息只问两件事。0813 无回复则结束 - [详情](job-pipeline/cards/a.md)
- [ ] 暂无


## 已沟通

- [ ] 示例信息 - SEO/GEO - 8-12K - BOSS/0810 - 附件筛选中 - 下一步：先给 10-15K 期望不动，0812下午仍无新互动时只跟进一次，推动10-15分钟电话 - [详情](job-pipeline/cards/b.md)


## 面试（R1/R2/R3）

- [ ] 示例新材 - 谷歌SEO - 10-15K - BOSS/0729 - R1 已完成 - 下一步：2026-08-14 前确认复试安排 - [详情](job-pipeline/cards/c.md)


## 已结束（拒 / 放弃）

- [x] 示例地板 - 高级SEO专员 - 10-11K - BOSS/0811 - 已收口 - 下一步：结束，0812 不再跟进 - [详情](job-pipeline/cards/d.md)
- [x] 青岛示例三维化妆品 - Shopify独立站运营 - 7-11k - BOSS/0811 - 已收口 - 下一步：继续结束，不回复 - [详情](job-pipeline/cards/e.md)
- [x] 示例信息 - 网站运营专员 - 8-10k - 智联/0726 - 旧岗已收口 - 下一步：结束 - [详情](job-pipeline/cards/f.md)
- [x] 合宜 - 谷歌SEM专员 - 6-10K - BOSS/0706 - 已收口 - 下一步：礼貌拒绝 - [详情](job-pipeline/cards/g.md)


## 归档（长期不跟）

- [ ] 青岛示例包装科技 - SEO数字营销运营经理 - 15-30K - BOSS/0707 - 归档 - 下一步：不主动 - [详情](job-pipeline/cards/h.md)


%% kanban:settings
```
{"kanban-plugin":"board"}
```
%%
"""


def test_parse_only_active_columns_and_unchecked_cards():
    actions = parse_board_actions(_BOARD, _TODAY)
    cards = {action.card for action in actions}
    # 收集箱模板行、`暂无` 占位、已结束列的 [x] 卡都不产生动作。
    assert cards == {
        "示例机械 - 外贸独立站运营 - 7-14K",
        "示例信息 - SEO/GEO - 8-12K",
        "示例新材 - 谷歌SEO - 10-15K",
    }


def test_dates_only_from_next_step_and_salary_not_misread():
    actions = [a for a in parse_board_actions(_BOARD, _TODAY) if a.card.startswith("示例信息")]
    # 「BOSS/0810」在下一步标记之前，不解析；「10-15K」「10-15分钟」不是日期。
    assert [a.due for a in actions] == [date(2026, 8, 12)]
    assert actions[0].kind == "follow"


def test_kind_classification_send_and_close():
    actions = [a for a in parse_board_actions(_BOARD, _TODAY) if a.card.startswith("示例机械")]
    assert [(a.due, a.kind) for a in actions] == [
        (date(2026, 8, 8), "send"),
        (date(2026, 8, 13), "close"),
    ]


def test_iso_date_in_interview_column():
    actions = [a for a in parse_board_actions(_BOARD, _TODAY) if a.card.startswith("示例新材")]
    assert [(a.due, a.kind) for a in actions] == [(date(2026, 8, 14), "follow")]


def test_split_due_buckets_and_overdue_days():
    sections = split_due(parse_board_actions(_BOARD, _TODAY), _TODAY)
    assert [item["due"] for item in sections["due_send"]] == ["2026-08-08"]
    assert sections["due_send"][0]["overdue_days"] == 5
    assert [item["due"] for item in sections["due_follow"]] == ["2026-08-12"]
    assert [item["due"] for item in sections["due_close"]] == ["2026-08-13"]
    assert sections["due_close"][0]["overdue_days"] == 0
    assert [item["due"] for item in sections["upcoming"]] == ["2026-08-14"]


def test_year_inference_across_new_year():
    content = "## 待沟通\n\n- [ ] 示例贸易 - 海外推广 - 8-10K - 智联/1228 - 判断 - 下一步：12月30日 前发出首次消息"
    actions = parse_board_actions(content, date(2027, 1, 2))
    assert [a.due for a in actions] == [date(2026, 12, 30)]


def test_format_digest_contains_sections_and_stale():
    sections = split_due(parse_board_actions(_BOARD, _TODAY), _TODAY)
    stale = [{"company_name": "示例科技", "title": "SEO运营", "reason": "interview 状态已 6 天无跟进"}]
    text = format_digest(sections, stale, _TODAY)
    assert "2026-08-13 求职日清单" in text
    assert "今日必发" in text and "示例机械" in text
    assert "今日跟进" in text and "今日收口" in text
    assert "示例科技" in text


def test_format_digest_empty_board_has_fallback_line():
    text = format_digest(split_due([], _TODAY), [], _TODAY)
    assert "没有到期动作" in text


def test_upcoming_window_is_seven_days():
    actions = [
        BoardAction(column="待沟通", card="示例A - 岗位 - 8K", due=date(2026, 8, 20), kind="send", snippet="发出"),
        BoardAction(column="待沟通", card="示例B - 岗位 - 8K", due=date(2026, 8, 21), kind="send", snippet="发出"),
    ]
    sections = split_due(actions, _TODAY)
    assert [item["card"] for item in sections["upcoming"]] == ["示例A - 岗位 - 8K"]


def test_split_message_respects_line_boundaries():
    from backend.app.services.telegram import split_message

    text = "\n".join(f"第{i}行内容" for i in range(1, 8))
    chunks = split_message(text, chunk_size=20)
    assert all(len(chunk) <= 20 for chunk in chunks)
    assert "\n".join(chunks) == text  # 行边界拆分，不丢字
    assert split_message("", 20) == []


def test_split_message_hard_splits_overlong_single_line():
    from backend.app.services.telegram import split_message

    text = "甲" * 50
    chunks = split_message(text, chunk_size=20)
    assert all(len(chunk) <= 20 for chunk in chunks)
    assert "".join(chunks) == text


def test_should_send_now_catch_up_semantics():
    from datetime import datetime

    from backend.app.services.daily_digest import should_send_now

    # 到点且今天未发 → 发（含开机晚于发送时点的补发）。
    assert should_send_now(datetime(2026, 8, 13, 8, 20), None, 8, 20) is True
    assert should_send_now(datetime(2026, 8, 13, 14, 0), "2026-08-12", 8, 20) is True
    # 未到点不发；今天已发不重发。
    assert should_send_now(datetime(2026, 8, 13, 8, 19), None, 8, 20) is False
    assert should_send_now(datetime(2026, 8, 13, 14, 0), "2026-08-13", 8, 20) is False


def test_digest_state_roundtrip(tmp_path):
    from backend.app.services.daily_digest import read_state, write_state

    state = tmp_path / "state.json"
    assert read_state(state) == {}  # 缺文件按从未发过
    write_state(state, last_sent="2026-08-13")
    assert read_state(state)["last_sent"] == "2026-08-13"
    state.write_text("not json", encoding="utf-8")
    assert read_state(state) == {}  # 损坏文件不炸循环


def test_digest_state_write_merges_instead_of_replacing(tmp_path):
    """写 last_sent 不能抹掉 last_collected：两个日期分开记才能「重试发送但不重复采集」。"""
    from backend.app.services.daily_digest import last_collected_note, read_state, write_state

    state = tmp_path / "state.json"
    write_state(state, last_collected="2026-08-14", collect_note="⚠️ 采集炸了")
    write_state(state, last_sent="2026-08-14")
    saved = read_state(state)
    assert saved == {
        "last_collected": "2026-08-14",
        "collect_note": "⚠️ 采集炸了",
        "last_sent": "2026-08-14",
    }
    assert last_collected_note(saved, "2026-08-14") == "⚠️ 采集炸了"
    assert last_collected_note(saved, "2026-08-15") == ""  # 昨天的失败不漏进今天的清单
    state.write_text("[]", encoding="utf-8")  # 合法 JSON 但不是对象
    assert read_state(state) == {}


def test_format_collect_failure_is_single_line_and_truncated():
    from backend.app.services.daily_digest import format_collect_failure

    assert format_collect_failure(None) == ""
    assert format_collect_failure("   ") == ""
    note = format_collect_failure("全部关键词采集失败:\n  Browser Bridge extension not connected")
    assert "\n" not in note  # 压成一行，别把几 KB 的 opencli 帮助文本铺进推送
    assert "Browser Bridge" in note
    assert note.startswith("⚠️")
    long_note = format_collect_failure("失败原因" * 200)
    assert "…" in long_note and len(long_note) < 200  # 截断，但仍带尾注
    assert long_note.endswith("（详情见 Web 采集面板；下面的岗位可能不是最新的）")


def test_format_collect_failure_drops_repr_dump():
    """多关键词采集器把每个关键词的 dict repr 塞进 error（几 KB + cmd.exe 乱码），
    手机上只该看到抬头；逐条原因留在 backend.log 与 Web 采集面板。"""
    from backend.app.services.daily_digest import format_collect_failure

    raw = (
        "全部关键词采集失败: [{'command': 'boss search 独立站运营', 'reason': \"Command "
        "'['cmd.exe', '/c', 'opencli.cmd']' timed out after 120 seconds\"}]"
    )
    note = format_collect_failure(raw)
    assert note == "⚠️ 今日晨间采集未成功：全部关键词采集失败（详情见 Web 采集面板；下面的岗位可能不是最新的）"
    assert "cmd.exe" not in note and "{" not in note


def test_parse_board_companies_buckets_closed_and_active():
    index = parse_board_companies(_BOARD)
    # 已结束 + 归档进 closed；收集箱模板行、`暂无`、模板库不进任何桶。
    assert "示例地板" in index.closed and "示例包装" in index.closed
    assert "示例机械" in index.active and "示例新材" in index.active
    # 「示例信息」同时在已沟通与已结束列出现，两个桶都记，由 match 决定优先级。
    assert "示例信息" in index.active and "示例信息" in index.closed


def test_company_match_full_name_vs_abbreviation():
    """看板简称 ↔ 岗位库全称双向命中：去前后缀后包含式比较。"""
    index = parse_board_companies(_BOARD)
    # 看板全称「青岛示例三维化妆品」→ 核「示例三维化妆品」；岗位库简称「示例三维」。
    assert index.match("示例三维") == "closed"
    # 反向：看板简称「示例机械」；岗位库全称「青岛示例机械制造有限公司」。
    assert index.match("青岛示例机械制造有限公司") == "active"
    # 归档列全称带「青岛…科技」后缀，岗位库写「示例包装」。
    assert index.match("示例包装") == "closed"


def test_company_match_does_not_kill_different_company():
    """宁漏配不误杀：短名只允许全等，长名不同前缀不互相命中。"""
    index = parse_board_companies(_BOARD)
    # 看板「合宜」核只有 2 字，不允许包含式匹配 → 「合宜斯生物」不是同一家。
    assert index.match("合宜斯生物") is None
    assert index.match("合宜") == "closed"  # 全等仍然命中
    # 完全无关的公司不因共用「示例」两字被误判。
    assert index.match("示例无关能源") is None
    assert index.match("") is None
    # 不做模糊相似度：错一字即视为不同公司。
    assert companies_match(normalize_company("示例地板"), normalize_company("示例地扳")) is False


def test_company_match_active_column_wins_over_closed():
    """同一家公司既有已收口旧岗又在活跃列 → 按活跃处理（标注而非剔除）。"""
    assert parse_board_companies(_BOARD).match("示例信息") == "active"


def test_normalize_company_strips_affixes_only_at_edges():
    assert normalize_company("青岛七联洲际贸易有限公司") == "七联洲际"
    assert normalize_company("山东示例信息技术") == "示例"
    # 只削首尾：中间的「科技」不动，短名不被削空。
    assert normalize_company("示例科技园设备") == "示例科技园设备"
    assert normalize_company("科技") == "科技"


def test_reconcile_new_jobs_filters_closed_and_marks_active():
    from backend.app.services.daily_digest import reconcile_new_jobs

    scored = [
        {"title": "独立站运营", "company_name": "示例三维", "score": 80.0},
        {"title": "外贸独立站运营", "company_name": "青岛示例机械制造", "score": 78.0},
        {"title": "SEO专员", "company_name": "示例包装", "score": 77.0},
        {"title": "网站运营", "company_name": "示例无关能源", "score": 76.0},
    ]
    kept, filtered = reconcile_new_jobs(scored, parse_board_companies(_BOARD))
    assert [item["company_name"] for item in kept] == ["青岛示例机械制造", "示例无关能源"]
    assert filtered == 2  # 示例三维（已结束）+ 示例包装（归档）
    assert kept[0]["board_state"] == "active"
    assert "board_state" not in kept[1]  # 看板上没有的岗位不加标注


def test_reconcile_filters_before_truncation():
    """剔除必须发生在截断之前，否则已收口公司会白占前 top_n 名额。"""
    from backend.app.services.daily_digest import reconcile_new_jobs

    scored = [{"title": "岗位", "company_name": "示例三维", "score": 90.0}] + [
        {"title": "岗位", "company_name": f"示例无关{i}号能源", "score": 80.0 - i} for i in range(3)
    ]
    kept, filtered = reconcile_new_jobs(scored, parse_board_companies(_BOARD), top_n=2)
    assert [item["company_name"] for item in kept] == ["示例无关0号能源", "示例无关1号能源"]
    assert filtered == 1


def test_board_unreadable_degrades_to_no_filter(monkeypatch):
    """看板不可解析时降级为「不过滤」，不让整个日清单报错。"""
    from backend.app.services import daily_digest

    def boom(_content):
        raise ValueError("看板结构变了")

    monkeypatch.setattr(daily_digest, "parse_board_companies", boom)
    assert daily_digest.board_company_index("## 待沟通\n- [ ] 示例 - 岗位 - 8K") is None

    scored = [{"title": "岗位", "company_name": "示例三维", "score": 80.0}]
    kept, filtered = daily_digest.reconcile_new_jobs(scored, None)
    assert kept == scored and filtered == 0  # 原样返回，一条不剔


def test_format_new_jobs_marks_board_hit_and_filtered_count():
    from backend.app.services.daily_digest import format_new_jobs

    text = format_new_jobs(
        [
            {"title": "外贸独立站运营", "company_name": "示例机械", "salary": "7-14K", "score": 78.0, "board_state": "active"},
            {"title": "网站运营", "company_name": "示例无关能源", "salary": None, "score": 76.0},
        ],
        filtered_closed=3,
    )
    assert "78.0 分｜看板已有" in text
    assert "76.0 分）" in text and "看板已有" not in text.split("示例无关能源")[1]
    assert "已过滤 3 条已收口公司" in text
    assert format_new_jobs([], 0) == ""  # 无岗位无过滤 → 不占位
    assert "已过滤 2 条" in format_new_jobs([], 2)  # 全被过滤时仍说明原因


def test_company_aliases_split_and_overlong_head_ignored():
    """首段多别名（A / B）都进索引；行首异常（没按「公司 - 岗位」写）放弃对账。"""
    content = (
        "## 面试（R1/R2/R3）\n\n"
        "- [ ] 青岛示例恩机械 / 示例恩电机 - 外贸运营 - 6-10K - BOSS/0814 - 判断 - 下一步：0814 面试\n"
        "\n## 已结束（拒 / 放弃）\n\n"
        "- [x] 示例灸石科技（SnappyExample） - 增长运营 - 20-35K - BOSS/0702 - 已收口 - 下一步：结束\n"
        "- [x] 这一行没有按公司减岗位的格式写而且长得离谱所以不应该被当成公司名参与包含匹配\n"
    )
    index = parse_board_companies(content)
    assert "示例恩机械" in index.active and "示例恩电机" in index.active
    assert "示例灸石" in index.closed and "SnappyExample" in index.closed
    # 超长行不产出公司名，也就不会拿一整行去误杀岗位库里的公司。
    assert not any(len(name) > 24 for name in index.closed)


def test_board_company_index_is_frozen_dataclass():
    """索引不可变：对账只读，任何消费端都不能反过来改看板事实。"""
    import dataclasses

    import pytest

    index = BoardCompanyIndex(closed=("甲",), active=("乙",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        index.closed = ("丙",)  # type: ignore[misc]


def test_collect_new_jobs_reuses_existing_score(monkeypatch):
    """摘要复用已有 FitScore：重复生成不得让 fit_scores 线性膨胀（score_job_into_db 是追加语义）。"""
    from datetime import datetime, timezone

    from sqlmodel import Session, SQLModel, create_engine, func, select

    from backend.app.models import FitScore, Job
    from backend.app.services.daily_digest import collect_new_jobs

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        job = Job(
            source="manual",
            external_id="fresh-1",
            title="独立站运营",
            company_name="示例科技",
            status="new",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        first = collect_new_jobs(session)
        assert [item["title"] for item in first] == ["独立站运营"]
        rows_after_first = session.exec(select(func.count()).select_from(FitScore)).one()

        second = collect_new_jobs(session)
        assert [item["score"] for item in second] == [item["score"] for item in first]
        assert session.exec(select(func.count()).select_from(FitScore)).one() == rows_after_first
