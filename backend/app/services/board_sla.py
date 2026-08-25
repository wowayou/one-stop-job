"""看板 SLA 解析：从个人上下文仓库的 Obsidian 看板里解析「下一步」中的到期动作。

只读（红线 §3.10）：本模块只消费 ContextRepository 读出的看板文本，不写看板、
不改岗位状态；产出的清单仅用于给机主本人的提醒（红线 §2 机主回执豁免）。
收口/跟进动作本身仍由本人在平台与 Obsidian 里完成，系统只负责「从不遗忘」。

看板结构约定（与个人仓库 toolkit/23-job-pipeline.md 一致）：
- `## 列名` 分列；活跃列 = 待沟通 / 已投递 / 已沟通 / 面试（R1/R2/R3）。
- 卡片行以 `- [ ] ` 开头；`- [x]` 表示已收口，不再提醒。
- 行内「下一步：」之后的文本才是动作区；日期只从动作区提取，避免把薪资
  （10-15K）、门牌号等误读成日期。
- 卡片行首段是公司名（`公司 - 岗位 - 薪资 - 渠道/日期 - 判断 - 下一步：…`），
  `parse_board_companies` 复用同一套列/卡片识别，供日清单与新岗位对账。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterator

# 活跃列：其余列（收集箱/需调研/已结束/归档/模板库/使用规则等）不产生到期动作。
_ACTIVE_COLUMNS = {"待沟通", "已投递", "已沟通"}
# 已结束/归档列：这两列出现过的公司视为本人已收口，不再当新机会推送。
# `offer` 不算收口列（那是活着的机会），也不在活跃列里，故不参与对账。
_CLOSED_COLUMN_PREFIXES = ("已结束", "归档")

# 动作区内的日期写法：`0813`（MMDD 紧凑）、`2026-08-13`、`8 月 13 日`。
_DATE_COMPACT = re.compile(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
_DATE_ISO = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_CN = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?")

_KIND_CLOSE = re.compile(r"结束|关闭|退出|收口|归档")
_KIND_SEND = re.compile(r"发出|发送|首次|投递|建联")
_KIND_FOLLOW = re.compile(r"跟进|确认|电话|约面|推动|问")

_NEXT_STEP_MARKERS = ("下一步：", "下一步:")


@dataclass(frozen=True)
class BoardAction:
    """看板某张活跃卡上的一个有日期动作。"""

    column: str  # 所在看板列
    card: str  # 卡片摘要（行首「公司 - 岗位 - 薪资」）
    due: date  # 动作日期
    kind: str  # send / follow / close / other
    snippet: str  # 日期后的动作原文片段（截断）


def _is_active_column(heading: str) -> bool:
    return heading in _ACTIVE_COLUMNS or heading.startswith("面试")


def _is_closed_column(heading: str) -> bool:
    """已结束（拒 / 放弃）/ 归档（长期不跟）——列名带括号说明，用前缀匹配。"""
    return heading.startswith(_CLOSED_COLUMN_PREFIXES)


def _iter_card_rows(content: str) -> Iterator[tuple[str, str, bool]]:
    """遍历看板卡片行，产出 (列名, 行原文, 是否已勾选)。

    单一解析入口：到期动作与公司对账都从这里取行，避免出现第二套列/卡片识别。
    过滤掉模板行与 `暂无` 占位（它们不是真岗位卡）。
    """
    column: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            column = line[3:].strip()
            continue
        if column is None:
            continue
        stripped = line.lstrip()
        checked = stripped.startswith(("- [x]", "- [X]"))
        if not checked and not stripped.startswith("- [ ]"):
            continue
        if "_template.md" in stripped or "暂无" in stripped or "新岗位线索" in stripped:
            continue
        yield column, stripped, checked


def _infer_year(month: int, day: int, today: date) -> date | None:
    """无年份日期取距 today 最近的那一年；非法日期（2 月 30 等）返回 None。"""
    candidates: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda item: (abs((item - today).days), item))


def _classify(snippet: str) -> str:
    if _KIND_CLOSE.search(snippet):
        return "close"
    if _KIND_SEND.search(snippet):
        return "send"
    if _KIND_FOLLOW.search(snippet):
        return "follow"
    return "other"


def _card_summary(line: str) -> str:
    body = line.lstrip()
    for prefix in ("- [ ]", "- [x]", "- [X]"):
        if body.startswith(prefix):
            body = body[len(prefix) :].strip()
            break
    segments = [segment.strip() for segment in body.split(" - ")]
    return " - ".join(segments[:3]) if segments else body[:60]


def _next_step_text(line: str) -> str | None:
    for marker in _NEXT_STEP_MARKERS:
        if marker in line:
            text = line.split(marker, 1)[1]
            # 动作区到「详情/模板/JD」链接为止：链接里的数字不是动作日期。
            # `[详情]`/`[模板]` 是路径里的 yyyy-mm-dd 文件名；`[JD]`（board_write 写入收集箱行
            # 时附的原链接）是 BOSS 的 job_detail id，形如 `...0nJ93t29FV`，其中的 4 位数字段
            # 会被 `_DATE_COMPACT` 读成 MMDD，凭空造出一条到期动作。
            for link_marker in ("[详情]", "[模板]", "[JD]"):
                if link_marker in text:
                    text = text.split(link_marker, 1)[0]
            return text.replace("**", "")
    return None


def _date_matches(text: str, today: date) -> list[tuple[int, int, date]]:
    """动作区里的 (起点, 终点, 日期)。三种写法合并后按出现位置排序、同位置去重。"""
    found: dict[int, tuple[int, int, date]] = {}
    for match in _DATE_ISO.finditer(text):
        year, month, day = (int(part) for part in match.groups())
        try:
            found[match.start()] = (match.start(), match.end(), date(year, month, day))
        except ValueError:
            continue
    for match in _DATE_CN.finditer(text):
        if match.start() in found:
            continue
        inferred = _infer_year(int(match.group(1)), int(match.group(2)), today)
        if inferred is not None:
            found[match.start()] = (match.start(), match.end(), inferred)
    for match in _DATE_COMPACT.finditer(text):
        # 紧凑写法最容易撞上其它数字（门牌/编号），已被 ISO/中文吃掉的位置不再重复。
        if any(start <= match.start() < end for start, end, _ in found.values()):
            continue
        inferred = _infer_year(int(match.group(1)), int(match.group(2)), today)
        if inferred is not None:
            found[match.start()] = (match.start(), match.end(), inferred)
    return sorted(found.values(), key=lambda item: item[0])


def _trim_snippet(text: str, limit: int) -> str:
    """截断动作片段：优先在句读（。；！？)处收尾，否则硬切并补省略号，避免读起来像断句。"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for punct in "。；！？":
        pos = head.rfind(punct)
        if pos >= limit // 2:
            return head[: pos + 1]
    return head + "…"


