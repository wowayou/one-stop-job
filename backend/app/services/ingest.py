"""统一 ingest 分派器：一段文本/截图 → 抽链 → 分派采集器或 freeform LLM → **候选列表**。

设计要点（见 CLAUDE.md）：
- Telegram / HTTP 只是触发方式；真正的 `Job.source` 仍由各采集器决定（§8）。
- **默认不入库**。本模块只产出规范化候选 dict；是否写入 Job 表由用户在聊天里点「入库」决定。
- 原文/截图由调用方写入聊天记录保留；本模块不删任何原料。
- 仍只走 normalizer 产出规范化 dict，commit 时才走 importer（§6）。
"""

from __future__ import annotations

import logging
import re

from sqlmodel import Session, select

from ..models import ChatMessage, ChatThread, FitScore, Job, UserProfile
from . import bebee, wechat
from .collectors import BeBeeCollector, WeChatPasteCollector
from .jobs import company_map, research_items_map
from .normalizer import UNKNOWN_COMPANIES, UNKNOWN_TITLES, normalize_record
from .scoring import score_job

logger = logging.getLogger(__name__)

# BOSS 直聘 / 智联：识别到但受风控无法直接抓取的招聘站域名（CLAUDE.md §3.3 红线：不破解风控）。
# 这里只做“识别 + 提示用户改发文本/截图”，绝不为它们新建采集器或发起请求。
_KNOWN_UNCRAWLABLE_LINK_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:zhipin|zhaopin)\.com(?:/[^\s\)\]\}\"'<>，。、；;）】]*)?",
    re.IGNORECASE,
)
_TRAILING_PUNCT = "）)】」』,，。.;；、!！?？\"'>》"


def extract_known_uncrawlable_links(blob: str) -> list[str]:
    """识别 BOSS(zhipin.com)/智联(zhaopin.com)链接：只用于回执提示，不抓取、不去重跨来源。"""
    if not blob:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in _KNOWN_UNCRAWLABLE_LINK_RE.findall(blob):
        cleaned = raw.rstrip(_TRAILING_PUNCT)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def classify_links(text: str) -> dict[str, list[str]]:
    """把一段文本里的链接按来源归类。

    返回 {"wechat": [...], "bebee": [...], "known_uncrawlable": [...]}；
    `known_uncrawlable` 是识别到但受风控无法直接抓取的招聘站链接（如 BOSS/智联），仅用于回执提示。
    """
    return {
        "wechat": wechat.extract_mp_links(text),
        "bebee": bebee.extract_bebee_links(text),
        "known_uncrawlable": extract_known_uncrawlable_links(text),
    }


def score_job_ids(session: Session, job_ids: list[int], profile: UserProfile) -> int:
    """给已入库岗位评分（commit 路径用）。单条失败仅记日志，返回成功数。"""
    if not job_ids:
        return 0
    jobs = [session.get(Job, jid) for jid in job_ids]
    jobs = [job for job in jobs if job is not None]
    company_ids = [job.company_id for job in jobs if job.company_id]
    companies = company_map(session, company_ids)
    research_by_company = research_items_map(session, company_ids)

    scored = 0
    for job in jobs:
        try:
            company = companies.get(job.company_id or 0)
            research = research_by_company.get(job.company_id or 0, [])
            result = score_job(job, company, research, profile)
            session.add(
                FitScore(
                    job_id=job.id or 0,
                    total=result.total,
                    hard_blocked=result.hard_blocked,
                    details=result.details,
                )
            )
            scored += 1
        except Exception:  # noqa: BLE001
            logger.warning("ingest 评分失败 job_id=%s", job.id, exc_info=True)
    session.commit()
    return scored


def candidate_match_key(candidate: dict) -> str | None:
    """候选去重匹配 key：优先复用 canonical_key；缺失时退化为「标题+公司」归一化字符串。

    与 `canonical_job_key` 一致地过滤掉未知占位符（"未命名岗位"/"未知公司" 等），
    避免大量抽取失败的候选因为都是占位符而被误判成同一条。
    """
    key = candidate.get("canonical_key")
    if key:
        return str(key)
    title = re.sub(r"\s+", "", str(candidate.get("title") or "").strip().lower())
    company = re.sub(r"\s+", "", str(candidate.get("company_name") or "").strip().lower())
    if title in UNKNOWN_TITLES or company in UNKNOWN_COMPANIES:
        return None
    return f"title_company:{title}|{company}"


