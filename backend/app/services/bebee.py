"""beBee 渠道:抓取角色/列表页,解析其中的岗位。

设计要点(见 CLAUDE.md §2):
- 主解析走 JSON-LD(`<script type="application/ld+json">`),再回退 Next/RSC jobs、microdata 和可见列表卡片。
- 都拿不到时由采集器记录更明确的 skip 原因。
- 纯函数为主,便于单测;网络抓取集中在 fetch_listing,可在测试中 monkeypatch。
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)


def fetch_listing(url: str, cfg: dict | None = None) -> str:
    """抓取一个 beBee 列表/角色页 HTML(失败抛异常,由采集器捕获记录)。"""
    import httpx

    cfg = cfg or {}
    headers = {
        "User-Agent": cfg.get("user_agent", DEFAULT_USER_AGENT),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    timeout = float(cfg.get("timeout_seconds", 20))
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text or ""


def _clean(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


# ==================== JSON-LD ====================

def _iter_jsonld(html: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            yield json.loads(raw)
        except Exception:
            continue


def _collect_jobpostings(node, out: list[dict]) -> None:
    if isinstance(node, dict):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "JobPosting" in types:
            out.append(node)
        for key in ("@graph", "itemListElement", "item", "mainEntity", "mainEntityOfPage"):
            if key in node:
                _collect_jobpostings(node[key], out)
    elif isinstance(node, list):
        for item in node:
            _collect_jobpostings(item, out)


def _company(node: dict) -> str | None:
    org = node.get("hiringOrganization")
    if isinstance(org, dict):
        return _clean(org.get("name"))
    if isinstance(org, str):
        return _clean(org)
    return None


def _location(node: dict) -> tuple[str | None, str | None]:
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            return _clean(addr.get("addressLocality")), _clean(addr.get("addressRegion"))
        if isinstance(addr, str):
            return _clean(addr), None
        return _clean(loc.get("name")), None
    if isinstance(loc, str):
        return _clean(loc), None
    return None, None


def _salary(node: dict) -> str | None:
    base = node.get("baseSalary") or node.get("estimatedSalary")
    if not isinstance(base, dict):
        return None
    currency = base.get("currency") or ""
    val = base.get("value")
    if isinstance(val, dict):
        unit = val.get("unitText") or ""
        lo, hi, single = val.get("minValue"), val.get("maxValue"), val.get("value")
        if lo and hi:
            return _clean(f"{lo}-{hi} {currency}/{unit}".strip(" /"))
        if single:
            return _clean(f"{single} {currency}/{unit}".strip(" /"))
    elif val:
        return _clean(f"{val} {currency}".strip())
    return None


def parse_jobs_jsonld(html: str, base_url: str | None = None) -> list[dict]:
    postings: list[dict] = []
    for data in _iter_jsonld(html):
        _collect_jobpostings(data, postings)

    jobs: list[dict] = []
    for posting in postings:
        title = _clean(posting.get("title"))
        if not title:
            continue
        city, area = _location(posting)
        url = posting.get("url")
        if url and base_url:
            url = urljoin(base_url, url)
        jobs.append(
            {
                "title": title,
                "company_name": _company(posting) or "",
                "url": url or base_url,
                "salary_text": _salary(posting),
                "city": city,
                "area": area,
                "description": _clean(posting.get("description")),
            }
        )
    return jobs


# ==================== Next / React Flight 回退 ====================

def _balanced_slice(text: str, start: int, opener: str, closer: str) -> str | None:
    if start < 0 or start >= len(text) or text[start] != opener:
        return None

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _collect_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_collect_strings(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_collect_strings(item))
        return out
    return []


def _next_push_payloads(script_text: str) -> list[str]:
    payloads: list[str] = []
    starts: list[int] = []
    for match in re.finditer(r"(?:self\.)?__next_f\.push\(", script_text):
        starts.append(match.end())
    for match in re.finditer(r"\.push\(", script_text):
        prefix = script_text[max(0, match.start() - 80) : match.start()]
        if "__next_f" in prefix:
            starts.append(match.end())

    seen_starts: set[int] = set()
    for args_start in sorted(starts):
        if args_start in seen_starts:
            continue
        seen_starts.add(args_start)
        args = _balanced_slice(script_text, args_start, "[", "]")
        if not args:
            continue
        try:
            payloads.extend(_collect_strings(json.loads(args)))
        except Exception:
            pass
    return payloads


def _payload_texts(html: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    payloads = [html or ""]
    for script in soup.find_all("script"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        payloads.append(raw)
        if '\\"' in raw:
            payloads.append(raw.replace('\\"', '"').replace("\\/", "/"))
        payloads.extend(_next_push_payloads(raw))
        script_type = str(script.get("type") or "").lower()
        if script_type == "application/json" or script.get("id") == "__NEXT_DATA__":
            try:
                payloads.extend(_collect_strings(json.loads(raw)))
            except Exception:
                pass
    return payloads


def _next_jobs_blocks(html: str) -> list[str]:
    blocks: list[str] = []
    seen: set[str] = set()
    key_re = re.compile(r'(?:"jobs"|jobs)\s*:')
    for text in _payload_texts(html):
        for match in key_re.finditer(text):
            index = match.end()
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text) or text[index] != "[":
                continue
            block = _balanced_slice(text, index, "[", "]")
            if block and block not in seen:
                seen.add(block)
                blocks.append(block)
    return blocks


def _quote_js_object_keys(value: str) -> str:
    out: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            out.append(char)
            index += 1
            continue

        previous = out[-1] if out else ""
        if (not previous or previous in "{,") or previous.isspace():
            lookahead = value[index:]
            match = re.match(r"\s*([A-Za-z_$][\w$-]*)\s*:", lookahead)
            if match:
                leading = lookahead[: len(match.group(0)) - len(match.group(1)) - 1]
                out.append(leading)
                out.append(json.dumps(match.group(1)))
                out.append(":")
                index += len(match.group(0))
                continue

        out.append(char)
        index += 1
    return "".join(out)


def _parse_jobs_block(block: str) -> list[dict] | None:
    candidates = [
        block,
        re.sub(r",\s*([}\]])", r"\1", block),
        _quote_js_object_keys(re.sub(r",\s*([}\]])", r"\1", block)),
    ]
    for candidate in candidates:
        candidate = re.sub(r"\bundefined\b", "null", candidate)
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return None


def _scalar(value) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "title", "label", "value", "text"):
            if key in value:
                found = _scalar(value[key])
                if found:
                    return found
        return None
    if isinstance(value, list):
        parts = [_scalar(item) for item in value]
        return _clean(", ".join(part for part in parts if part))
    return _clean(value)


def _keywords_text(value) -> str | None:
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _scalar(item)
            if text:
                parts.append(text)
        return _clean(", ".join(parts))
    return _scalar(value)


def _job_from_next_payload(node: dict, base_url: str | None = None) -> dict | None:
    title = _scalar(node.get("title") or node.get("name"))
    if not title:
        return None

    url = _scalar(node.get("external_apply_url") or node.get("url") or node.get("apply_url"))
    if url and base_url:
        url = urljoin(base_url, url)

    location = _scalar(node.get("geoname_locality")) or _scalar(node.get("location_name")) or _scalar(node.get("location"))
    return {
        "title": title,
        "company_name": _scalar(node.get("publisher_name") or node.get("company_name") or node.get("company")) or "",
        "url": url or base_url,
        "city": location,
        "description": _clean(node.get("description")),
        "published_at": _scalar(node.get("started_date") or node.get("datePosted") or node.get("published_at")),
        "skills": _keywords_text(node.get("primary_keywords") or node.get("keywords")),
    }


def parse_jobs_next_payload(html: str, base_url: str | None = None) -> list[dict]:
    """Parse beBee jobs embedded in Next.js / React Flight payload chunks."""

    jobs: list[dict] = []
    for block in _next_jobs_blocks(html):
        parsed = _parse_jobs_block(block)
        if parsed is None:
            continue
        for item in parsed:
            job = _job_from_next_payload(item, base_url)
            if job:
                jobs.append(job)
    return _dedupe_jobs(jobs)


# ==================== microdata 回退 ====================

def parse_jobs_microdata(html: str, base_url: str | None = None) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    jobs: list[dict] = []
    for node in soup.select('[itemtype*="JobPosting"]'):
        def prop(name: str) -> str | None:
            el = node.find(attrs={"itemprop": name})
            if not el:
                return None
            return _clean(el.get("content") or el.get_text(" ", strip=True))

        title = prop("title")
        if not title:
            continue
        href = None
        link = node.find("a", href=True)
        if link:
            href = urljoin(base_url or "", link["href"])
        jobs.append(
            {
                "title": title,
                "company_name": prop("hiringOrganization") or prop("name") or "",
                "url": prop("url") or href or base_url,
                "salary_text": prop("baseSalary"),
                "city": prop("addressLocality") or prop("jobLocation"),
                "description": prop("description"),
            }
        )
    return jobs


def _attr_contains_selector(attrs: list[str]) -> str:
    return ", ".join(f'[class*="{attr}"], [data-testid*="{attr}"], [id*="{attr}"]' for attr in attrs)


def _first_text(node, selectors: list[str]) -> str | None:
    for selector in selectors:
        el = node.select_one(selector)
        if el:
            text = _clean(el.get("datetime") or el.get("content") or el.get("title") or el.get_text(" ", strip=True))
            if text:
                return text
    return None


def _best_link(node, base_url: str | None = None) -> tuple[str | None, str | None]:
    candidates = []
    for link in node.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        text = _clean(link.get("title") or link.get_text(" ", strip=True))
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        score = 0
        lower_href = href.lower()
        if "/job" in lower_href or "/jobs/" in lower_href:
            score += 4
        if text:
            score += min(len(text), 80) / 80
        candidates.append((score, href, text))
    if not candidates:
        return None, None
    _score, href, text = max(candidates, key=lambda item: item[0])
    return urljoin(base_url or "", href), text


def _meta_content(node, names: list[str]) -> str | None:
    for name in names:
        el = node.find(attrs={"name": name}) or node.find(attrs={"property": name}) or node.find(attrs={"itemprop": name})
        if el:
            value = _clean(el.get("content") or el.get_text(" ", strip=True))
            if value:
                return value
    return None


def _looks_like_job_card(node) -> bool:
    text = _clean(node.get_text(" ", strip=True)) or ""
    if len(text) < 8:
        return False
    has_link = bool(node.find("a", href=True))
    class_blob = " ".join(str(item) for item in (node.get("class") or [])).lower()
    id_blob = str(node.get("id") or "").lower()
    data_blob = " ".join(f"{key}={value}" for key, value in node.attrs.items() if key.startswith("data-")).lower()
    markers = ("job", "vacancy", "position", "offer", "listing", "card")
    return has_link and any(marker in f"{class_blob} {id_blob} {data_blob}" for marker in markers)


def _dedupe_jobs(jobs: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for job in jobs:
        key = (
            str(job.get("url") or ""),
            str(job.get("title") or "").lower(),
            str(job.get("company_name") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped


def parse_jobs_cards(html: str, base_url: str | None = None) -> list[dict]:
    """Parse visible beBee-style listing cards when no JobPosting markup exists."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    card_selector = ", ".join(
        [
            'article',
            '[role="listitem"]',
            '[data-testid*="job"]',
            '[data-test*="job"]',
            '[class*="job-card"]',
            '[class*="JobCard"]',
            '[class*="job-list"] li',
            '[class*="jobs-list"] li',
            '[class*="vacancy"]',
            '[class*="position"]',
        ]
    )
    nodes = [node for node in soup.select(card_selector) if _looks_like_job_card(node)]
    if not nodes:
        nodes = [node for node in soup.find_all(["article", "li", "div"]) if _looks_like_job_card(node)]

    jobs: list[dict] = []
    company_selector = _attr_contains_selector(["company", "employer", "organization", "business"])
    location_selector = _attr_contains_selector(["location", "city", "place", "address"])
    salary_selector = _attr_contains_selector(["salary", "compensation", "pay"])
    date_selector = _attr_contains_selector(["date", "time", "posted", "publish"])
    description_selector = _attr_contains_selector(["description", "summary", "snippet", "excerpt"])

    for node in nodes:
        url, link_text = _best_link(node, base_url)
        title = _first_text(
            node,
            [
                '[itemprop="title"]',
                '[data-testid*="title"]',
                '[data-test*="title"]',
                '[class*="title"]',
                '[class*="Title"]',
                'h1',
                'h2',
                'h3',
                'a[href]',
            ],
        ) or link_text
        title = _clean(title)
        if not title:
            continue

        company = _first_text(node, [company_selector, '[itemprop="hiringOrganization"]']) or ""
        location = _first_text(node, [location_selector, '[itemprop="jobLocation"]'])
        salary = _first_text(node, [salary_selector, '[itemprop="baseSalary"]'])
        published_at = _first_text(node, ['time[datetime]', date_selector, '[itemprop="datePosted"]'])
        description = _first_text(node, [description_selector, '[itemprop="description"]'])

        if not published_at:
            time_el = node.find("time")
            if time_el:
                published_at = _clean(time_el.get("datetime") or time_el.get_text(" ", strip=True))

        jobs.append(
            {
                "title": title,
                "company_name": company,
                "url": url or base_url,
                "salary_text": salary,
                "city": location,
                "description": description,
                "published_at": published_at,
            }
        )

    return _dedupe_jobs(jobs)