def parse_board_actions(content: str, today: date, *, snippet_length: int = 100) -> list[BoardAction]:
    """解析看板全文，产出活跃列中所有带日期的下一步动作。纯函数，不做 IO。"""
    actions: list[BoardAction] = []
    for column, stripped, checked in _iter_card_rows(content):
        if checked or not _is_active_column(column):
            continue  # `- [x]` 已收口；非活跃列不产生到期动作。
        next_step = _next_step_text(stripped)
        if next_step is None:
            continue
        matches = _date_matches(next_step, today)
        card = _card_summary(stripped)
        for index, (_, end, due) in enumerate(matches):
            boundary = matches[index + 1][0] if index + 1 < len(matches) else len(next_step)
            snippet = next_step[end:boundary].strip(" ，。;；-—*[]") or next_step.strip()
            snippet = _trim_snippet(snippet, snippet_length)
            actions.append(BoardAction(column=column, card=card, due=due, kind=_classify(snippet), snippet=snippet))
    return actions


# ---------------------------------------------------------------------------
# 公司对账（供日清单新岗位段用；只读，同一套列/卡片识别）
# ---------------------------------------------------------------------------

# 去前后缀后再比较：看板写简称（"发多维"），岗位库写全称（"青岛发多维化妆品"）。
# 只削首尾，绝不削中间，避免把"青岛七联洲际贸易"削成不可辨识的碎片。
_COMPANY_PREFIXES = ("青岛", "山东", "济南", "烟台", "潍坊", "威海", "淄博", "北京", "上海", "深圳", "广州", "成都", "杭州", "江苏", "浙江")
_COMPANY_SUFFIXES = (  # 长度降序：先削最长的，避免"信息技术"被"技术"截半
    "股份有限公司", "有限责任公司",
    "有限公司", "信息技术", "电子商务", "国际贸易",
    "分公司", "进出口", "工作室",
    "集团", "股份", "公司", "科技", "技术", "网络", "外贸", "贸易",
    "实业", "工贸", "制造", "工业", "产业", "企业", "服务", "中心",
)
# 卡片首段最长可信公司名长度：超过则视为该行没按「公司 - 岗位」写，放弃对账
# （宁漏配不误杀：拿一整行去做包含匹配会命中一堆无关公司）。
_MAX_COMPANY_LENGTH = 24
# 包含式匹配的最短长度：短名（如"海尔"）只允许全等，否则会误杀"海尔斯"这类不同公司。
_MIN_CONTAINS_LENGTH = 3

_COMPANY_NOISE = re.compile(r"[\s　*_`【】\[\]()（）]+")
# 首段里的公司别名分隔：`青岛恩能机械 / 恩能电机`、`灸石科技（Snappyit）`、顿号并列。
_COMPANY_ALIAS_SPLIT = re.compile(r"[/／、,，|]|（|）|\(|\)")


def normalize_company(name: str) -> str:
    """公司名归一：去空白/标点噪音 → 去地域前缀 → 反复去行业与法人后缀。

    只做确定性的前后缀削减，**不做模糊相似度**：漏配只是日清单多显示一条，
    误杀会让真机会消失（规格「宁漏配不误杀」）。
    """
    core = _COMPANY_NOISE.sub("", name or "")
    for prefix in _COMPANY_PREFIXES:
        if core.startswith(prefix) and len(core) > len(prefix) + 1:
            core = core[len(prefix) :]
            break
    changed = True
    while changed:
        changed = False
        for suffix in _COMPANY_SUFFIXES:  # 已按长度降序排列，先削最长的
            if core.endswith(suffix) and len(core) > len(suffix) + 1:
                core = core[: -len(suffix)]
                changed = True
                break
    return core


