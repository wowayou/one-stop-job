"""公众号渠道：从粘贴文本中抽取 mp.weixin.qq.com 链接、抓取并解析文章正文、
把一篇招聘文章拆成多个结构化岗位。

设计要点（见 plan）：
- 元宝/公众号后台/手动浏览得到的都是 mp.weixin 链接，本模块只负责"链接 → 岗位"。
- 一篇招聘文章通常含多个岗位，需拆成 list；正则拆不出时兜底产出 1 条，绝不静默丢失。
- 反爬验证页会返回 HTTP 200，靠"缺少正文/og:title 或正文含验证字样"识别，而非状态码。
- 纯函数为主，便于单测；网络抓取集中在 fetch_article，可在测试中 monkeypatch。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49"
)

# ==================== 链接抽取与归一化 ====================

MP_LINK_RE = re.compile(
    r"https?://mp\.weixin\.qq\.com/s[/?][^\s\)\]\}\"'<>，。、；;）】]+",
    re.IGNORECASE,
)
_TRAILING_PUNCT = "）)】」』,，。.;；、!！?？\"'>》"


def canonicalize_mp_url(url: str) -> str:
    """把 mp.weixin 文章链接归一化成稳定去重键。

    - /s/<token> 永久链：保留 path，丢弃 query/fragment。
    - /s?__biz=&mid=&idx=&sn= 形式：只保留这四个标识参数，丢弃 chksm/scene/key 等噪声。
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url.strip()

    path = parsed.path or ""
    if path.startswith("/s/"):
        return f"https://mp.weixin.qq.com{path}"
    if path == "/s":
        qs = parse_qs(parsed.query)
        keep = {k: qs[k][0] for k in ("__biz", "mid", "idx", "sn") if qs.get(k)}
        if keep:
            return "https://mp.weixin.qq.com/s?" + urlencode(keep)
    if path:
        return f"https://mp.weixin.qq.com{path}"
    return url.strip()


def extract_mp_links(blob: str) -> list[str]:
    """从任意粘贴文本（元宝整段回答 / JSON / 裸链接列表）提取并去重 mp.weixin 链接。"""
    if not blob:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in MP_LINK_RE.findall(blob):
        cleaned = raw.rstrip(_TRAILING_PUNCT)
        canon = canonicalize_mp_url(cleaned)
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


# ==================== 抓取与正文解析 ====================

_DELETED_MARKERS = ("此内容已被发布者删除", "该内容已被发布者删除", "该公众号已迁移", "此账号已被屏蔽")
_VERIFY_MARKERS = ("环境异常", "完成验证", "去验证", "访问过于频繁", "请输入验证码", "参数错误")
_NOISE_LINE_RE = re.compile(
    r"(点击上方|长按.{0,6}识别|扫码|二维码|关注我们|关注公众号|阅读原文|设为星标|戳.{0,4}关注|点个在看|分享到朋友圈)"
)


@dataclass
class ArticleFetch:
    url: str
    ok: bool
    og_title: str | None = None
    body_text: str = ""
    reason: str | None = None


