"""采集结果的区域白名单过滤（source-agnostic 纯函数）。

为什么需要：`opencli` 只能按城市搜（`--city 青岛`），一次回来的 89 条里大半落在你根本
不考虑的区（黄岛、城阳、李沧）。区域是硬口径，不是判断题——在进候选之前挡掉，比事后
一条条改状态便宜得多。

边界（CLAUDE.md）：
- §8 来源解耦：本模块只看规范化后的 `city`/`area` 字段，不认识任何来源，禁止 `if source ==`。
- §7 不静默丢数据：过滤掉的条数与样例进 `report`，由调用方写进 `SourceRun.raw_config`，
  Web 采集面板与手机回执都能看到「过滤了多少」。
- 只作用于**采集器**路径（`collect_ops`）；CSV 导入、手动单条、Telegram 截图 ingest 都是
  本人主动挑的输入，不过滤。
"""

from __future__ import annotations

from typing import Any

# 「市南」「市南区」「青岛市」要能互相认出来：比对前把这些行政区后缀削掉。
_AREA_SUFFIXES = ("新区", "区", "县", "市", "镇", "街道")


def normalize_area(value: Any) -> str:
    """区域名归一：去空白 + 削掉行政区后缀。空值返回空串。

    `新区` 排在 `区` 前面，否则「黄岛新区」会先被削成「黄岛新」。只削一次——
    「市南区」→「市南」，不会继续把「市南」削成「市」。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    for suffix in _AREA_SUFFIXES:
        if len(text) > len(suffix) and text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def record_area(record: dict) -> str:
    """记录的「区」；解析不出来返回空串（= 区域未知）。

    `normalizer.parse_city_area` 对单段输入（只有「青岛」）会把 city 和 area 填成同一个值，
    那不是区，是城市——必须当成未知，否则白名单会把它误判成「区域不符」或误放行。
    """
    city = normalize_area(record.get("city"))
    area = normalize_area(record.get("area"))
    if not area or area == city:
        return ""
    return area


def area_allowed(record: dict, cfg: dict) -> tuple[bool, str]:
    """这条记录是否放行，返回 (放行?, 原因)。原因用于报告里区分「区域不符」和「区域未知」。

    `cities` 为空 = 不限城市；`areas` 为空 = 不限区（等于没开过滤）。

    区域未知时看 `keep_unknown_area`，**默认放行**：「区域未知」不等于「区域不符」，
    公众号/beBee 的岗位常常压根没有区，默认挡掉等于把这两个来源整批吃掉（只在报告里
    留个数字，人不会去看）。宁可多看一条再人工跳过，也不要静默少一条。
    """
    cities = [normalize_area(item) for item in cfg.get("cities") or [] if str(item or "").strip()]
    areas = [normalize_area(item) for item in cfg.get("areas") or [] if str(item or "").strip()]

    if cities:
        city = normalize_area(record.get("city"))
        if city and city not in cities:
            return False, "city"

    if not areas:
        return True, ""

    area = record_area(record)
    if not area:
        return (True, "") if cfg.get("keep_unknown_area", True) else (False, "unknown")
    return (True, "") if area in areas else (False, "area")


def apply_area_filter(records: list[dict], cfg: dict | None) -> tuple[list[dict], dict]:
    """按白名单过滤一批采集记录，返回 (保留的记录, 报告)。

    报告结构固定，直接并进 `SourceRun.raw_config`：
    `{enabled, kept, filtered, unknown_area, samples}`——`samples` 只留前 5 条
    「标题 · 城市 · 区」，够在 Web 面板上认出被挡掉的是哪类岗位，又不会把整批 dump 进库。

    未启用（`enabled=false` 或没配 `cities`/`areas`）时原样返回，报告 `enabled=False`。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    enabled = bool(cfg.get("enabled")) and bool(cfg.get("cities") or cfg.get("areas"))
    if not enabled:
        return list(records), {"enabled": False, "kept": len(records), "filtered": 0, "unknown_area": 0, "samples": []}

    kept: list[dict] = []
    samples: list[str] = []
    unknown = 0
    for record in records:
        allowed, reason = area_allowed(record, cfg)
        if allowed:
            kept.append(record)
            continue
        if reason == "unknown":
            unknown += 1
        if len(samples) < 5:
            location = " · ".join(filter(None, [str(record.get("city") or ""), str(record.get("area") or "")]))
            samples.append(f"{record.get('title') or '未命名岗位'} · {location or '区域未知'}")
    return kept, {
        "enabled": True,
        "kept": len(kept),
        "filtered": len(records) - len(kept),
        "unknown_area": unknown,
        "samples": samples,
    }