def companies_match(left: str, right: str) -> bool:
    """两个**已归一**的公司名是否指同一家：全等，或短的一方被长的一方包含。

    包含式是双向的（"发多维" ⊂ "发多维化妆品"，反向同理），但短名必须
    ≥ `_MIN_CONTAINS_LENGTH` 才允许包含匹配。
    """
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= _MIN_CONTAINS_LENGTH and short in long


def _company_aliases(card_line: str) -> list[str]:
    """卡片行 → 首段里的公司别名（未归一）。首段 = 第一个 ` - ` 之前。"""
    body = card_line.lstrip()
    for prefix in ("- [ ]", "- [x]", "- [X]"):
        if body.startswith(prefix):
            body = body[len(prefix) :].strip()
            break
    head = body.split(" - ", 1)[0].strip().strip("*")
    if not head or len(head) > _MAX_COMPANY_LENGTH:
        return []
    return [part.strip() for part in _COMPANY_ALIAS_SPLIT.split(head) if part.strip()]


@dataclass(frozen=True)
class BoardCompanyIndex:
    """看板上出现过的公司（已归一），按「已收口」与「活跃」分桶。"""

    closed: tuple[str, ...]  # 已结束/归档列
    active: tuple[str, ...]  # 待沟通/已投递/已沟通/面试

    def match(self, company_name: str) -> str | None:
        """岗位库公司名 → "active" / "closed" / None。

        活跃优先：同一家公司既有已收口的旧岗又在活跃列里（如乐达信息产业），
        按活跃处理（标注而非剔除），否则会把正在推进的机会藏起来。
        """
        core = normalize_company(company_name)
        if not core:
            return None
        if any(companies_match(core, known) for known in self.active):
            return "active"
        if any(companies_match(core, known) for known in self.closed):
            return "closed"
        return None


def parse_board_companies(content: str) -> BoardCompanyIndex:
    """解析看板全文 → 公司对账索引。纯函数，不做 IO。

    与 `parse_board_actions` 共用 `_iter_card_rows`；这里**不看勾选状态**：
    已结束列的卡是 `- [x]`，归档列两种都有，公司在不在看板上与勾选无关。
    """
    closed: list[str] = []
    active: list[str] = []
    for column, stripped, _checked in _iter_card_rows(content):
        if _is_closed_column(column):
            bucket = closed
        elif _is_active_column(column):
            bucket = active
        else:
            continue  # 收集箱/需调研/offer/模板库/使用规则不参与对账
        for alias in _company_aliases(stripped):
            core = normalize_company(alias)
            if core and core not in bucket:
                bucket.append(core)
    return BoardCompanyIndex(closed=tuple(closed), active=tuple(active))


def split_due(actions: list[BoardAction], today: date) -> dict[str, list[dict]]:
    """按到期与类型分组：due_* 为今天及逾期；upcoming 为未来 7 天内。"""
    sections: dict[str, list[dict]] = {"due_send": [], "due_follow": [], "due_close": [], "due_other": [], "upcoming": []}
    for action in sorted(actions, key=lambda item: item.due):
        payload = {**asdict(action), "due": action.due.isoformat(), "overdue_days": (today - action.due).days}
        if action.due <= today:
            sections[f"due_{action.kind}" if action.kind != "other" else "due_other"].append(payload)
        elif (action.due - today).days <= 7:
            sections["upcoming"].append(payload)
    return sections


def format_digest(sections: dict[str, list[dict]], stale_jobs: list[dict], today: date) -> str:
    """拼装给机主本人的晨间摘要文本（Telegram 纯文本，长度截断由发送端负责）。"""
    lines: list[str] = [f"📋 {today.isoformat()} 求职日清单"]

    def _block(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.append("")
        lines.append(title)
        for item in items:
            overdue = item.get("overdue_days", 0)
            suffix = f"（逾期 {overdue} 天）" if overdue > 0 else ""
            lines.append(f"· {item['card']}{suffix}")
            if item.get("snippet"):
                lines.append(f"  ↳ {item['due'][5:]} {item['snippet']}")

    _block("📨 今日必发（首次触达）", sections.get("due_send", []))
    _block("🔁 今日跟进", sections.get("due_follow", []))
    _block("✅ 今日收口", sections.get("due_close", []))
    _block("📌 其他到期动作", sections.get("due_other", []))

    if stale_jobs:
        lines.append("")
        lines.append("⏰ 库内需跟进（久无进展）")
        for job in stale_jobs:
            lines.append(f"· {job.get('company_name') or '?'} - {job.get('title') or '?'}：{job.get('reason', '')}")

    if len(lines) == 1:
        lines.append("今天看板上没有到期动作；按规则继续补新鲜岗位首次触达。")
    return "\n".join(lines)