def recent_ingest_candidate_index(session: Session, *, limit: int = 50) -> dict[str, int]:
    """最近 `limit` 个 `kind="ingest"` 线程里出现过的候选 -> 所属线程 id。

    同一个 key 命中多个线程时保留最近更新的那个线程。只读，不涉及 Job 表，
    只是给「这批候选是不是最近已经录入过」提供一个只读比对索引。
    """
    threads = session.exec(
        select(ChatThread).where(ChatThread.kind == "ingest").order_by(ChatThread.updated_at.desc()).limit(limit)
    ).all()
    thread_order = {t.id: idx for idx, t in enumerate(threads) if t.id is not None}
    if not thread_order:
        return {}
    messages = session.exec(
        select(ChatMessage).where(ChatMessage.thread_id.in_(list(thread_order)), ChatMessage.role == "assistant")
    ).all()
    # 按线程「从近到远」的顺序处理消息，保证同一 key 命中多个线程时，索引里留下的是最近的那个。
    messages = sorted(messages, key=lambda m: thread_order.get(m.thread_id, len(thread_order)))
    index: dict[str, int] = {}
    for message in messages:
        for candidate in (message.metadata_json or {}).get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            key = candidate_match_key(candidate)
            if key and key not in index:
                index[key] = message.thread_id
    return index


def find_duplicate_thread(session: Session, candidates: list[dict], *, limit: int = 50) -> tuple[int | None, int]:
    """把本轮候选和最近 ingest 线程里的候选比对查重（只标注，不丢候选，不入库）。

    返回 `(merge_thread_id, duplicate_count)`：
    - `merge_thread_id` 非 None：本轮候选**全部**命中同一个既有线程，调用方应该把这条消息
      并入该线程而不是新建；候选上暂时写的 `duplicate_in_thread_id` 会被清掉——它们即将
      成为那个线程自己的一部分，不需要再额外标「重复候选」。
    - `merge_thread_id` 为 None 但 `duplicate_count > 0`：部分命中，调用方仍新建线程，
      每个命中的候选已经带上 `duplicate_in_thread_id`，供前端标「重复候选」并默认不勾选。
    """
    if not candidates:
        return None, 0
    index = recent_ingest_candidate_index(session, limit=limit)
    if not index:
        return None, 0

    matched_thread_ids: set[int] = set()
    all_matched = True
    duplicate_count = 0
    for candidate in candidates:
        key = candidate_match_key(candidate)
        thread_id = index.get(key) if key else None
        if thread_id is not None:
            candidate["duplicate_in_thread_id"] = thread_id
            matched_thread_ids.add(thread_id)
            duplicate_count += 1
        else:
            all_matched = False

    if all_matched and len(matched_thread_ids) == 1:
        merge_thread_id = next(iter(matched_thread_ids))
        for candidate in candidates:
            candidate.pop("duplicate_in_thread_id", None)
        return merge_thread_id, duplicate_count
    return None, duplicate_count


def _residual_text(text: str, classified: dict[str, list[str]]) -> str:
    """去掉已被专用采集器认领的链接后剩下的文本。"""
    residual = text or ""
    for links in classified.values():
        for link in links:
            residual = residual.replace(link, " ")
    return residual.strip()


def _collect_records(collector) -> tuple[list[dict], dict]:
    """跑采集器，返回 (规范化 records, report)。失败时 records=[] 且 report 带 error。"""
    report = getattr(collector, "report", None) or {}
    try:
        records = collector.collect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest 采集失败 source=%s", getattr(collector, "source", "?"), exc_info=True)
        report = {**report, "error": str(exc), "skipped": list(report.get("skipped", [])) + [{"url": None, "reason": str(exc)}]}
        return [], report
    return list(records or []), dict(getattr(collector, "report", report) or report)


