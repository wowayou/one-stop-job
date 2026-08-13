"""晨间日清单组装：看板到期动作 + 库内 stale 岗位 → 结构化 payload / 摘要文本。

被 `routers/followups.py` 的 board-sla 端点与 `main.py` 的每日推送循环共用，
避免两处组装逻辑漂移。只读（板块解析见 services/board_sla.py 的红线说明）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from ..config import get_settings
from ..models import Job, utc_now
from .board_sla import BoardCompanyIndex, format_digest, parse_board_actions, parse_board_companies, split_due
from .context_repository import ContextRepository
from .followup import find_stale_jobs

logger = logging.getLogger(__name__)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def score_new_jobs(session: Session, *, hours: int = 26) -> list[dict]:
    """近 hours 小时新入库、仍为 new 状态的岗位，**全部**按分数降序返回（不截断）。

    只读展示，不改岗位状态。26 小时窗口略大于 24，避免每日采集时刻的小幅漂移漏掉岗位。

    评分复用既有最新 FitScore，只给**尚无评分**的岗位调用 `score_job_into_db`：
    该函数每次调用都追加一行（历史流水语义，所有消费端只读 `latest_score_map`），
    若每次生成摘要都重评一遍，`fit_scores` 会随每次请求线性膨胀。
    """
    from .jobs import latest_score_map
    from .queries import get_profile, score_job_into_db

    cutoff = _naive_utc(utc_now()) - timedelta(hours=hours)
    jobs = session.exec(select(Job).where(Job.status == "new").order_by(Job.created_at.desc()).limit(300)).all()
    fresh = [job for job in jobs if (_naive_utc(job.created_at) or cutoff) >= cutoff]
    if not fresh:
        return []
    existing = latest_score_map(session, [job.id for job in fresh if job.id is not None])
    profile = get_profile(session)
    scored: list[dict] = []
    for job in fresh:
        known = existing.get(job.id or 0)
        if known is not None:
            total = float(known.total)
        else:
            try:
                total = float(score_job_into_db(session, job, profile).total)
            except Exception:  # noqa: BLE001 - 单岗位评分失败不影响摘要
                total = 0.0
        scored.append(
            {
                "title": job.title,
                "company_name": job.company_name,
                "salary": job.salary_text,
                "area": " · ".join(filter(None, [job.city, job.area])),
                "score": round(total, 1),
                "url": job.url,
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


def collect_new_jobs(session: Session, *, hours: int = 26, top_n: int = 8) -> list[dict]:
    """不与看板对账的新岗位前 top_n（保留给不做对账的调用方与测试）。"""
    return score_new_jobs(session, hours=hours)[:top_n]


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


def format_new_jobs(new_jobs: list[dict], filtered_closed: int = 0) -> str:
    """新岗位段文本；无岗位且无过滤时返回空串（不在摘要里占位）。"""
    if not new_jobs and not filtered_closed:
        return ""
    lines = ["", "🆕 新入库岗位（评分前列）"]
    for item in new_jobs:
        parts = [item["title"], item["company_name"], item.get("salary") or "薪资未知"]
        if item.get("area"):
            parts.append(item["area"])
        marker = "｜看板已有" if item.get("board_state") == "active" else ""
        lines.append(f"· {' - '.join(parts)}（{item['score']} 分{marker}）")
        if item.get("url"):
            lines.append(f"  {item['url']}")
    if filtered_closed:
        lines.append(f"（已过滤 {filtered_closed} 条已收口公司的岗位）")
    return "\n".join(lines)


def build_daily_digest(session: Session, today: date, *, include_new_jobs: bool = True) -> dict:
    """组装日清单。看板不可读时抛 ContextRepositoryError，由调用方决定报错方式。

    新岗位段与看板对账（只读）：已结束/归档列的公司剔除并计入 `filtered_closed`，
    活跃列的公司保留并标注「看板已有」。
    """
    settings = get_settings()
    repository = ContextRepository(settings.context_repo_path)
    document = repository.read_document("board")
    sections = split_due(parse_board_actions(document.content, today), today)
    stale = find_stale_jobs(session, now=utc_now(), stale_days=settings.followup_stale_days)
    new_jobs: list[dict] = []
    filtered_closed = 0
    if include_new_jobs:
        new_jobs, filtered_closed = reconcile_new_jobs(
            score_new_jobs(session), board_company_index(document.content)
        )
    text = format_digest(sections, stale, today)
    new_jobs_text = format_new_jobs(new_jobs, filtered_closed)
    if new_jobs_text:
        text = f"{text}\n{new_jobs_text}"
    return {
        "date": today.isoformat(),
        "board_updated": document.updated,
        **sections,
        "stale_jobs": stale,
        "new_jobs": new_jobs,
        "filtered_closed": filtered_closed,
        "digest_text": text,
    }


def should_send_now(now: datetime, last_sent_day: str | None, hour: int, minute: int) -> bool:
    """今天的发送时点已过、且今天还没发过 → 该发。

    覆盖两种场景：机器一直开着，到点即发；发送时点时机器关机/休眠，开机后由
    轮询在下一个检查周期补发（而不是丢掉今天的清单等到明天）。
    """
    fire_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= fire_at and last_sent_day != now.date().isoformat()


def read_last_sent(state_path: Path) -> str | None:
    """读取最近一次成功发送的日期（YYYY-MM-DD）；文件缺失或损坏按从未发过处理。"""
    try:
        value = json.loads(state_path.read_text(encoding="utf-8")).get("last_sent")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def write_last_sent(state_path: Path, day: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_sent": day}), encoding="utf-8")
