"""采集结果的两道过滤（source-agnostic 纯函数）：区域白名单 + 候选闸门。

**区域白名单**（`apply_area_filter`，评分之前）：`opencli` 只能按城市搜（`--city 青岛`），
一次回来的 89 条里大半落在你根本不考虑的区（黄岛、城阳、李沧）。区域是硬口径，不是判断
题——在进候选之前挡掉，比事后一条条改状态便宜得多。

**候选闸门**（`apply_score_gate`，评分之后）：管线本该「收集 → 规则预筛 → 人工筛选」逐级
收窄，但在此之前规则预筛只挡区域，评分只用来**排序**不用来**收窄**——实测一批 58 条候选
全数列出，40 分以下 10 条（「视频优化业务助理」「线上课程优化，7小时稳拿260元」这类）和
75 分的岗位并排等你人工读。闸门把「评分已经判定不合格」的那部分挡在待筛列表之外：
命中硬性排除（dealbreakers / 城市 / 薪资）的直接不上桌，低于 `min_score` 的同样挡掉，
剩下的再按 `max_pending` 截断——人工注意力是本管线里最贵的资源。

两道过滤都遵守同一组边界（CLAUDE.md）：
- §8 来源解耦：只看规范化后的字段与评分结果，不认识任何来源，禁止 `if source ==`。
- §7 不静默丢数据：挡掉的条数与样例进 `report`，由调用方写进 `SourceRun.raw_config`，
  Web 采集面板与手机回执都能看到「挡掉了多少、挡掉的是哪类」。
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


# 候选闸门默认值：不配置时就是这两个数。
# `min_score` 取 45：实测 40 分以下清一色是方向外岗位（视频优化助理、线上课程、教育机构），
# 45-50 带才开始出现「沾边但不对路」的边缘项——那种值得你自己看一眼，所以门槛压在它下面，
# 宁可多放一条，不要静默挡掉一个真机会。`max_pending` 取 15：一屏能读完的量级。
DEFAULT_MIN_SCORE = 45.0
DEFAULT_MAX_PENDING = 15


def apply_score_gate(candidates: list[dict], cfg: dict | None) -> tuple[list[dict], dict]:
    """按评分结果收窄待筛候选，返回 (放行的候选, 报告)。

    **必须在 `attach_candidate_scores` 之后调用**（依赖候选上的 `score` / `hard_blocked`）。
    调用方传入的列表已按分降序，本函数保持该顺序，只做「挡掉」和「截断」。

    三道口径，依次收窄：
    1. `hard_blocked`（命中 dealbreakers / 城市不符 / 薪资过低）——硬性排除是你早就写下的
       底线，不是判断题，不该每次采集都再摆到眼前让你重新否决一遍；
    2. `score < min_score`——评分已经判定不合格；
    3. 剩余超过 `max_pending` 的尾部——按分截断，注意力优先给头部。

    `score is None`（单条评分异常，见 `attach_candidate_scores`）**一律放行**：分数缺失是
    我们自己的故障，不能因此静默吃掉一个可能合适的岗位。

    报告结构固定，直接并进 `SourceRun.raw_config`：
    `{enabled, kept, hard_blocked, below_score, truncated, min_score, max_pending, samples}`。
    `samples` 只留前 5 条被挡掉的「标题 · 原因」，够在 Web 面板上认出挡掉的是哪类岗位。

    未启用（`enabled=false`）时原样返回，报告 `enabled=False`——与 `apply_area_filter`
    同一套降级口径。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    if not cfg.get("enabled", True):
        return list(candidates), {"enabled": False, "kept": len(candidates), "hard_blocked": 0, "below_score": 0, "truncated": 0}

    try:
        min_score = float(cfg.get("min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        min_score = DEFAULT_MIN_SCORE
    try:
        max_pending = int(cfg.get("max_pending", DEFAULT_MAX_PENDING))
    except (TypeError, ValueError):
        max_pending = DEFAULT_MAX_PENDING

    kept: list[dict] = []
    samples: list[str] = []
    hard_blocked = 0
    below_score = 0

    def note(candidate: dict, reason: str) -> None:
        if len(samples) < 5:
            samples.append(f"{candidate.get('title') or '未命名岗位'} · {reason}")

    for candidate in candidates:
        if candidate.get("hard_blocked"):
            hard_blocked += 1
            note(candidate, "命中硬性排除")
            continue
        score = candidate.get("score")
        if score is not None and float(score) < min_score:
            below_score += 1
            note(candidate, f"{score} 分低于 {min_score:g}")
            continue
        kept.append(candidate)

    truncated = max(0, len(kept) - max_pending) if max_pending > 0 else 0
    if truncated:
        for candidate in kept[max_pending:]:
            note(candidate, "超出单次待筛上限")
        kept = kept[:max_pending]

    return kept, {
        "enabled": True,
        "kept": len(kept),
        "hard_blocked": hard_blocked,
        "below_score": below_score,
        "truncated": truncated,
        "min_score": min_score,
        "max_pending": max_pending,
        "samples": samples,
    }
