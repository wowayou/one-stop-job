"""采集触发路由（Phase R · R2）。

/api/collect/* 与 /api/sources：各来源采集器的触发端点与状态查询。从 main.py 原样搬出，
行为逐字不变；依赖仅来自 deps/models/schemas/services/config，不 import main。

红线：这里调用的 `run_collector`/`run_source`/`run_wechat_collection`（均在
`services/collect_ops.py`）会走 `upsert_job_records`——这是用户主动触发的采集入库
（红线 §2 允许），不是 ingest 的自动入库。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..config import get_settings, load_yaml_config, save_yaml_config
from ..deps import SessionDep
from ..models import SourceRun
from ..schemas import AutomationSettingsUpdate, WeChatCollectRequest
from ..services.collect_ops import run_source, run_wechat_collection, source_status_payload
from ..services.automation import automation_mode, rescore_all_jobs, rescore_pending_candidates
from ..services.chat_ingest import recent_collect_candidates
from ..services.sources import get_source_definition, list_source_definitions
from ..services.wechat import extract_mp_links

router = APIRouter()


@router.get("/api/automation/status")
async def automation_status(session: SessionDep) -> dict:
    settings = get_settings()
    boss_source = get_source_definition(settings, "boss")
    latest = (
        session.exec(
            select(SourceRun).where(SourceRun.source == boss_source.label).order_by(SourceRun.started_at.desc())
        ).first()
        if boss_source
        else None
    )
    reach = settings.reach_config
    pending = [item for item in recent_collect_candidates(session) if str(item.get("status") or "pending") == "pending" and not item.get("hard_blocked")]
    raw = latest.raw_config if latest and isinstance(latest.raw_config, dict) else {}
    gate = raw.get("score_gate") if isinstance(raw.get("score_gate"), dict) else {}
    return {
        "mode": automation_mode(),
        "reach_level": str(reach.get("level") or "core"),
        "rescore_existing": bool(reach.get("rescore_existing", True)),
        "latest_run": latest.model_dump() if latest else None,
        "latest_counts": {
            "found": latest.fetched_count if latest else 0,
            "hard_blocked": int(gate.get("hard_blocked") or 0),
            "pending": len(pending),
            "materials_prepared": int(raw.get("materials_prepared") or 0),
        },
        "safe_boundary": "自动驾驶只生成本地候选与材料；不自动投递、不联系招聘方。",
    }


@router.put("/api/automation/settings")
async def automation_settings(payload: AutomationSettingsUpdate, session: SessionDep) -> dict:
    config = load_yaml_config()
    automation = config.get("automation") if isinstance(config.get("automation"), dict) else {}
    reach = config.get("reach") if isinstance(config.get("reach"), dict) else {}
    config["automation"] = {**automation, "mode": payload.mode}
    config["reach"] = {**reach, "level": payload.reach_level, "rescore_existing": payload.rescore_existing}
    save_yaml_config(config)
    get_settings.cache_clear()
    rescored = {"jobs": 0, "candidates": 0}
    if payload.rescore_existing:
        rescored = {"jobs": rescore_all_jobs(session), "candidates": rescore_pending_candidates(session)}
    return {**(await automation_status(session)), "rescored": rescored}


@router.post("/api/automation/scan")
async def automation_scan(session: SessionDep) -> dict:
    return run_source(session, "boss")


@router.post("/api/automation/rescore")
async def automation_rescore(session: SessionDep) -> dict:
    return {"jobs": rescore_all_jobs(session), "candidates": rescore_pending_candidates(session)}


@router.get("/api/collect/runs")
async def list_collect_runs(session: SessionDep) -> list[SourceRun]:
    return session.exec(select(SourceRun).order_by(SourceRun.started_at.desc())).all()


@router.get("/api/sources")
async def list_sources(session: SessionDep) -> list[dict]:
    return [source_status_payload(session, source) for source in list_source_definitions(get_settings())]


@router.post("/api/collect/runs")
async def run_collection(session: SessionDep, source: str = "boss") -> dict:
    source_key = source.lower().strip()
    if source_key == "boss":
        return run_source(session, "boss")
    raise HTTPException(status_code=400, detail="请使用 /api/sources/{source_key}/collect 运行配置化采集来源")


@router.post("/api/sources/{source_key}/collect")
async def collect_source(source_key: str, session: SessionDep) -> dict:
    return run_source(session, source_key)


@router.post("/api/collect/bebee")
async def collect_bebee(session: SessionDep) -> dict:
    """beBee 渠道:抓 config.yaml bebee.role_urls 列表页 → 解析 JobPosting → 入库。"""
    return run_source(session, "bebee")


@router.post("/api/collect/wechat")
async def collect_wechat(payload: WeChatCollectRequest, session: SessionDep) -> dict:
    """公众号渠道：粘贴元宝回答 / mp.weixin 链接 / 文章正文 → 抓取解析 → 入库。"""
    wechat_cfg = get_settings().wechat_config
    source_label = wechat_cfg.get("source_label", "公众号")

    # 汇总并去重链接（text 与 urls 都过一遍正则抽链）
    links: list[str] = []
    seen: set[str] = set()
    for blob in [payload.text, *(payload.urls or [])]:
        if not blob:
            continue
        for link in extract_mp_links(blob):
            if link not in seen:
                seen.add(link)
                links.append(link)
    bodies = payload.bodies or {}
    for key in bodies:  # 手动粘正文的 key 应为 mp.weixin 链接，确保它进入处理队列
        for link in extract_mp_links(key):
            if link not in seen:
                seen.add(link)
                links.append(link)

    if not links and not bodies:
        raise HTTPException(
            status_code=400,
            detail="未识别到 mp.weixin.qq.com 链接；请粘贴元宝回答/文章链接，或改用手动粘贴文章正文",
        )

    return run_wechat_collection(session, links, bodies, source_label)


@router.post("/api/collect/yuanbao")
async def collect_yuanbao(session: SessionDep, prompt: str | None = None) -> dict:
    """可选：用 Playwright 自动驱动元宝网页抓链接，再走同一抓取/解析管线。

    默认关闭，需在 config.yaml 设 wechat.yuanbao_automation.enabled=true 并安装
    requirements-automation.txt（playwright）。
    """
    wechat_cfg = get_settings().wechat_config
    source_label = wechat_cfg.get("source_label", "公众号")
    auto_cfg = wechat_cfg.get("yuanbao_automation", {})
    if not auto_cfg.get("enabled"):
        raise HTTPException(
            status_code=403,
            detail="元宝自动化未启用（config.yaml wechat.yuanbao_automation.enabled=false）",
        )

    try:
        from ..services.yuanbao import collect_yuanbao_links

        links = collect_yuanbao_links(auto_cfg, prompt or auto_cfg.get("prompt_template", ""))
    except Exception as exc:  # 缺 playwright / 登录失效 / 选择器变动
        raise HTTPException(status_code=502, detail=f"元宝自动化失败：{exc}")

    if not links:
        raise HTTPException(status_code=502, detail="元宝未返回任何 mp.weixin 链接（可能需重新扫码登录或调整选择器）")

    return run_wechat_collection(session, links, {}, source_label)
