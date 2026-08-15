"""晨间日清单组装：看板到期动作 + 库内 stale 岗位 → 结构化 payload / 摘要文本。

被 `routers/followups.py` 的 board-sla 端点与 `main.py` 的每日推送循环共用，
避免两处组装逻辑漂移。只读（板块解析见 services/board_sla.py 的红线说明）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from sqlmodel import Session

from ..config import get_settings
from ..models import utc_now
from .board_sla import BoardCompanyIndex, format_digest, parse_board_actions, parse_board_companies, split_due
from .context_repository import ContextRepository
from .followup import find_stale_jobs

logger = logging.getLogger(__name__)


def pending_candidate_rows(session: Session) -> list[dict]:
    """待你初筛的采集候选 → 清单行（按匹配分降序）。

    采集不再直接落盘（见 `collect_ops`），所以「今天有什么新岗位」的事实源不再是 Job 表，
    而是采集线索里 `status=pending` 的候选。分数是采集时就算好的（`attach_candidate_scores`，
    与岗位池 FitScore 同一个 `scoring.score_job`），这里只读，不重算、不落库。

    不限「今天」：昨天没筛完的照样该出现在今天的清单里——它是待办，不是流水。
    """
    from .chat_ingest import recent_collect_candidates

    rows = [
        {
            "title": str(item.get("title") or "未命名岗位"),
            "company_name": str(item.get("company_name") or "未知公司"),
            "salary": item.get("salary_text"),
            "area": " · ".join(filter(None, [item.get("city"), item.get("area")])),
            "score": item.get("score"),
            "url": item.get("url"),
        }
        for item in recent_collect_candidates(session)
        if str(item.get("status") or "pending") == "pending"
    ]
    rows.sort(key=lambda row: row["score"] if row["score"] is not None else -1, reverse=True)
    return rows


def board_company_index(content: str) -> BoardCompanyIndex | None:
    """看板文本 → 公司对账索引；解析不出来返回 None = 本次不过滤。

    降级优先于报错：看板结构变化不该让整个日清单挂掉，最坏结果只是
    像对账上线前那样多显示几条已收口公司。
    """
    try:
        return parse_board_companies(content)
    except Exception:  # noqa: BLE001 - 对账是增强项，解析异常一律降级为不过滤
        logger.warning("看板公司对账解析失败，本次不过滤新岗位", exc_info=True)
        return None


def reconcile_new_jobs(
    scored: list[dict], board_index: BoardCompanyIndex | None, *, top_n: int = 8
) -> tuple[list[dict], int]:
    """按看板列对账新岗位，返回 (前 top_n 条, 被剔除的已收口条数)。

    - 已结束/归档列的公司 → 剔除（本人已经收口，再推一次只是消耗注意力）；
    - 活跃列的公司 → 保留并标 `board_state="active"`，摘要里显示「看板已有」；
    - `board_index is None`（看板不可读/解析失败）→ 原样返回，计数 0。

    剔除发生在**截断之前**：否则已收口公司会白占前 top_n 的名额。
    计数覆盖窗口内全部新岗位，不只是前 top_n。
    """
    if board_index is None:
        return scored[:top_n], 0
    kept: list[dict] = []
    filtered_closed = 0
    for item in scored:
        state = board_index.match(item.get("company_name") or "")
        if state == "closed":
            filtered_closed += 1
            continue
        kept.append({**item, "board_state": state} if state else item)
    return kept[:top_n], filtered_closed


def format_new_jobs(new_jobs: list[dict], filtered_closed: int = 0, total: int | None = None) -> str:
    """待筛岗位段文本；无岗位且无过滤时返回空串（不在摘要里占位）。

    这些岗位**还没入库**：采集只把新岗位挂成候选，勾选后才写 Job 表（见 `collect_ops`）。
    所以段尾必须给出去哪儿处理，否则手机上读到一串岗位却不知道下一步做什么。
    `total` 是截断前的待筛总数，比列出来的多时补一句，免得以为只有这几条。
    """
    if not new_jobs and not filtered_closed:
        return ""
    lines = ["", "🆕 待筛岗位（评分前列，未入库）"]
    for item in new_jobs:
        parts = [item["title"], item["company_name"], item.get("salary") or "薪资未知"]
        if item.get("area"):
            parts.append(item["area"])
        marker = "｜看板已有" if item.get("board_state") == "active" else ""
        score = item.get("score")
        score_text = f"{score} 分" if score is not None else "未评分"
        lines.append(f"· {' - '.join(parts)}（{score_text}{marker}）")
        if item.get("url"):
            lines.append(f"  {item['url']}")
    if filtered_closed:
        lines.append(f"（已过滤 {filtered_closed} 条已收口公司的岗位）")
    if total is not None and total > len(new_jobs):
        lines.append(f"（共 {total} 条待筛）")
    if new_jobs:
        lines.append("在 Web「聊天」的采集线索里勾选入库；不勾的不会进岗位池。")
    return "\n".join(lines)


def build_new_jobs_text(session: Session, *, top_n: int = 8) -> str:
    """只取「待筛岗位」段（手动 `/collect` 补采后的回执用），无待筛项时返回空串。

    看板读不出来就不做对账：多显示几条已收口公司，好过整条补采回执失败——与
    `build_daily_digest` 里对账解析失败的降级口径一致。
    """
    index: BoardCompanyIndex | None = None
    try:
        document = ContextRepository(get_settings().context_repo_path).read_document("board")
        index = board_company_index(document.content)
    except Exception:  # noqa: BLE001 - 看板不可读只降级为不对账，不影响补采回执本身
        logger.warning("补采回执：看板不可读，本次不过滤待筛岗位", exc_info=True)
    rows = pending_candidate_rows(session)
    kept, filtered_closed = reconcile_new_jobs(rows, index, top_n=top_n)
    return format_new_jobs(kept, filtered_closed, total=len(rows) - filtered_closed)


def build_daily_digest(session: Session, today: date, *, include_new_jobs: bool = True) -> dict:
    """组装日清单。看板不可读时抛 ContextRepositoryError，由调用方决定报错方式。

    待筛岗位段与看板对账（只读）：已结束/归档列的公司剔除并计入 `filtered_closed`，
    活跃列的公司保留并标注「看板已有」。
    """
    settings = get_settings()
    repository = ContextRepository(settings.context_repo_path)
    document = repository.read_document("board")
    sections = split_due(parse_board_actions(document.content, today), today)
    stale = find_stale_jobs(session, now=utc_now(), stale_days=settings.followup_stale_days)
    new_jobs: list[dict] = []
    filtered_closed = 0
    pending_total = 0
    if include_new_jobs:
        rows = pending_candidate_rows(session)
        new_jobs, filtered_closed = reconcile_new_jobs(rows, board_company_index(document.content))
        pending_total = len(rows) - filtered_closed
    text = format_digest(sections, stale, today)
    new_jobs_text = format_new_jobs(new_jobs, filtered_closed, total=pending_total)
    if new_jobs_text:
        text = f"{text}\n{new_jobs_text}"
    return {
        "date": today.isoformat(),
        "board_updated": document.updated,
        **sections,
        "stale_jobs": stale,
        "new_jobs": new_jobs,
        "filtered_closed": filtered_closed,
        "pending_total": pending_total,
        "digest_text": text,
    }


def should_send_now(now: datetime, last_sent_day: str | None, hour: int, minute: int) -> bool:
    """今天的发送时点已过、且今天还没发过 → 该发。

    覆盖两种场景：机器一直开着，到点即发；发送时点时机器关机/休眠，开机后由
    轮询在下一个检查周期补发（而不是丢掉今天的清单等到明天）。
    """
    fire_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= fire_at and last_sent_day != now.date().isoformat()


def digest_state_path(data_dir: Path) -> Path:
    """推送状态文件路径。日清单循环与手动补采共用同一份状态，文件名不许在两处各写一遍。"""
    return data_dir / "daily_digest_state.json"


def read_state(state_path: Path) -> dict:
    """读推送状态文件；缺失/损坏一律按空状态处理（宁可多发一次，也不要因为状态坏了永远不发）。

    键：`last_sent`（最近一次**确认送达**的日期）、`last_collected`（最近一次采集成功/
    尝试的日期，含机主手动 `/collect` 补采）、`collect_note`（当日采集失败的一行附注）。
    两个日期必须分开记——发送失败要按周期重试，而定时采集的频率上限是每日一次
    （CLAUDE.md §3.3），不能跟着重试。
    """
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def write_state(state_path: Path, **updates: str) -> None:
    """合并写回：只覆盖传入的键，保留其余键（否则写 last_sent 会把 last_collected 抹掉）。"""
    state = {**read_state(state_path), **updates}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def last_collected_note(state: dict, day: str) -> str:
    """当日晨间采集失败的一行附注；不是今天采的就返回空串（昨天的失败不许漏进今天的清单）。"""
    if state.get("last_collected") != day:
        return ""
    note = state.get("collect_note")
    return note if isinstance(note, str) else ""


def mark_collect_success(data_dir: Path, day: str) -> None:
    """记下「今天已经成功采过一次」（机主手动 `/collect` 补采成功后调用）。

    两件事必须一起做：
    - 清掉 `collect_note`——日清单发送失败会在下个周期重发，带着一条已经不成立的
      「今日晨间采集未成功」会误导；
    - 写 `last_collected`——今天已经采到了，定时那次不必再跑一遍（频率上限每日一次，
      见 CLAUDE.md §3.3）。补采失败时**不写**，定时那次照旧还有机会。
    """
    write_state(digest_state_path(data_dir), last_collected=day, collect_note="")


_COLLECT_NOTE_MAX = 120


def collect_failure_headline(reason: object) -> str:
    """把采集失败原因压成一行**手机上能读**的抬头；原因为空时返回空串。

    为什么要砍掉结构化 dump：多关键词采集器失败时会把每个关键词的 dict repr 全塞进 error
    （几 KB，还夹着 cmd.exe 的乱码），照抄前 160 字符等于把噪音推到手机上。只留 `[{` 之前的
    抬头，完整原因去 backend.log 和 Web 采集面板看——那里本来就有 `report.skipped` 逐条记录。
    """
    text = " ".join(str(reason or "").split())
    if not text:
        return ""
    headline = text.split("[{", 1)[0].strip(" :：,，") or text[:_COLLECT_NOTE_MAX]
    if len(headline) > _COLLECT_NOTE_MAX:
        headline = f"{headline[:_COLLECT_NOTE_MAX]}…"
    return headline


def format_collect_failure(reason: object) -> str:
    """把晨间采集失败压成日清单正文里的一行附注；原因为空时返回空串（不在摘要里占位）。

    为什么要进推送正文：`run_source` 采集失败只把 SourceRun 置 `failed` 后返回，**不抛异常**，
    于是「新岗位」段静默为空——本人看到的是「今天没有合适岗位」，而不是「今天根本没采到」。

    为什么带上 `/collect`：定时采集失败不自动重跑（红线 §3.3），此前手机上读到这条附注也
    无从补救，只能等回到电脑前开 Web。补采入口写在失败现场，才是一条能立刻行动的通知。
    """
    headline = collect_failure_headline(reason)
    if not headline:
        return ""
    return f"⚠️ 今日晨间采集未成功：{headline}（下面的岗位可能不是最新的）；修好后发送 /collect 可重跑一次，详情见 Web 采集面板"