def run_ingest(
    text: str,
    *,
    wechat_cfg: dict,
    bebee_cfg: dict,
    ai_enabled: bool,
    image_data_url: str | None = None,
    manual_source: str = "manual",
) -> dict:
    """文本（可含链接）和/或截图 → 候选岗位列表（**不写库**）。

    返回：
    {
      candidates: [normalized_dict + status=pending ...],
      sources_report: [{source, jobs, skipped, error?}],
      links_total, unmatched, needs_ai,
      ai_error: AI 调用异常时的简短原因（None 表示没失败）,
      known_uncrawlable_links / known_uncrawlable_hint: 识别到的 BOSS/智联链接与是否需要提示,
    }
    """
    text = text or ""
    classified = classify_links(text)
    links_total = sum(len(v) for v in classified.values())
    candidates: list[dict] = []
    sources_report: list[dict] = []
    seen_external: set[str] = set()
    ai_error: str | None = None

    def _append_records(source_label: str, records: list[dict], report: dict) -> None:
        added = 0
        for rec in records:
            ext = rec.get("external_id")
            if ext and ext in seen_external:
                continue
            if ext:
                seen_external.add(ext)
            candidates.append({**rec, "status": "pending", "job_id": None})
            added += 1
        sources_report.append(
            {
                "source": source_label,
                "jobs": added,
                "skipped": list(report.get("skipped", [])),
                **({"error": report["error"]} if report.get("error") else {}),
            }
        )

    wechat_links = classified["wechat"]
    if wechat_links:
        wechat_label = wechat_cfg.get("source_label", "公众号")
        fetch_cfg = wechat_cfg.get("fetch", {})
        collector = WeChatPasteCollector(
            links=wechat_links,
            bodies={},
            cfg=wechat_cfg,
            ai_enabled=ai_enabled,
            min_jobs=int(wechat_cfg.get("min_jobs_before_llm_fallback", 1)),
            rate_limit_seconds=float(fetch_cfg.get("rate_limit_seconds", 0) or 0),
            source=wechat_label,
        )
        records, report = _collect_records(collector)
        _append_records(wechat_label, records, report)

    bebee_links = classified["bebee"]
    if bebee_links:
        bebee_label = bebee_cfg.get("source_label", "beBee")
        collector = BeBeeCollector(urls=bebee_links, cfg=bebee_cfg, source=bebee_label)
        records, report = _collect_records(collector)
        _append_records(bebee_label, records, report)

    residual = _residual_text(text, classified)
    needs_ai = bool((residual or image_data_url) and not ai_enabled)
    if (residual or image_data_url) and ai_enabled:
        from .ai import describe_extraction_error, extract_jobs_freeform

        try:
            raw_jobs = extract_jobs_freeform(residual, image_data_url)
        except Exception as exc:  # noqa: BLE001 - 异常本身已在 ai.py 内不吞，这里是唯一捕获点
            logger.warning("freeform 抽取失败", exc_info=True)
            raw_jobs = []
            ai_error = describe_extraction_error(exc)
            reason = f"AI 抽取失败：{ai_error}"
            report = {"skipped": [{"url": None, "reason": reason}], "error": reason}
        else:
            report = {"skipped": []} if raw_jobs else {"skipped": [{"url": None, "reason": "AI 未从文本/截图中认出岗位"}]}
        records = []
        for raw in raw_jobs or []:
            if not str(raw.get("title") or "").strip():
                continue
            records.append(normalize_record(raw, source=manual_source))
        _append_records(manual_source, records, report)

    known_uncrawlable_links = classified.get("known_uncrawlable") or []
    # “本次无其它可抓链接”＝这一轮没有识别到 wechat/bebee 专用采集器能处理的链接；
    # 与 AI 是否成功抽取到候选无关——即便正文里另有 JD 文本被 AI 认出，BOSS/智联链接本身依旧没法抓，提示仍然有意义。
    known_uncrawlable_hint = bool(known_uncrawlable_links) and not classified["wechat"] and not classified["bebee"]

    return {
        "candidates": candidates,
        "sources_report": sources_report,
        "links_total": links_total,
        "candidate_count": len(candidates),
        "unmatched": len(candidates) == 0,
        "needs_ai": needs_ai and len(candidates) == 0,
        "ai_error": ai_error,
        "known_uncrawlable_links": known_uncrawlable_links,
        "known_uncrawlable_hint": known_uncrawlable_hint,
    }