def diagnose_empty_html(html: str) -> str:
    """Return an actionable skipped reason for pages with no parsed jobs."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    text = _clean(soup.get_text(" ", strip=True)) or ""
    has_jsonld = bool(soup.find("script", attrs={"type": "application/ld+json"}))
    has_microdata = bool(soup.select('[itemtype*="JobPosting"]'))
    has_job_word = bool(re.search(r"\bjob|职位|岗位|招聘", html or "", re.IGNORECASE))
    script_text = " ".join(script.get_text(" ", strip=True)[:500] for script in soup.find_all("script")[:20])
    js_markers = ("__NEXT_DATA__", "__NUXT__", "window.__", "apolloState", "hydration", "vite")
    has_js_marker = any(marker.lower() in (html or script_text).lower() for marker in js_markers)
    next_blocks = _next_jobs_blocks(html)

    if next_blocks:
        parsed_blocks = [_parse_jobs_block(block) for block in next_blocks]
        if not any(block is not None for block in parsed_blocks):
            return "页面含 Next/RSC jobs 数据块，但解析失败；请提供该页面完整 HTML 样例补 payload 解析。"
        if any(block == [] for block in parsed_blocks if block is not None):
            return "页面含 Next/RSC jobs 数据块，但 jobs 为空；请确认该 URL 当前有公开岗位。"
        return "页面含 Next/RSC jobs 数据块，但未解析出有效岗位字段；请检查 title/publisher_name/url 等字段结构。"
    if not (html or "").strip() or len(text) < 20:
        return "页面源码几乎为空，未找到 JobPosting 或岗位卡片：可能被重定向、风控拦截，或岗位由 JS 接口渲染。请保存浏览器完整 HTML 或 Network 岗位 JSON。"
    if has_jsonld or has_microdata:
        return "页面含结构化数据，但未发现可用 JobPosting 字段；请检查标题/公司字段是否为空，或提供 HTML 样例补解析。"
    if has_js_marker and not has_job_word:
        return "未找到 JobPosting 或岗位卡片；页面疑似由 JS 接口渲染。请提供 Network 面板里的岗位列表 JSON 响应。"
    if has_job_word:
        return "未找到 JobPosting，也未识别到可用岗位卡片；请提供该列表页完整 HTML 样例补卡片选择器。"
    return "未找到 JobPosting 结构化数据或岗位卡片；请确认 URL 是公开岗位列表页，或提供浏览器保存的完整 HTML。"


def extract_jobs(html: str, base_url: str | None = None) -> list[dict]:
    """先 JSON-LD,空则 Next/RSC jobs,再 microdata 和 CSS 卡片回退。"""
    jobs = parse_jobs_jsonld(html, base_url)
    if jobs:
        return jobs
    jobs = parse_jobs_next_payload(html, base_url)
    if jobs:
        return jobs
    jobs = parse_jobs_microdata(html, base_url)
    if jobs:
        return jobs
    return parse_jobs_cards(html, base_url)