def parse_article_html(html: str) -> tuple[str | None, str]:
    """从文章 HTML 提取 (og:title, 正文纯文本)。剥离样板噪声行。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")

    og_title: str | None = None
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        og_title = og["content"].strip()
    if not og_title:
        node = soup.find(id="activity-name") or soup.find("title")
        if node and node.get_text(strip=True):
            og_title = node.get_text(strip=True)

    content = soup.find(id="js_content")
    if content is None:
        return og_title, ""

    text = content.get_text(separator="\n", strip=True)
    lines = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or _NOISE_LINE_RE.search(s):
            continue
        lines.append(s)
    return og_title, "\n".join(lines)


def fetch_article(url: str, cfg: dict | None = None) -> ArticleFetch:
    """抓取单篇 mp.weixin 文章。失败/验证页/已删除时 ok=False 并带 reason（不抛异常）。"""
    import httpx

    cfg = cfg or {}
    headers = {
        "User-Agent": cfg.get("user_agent", DEFAULT_USER_AGENT),
        "Referer": "https://mp.weixin.qq.com/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    timeout = float(cfg.get("timeout_seconds", 20))
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
            resp = client.get(url)
            html = resp.text or ""
    except Exception as exc:  # 网络层失败
        return ArticleFetch(url=url, ok=False, reason=f"抓取失败: {exc}")

    if any(m in html for m in _DELETED_MARKERS):
        return ArticleFetch(url=url, ok=False, reason="文章已被发布者删除")

    og_title, body_text = parse_article_html(html)
    # 验证页/异常环境通常返回 200 但没有正文与 og:title
    if not body_text and not og_title:
        if any(m in html for m in _VERIFY_MARKERS):
            return ArticleFetch(url=url, ok=False, reason="触发风控验证页（请改用手动粘正文）")
        return ArticleFetch(url=url, ok=False, reason="未解析到正文（可能是验证页或图片型文章）")
    return ArticleFetch(url=url, ok=True, og_title=og_title, body_text=body_text)


# ==================== 一篇文章 → 多个岗位（正则启发式） ====================

# 岗位标题行：【岗位名】 / 招聘岗位：xxx / 1. xxx / ① xxx
HEADING_RE = re.compile(
    r"^\s*(?:"
    r"【(?P<b1>[^】]{2,40})】"
    r"|(?:招聘)?(?:岗位|职位)(?:名称)?\s*[:：]\s*(?P<b2>\S.{1,38})"
    r"|(?P<num>[\d①②③④⑤⑥⑦⑧⑨⑩]{1,2})\s*[.、)）]\s*(?P<b3>\S.{1,30})"
    r")\s*$"
)

_SECTION_LABELS = [
    "岗位职责", "工作职责", "工作内容", "岗位描述", "职责",
    "任职要求", "岗位要求", "任职资格", "招聘要求", "要求",
    "薪资待遇", "薪资", "薪酬", "待遇", "福利待遇", "福利",
    "工作地点", "地点", "工作地址", "地址", "base", "Base",
    "工作经验", "经验", "学历", "公司", "企业", "单位", "联系方式", "联系人",
]
_SECTION_BOUNDARY = "|".join(re.escape(x) for x in _SECTION_LABELS)

SALARY_LABEL_RE = re.compile(
    r"(?:薪资待遇|薪资|薪酬|待遇|月薪|年薪|薪)\s*[:：]\s*"
    r"(面议|薪资面议|\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*[KkWw万千][^\s，。;；]*"
    r"|\d+(?:\.\d+)?\s*[KkWw万千][^\s，。;；]*"
    r"|\d{3,6}\s*[-~—至]\s*\d{3,6}\s*元?[^\s，。;；]*)"
)
SALARY_FREE_RE = re.compile(
    r"(年薪\s*\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*万"
    r"|\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*[KkWw万][^\s，。;；]*"
    r"|\d+(?:\.\d+)?\s*[Kk][^\s，。;；]*"
    r"|\d{4,6}\s*[-~—至]\s*\d{4,6}\s*元)"
)

WECHAT_ID_RE = re.compile(r"(?:微信|微信号|vx|wx|v信|加微)\s*[:：]?\s*([A-Za-z0-9_-]{5,30})", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
COMPANY_RE = re.compile(
    r"([一-龥（）()A-Za-z0-9·&]{2,30}?"
    r"(?:股份有限公司|有限责任公司|有限公司|集团|科技|网络|信息技术|电子商务|国际贸易|贸易|实业|传媒|教育))"
)


def _after_label(text: str, labels: list[str]) -> str | None:
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n]+)", text)
        if m:
            value = m.group(1).strip()
            if value:
                return value
    return None


def _section(text: str, labels: list[str]) -> str | None:
    for label in labels:
        m = re.search(
            rf"{re.escape(label)}\s*[:：]?\s*\n?(.+?)(?=\n\s*(?:{_SECTION_BOUNDARY})\s*[:：]|\Z)",
            text,
            re.DOTALL,
        )
        if m and m.group(1).strip():
            return m.group(1).strip()[:1500]
    return None


def _extract_salary(text: str) -> str | None:
    m = SALARY_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()
    m = SALARY_FREE_RE.search(text)
    if m:
        return m.group(1).strip()
    if "面议" in text:
        return "面议"
    return None


def _extract_contact(text: str) -> str | None:
    parts = []
    m = WECHAT_ID_RE.search(text)
    if m:
        parts.append("微信:" + m.group(1))
    m = EMAIL_RE.search(text)
    if m:
        parts.append("邮箱:" + m.group(0))
    m = PHONE_RE.search(text)
    if m:
        parts.append("电话:" + m.group(1))
    return " ".join(parts) if parts else None


def _guess_company(*sources: str | None) -> str | None:
    for src in sources:
        if not src:
            continue
        m = COMPANY_RE.search(src)
        if m:
            return m.group(1).strip()
    return None


def _segment_blocks(body_text: str) -> tuple[str, list[tuple[str, str]]]:
    """把正文按岗位标题行切块。返回 (preamble 文章级前言, [(title, block_body), ...])。"""
    lines = (body_text or "").split("\n")
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        title = (m.group("b1") or m.group("b2") or m.group("b3") or "").strip()
        if title:
            heads.append((i, title))

    if not heads:
        return body_text or "", []

    blocks: list[tuple[str, str]] = []
    for j, (idx, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        block_body = "\n".join(lines[idx + 1 : end]).strip()
        blocks.append((title, block_body))
    preamble = "\n".join(lines[: heads[0][0]]).strip()
    return preamble, blocks


def _block_to_job(title: str, block_body: str, url: str, article_company: str | None) -> dict:
    scope = f"{title}\n{block_body}"
    duties = _section(block_body, ["岗位职责", "工作职责", "工作内容", "岗位描述", "职责"])
    requirements = _section(block_body, ["任职要求", "岗位要求", "任职资格", "招聘要求", "要求"])
    contact = _extract_contact(block_body)

    desc_parts = [p for p in (duties, requirements) if p]
    description = "\n".join(desc_parts) if desc_parts else (block_body[:2000] or None)
    if contact and description:
        description = f"{description}\n联系方式：{contact}"
    elif contact:
        description = f"联系方式：{contact}"

    return {
        "title": title,
        "company_name": _after_label(block_body, ["公司", "企业", "单位"]) or article_company or "",
        "url": url,
        "salary_text": _extract_salary(scope),
        "city": _after_label(block_body, ["工作地点", "工作地址", "地点", "地址", "base", "Base", "城市"]),
        "experience": _after_label(block_body, ["工作经验", "经验"]),
        "degree": _after_label(block_body, ["学历"]),
        "skills": requirements,
        "description": description,
        "recruiter": contact,
    }


def extract_jobs_regex(body_text: str, url: str, og_title: str | None) -> list[dict]:
    """纯本地正则：把文章正文拆成岗位 dict 列表（键与 normalizer 模糊匹配兼容）。

    拆不出任何岗位块时兜底产出 1 条（标题=文章标题），保证不丢文章。
    """
    preamble, blocks = _segment_blocks(body_text)
    article_company = _guess_company(og_title, preamble)

    if not blocks:
        return [
            {
                "title": (og_title or "公众号招聘").strip(),
                "company_name": article_company or "",
                "url": url,
                "salary_text": _extract_salary(body_text or ""),
                "description": (body_text or "")[:4000] or None,
                "recruiter": _extract_contact(body_text or ""),
            }
        ]

    return [_block_to_job(title, block_body, url, article_company) for title, block_body in blocks]


def extract_jobs(
    body_text: str,
    url: str,
    og_title: str | None,
    *,
    ai_enabled: bool = False,
    min_jobs: int = 1,
) -> list[dict]:
    """编排：默认正则；正则切不出足够岗位块且 ai_enabled 时，用 LLM 兜底补抽。"""
    _, blocks = _segment_blocks(body_text)
    if ai_enabled and len(blocks) < max(1, min_jobs):
        try:
            from . import ai

            llm_jobs = ai.extract_jobs_llm(body_text, url, og_title)
            if llm_jobs:
                return llm_jobs
        except Exception as exc:  # LLM 不可用/失败 → 安全降级回正则
            logger.warning("LLM 兜底抽取失败，沿用正则结果: %s", exc)
    return extract_jobs_regex(body_text, url, og_title)
