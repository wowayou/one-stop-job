"""海信招聘官网（jobs.hisense.com）来源:抓公开岗位列表 JSON,解析成规范化前的 dict。

设计要点(见 CLAUDE.md §2):
- 海信招聘门户是北森(Beisen)iTalent SaaS 的纯前端 SPA,页面 HTML 是空壳,岗位数据全走
  后端 API。列表接口 `POST /api/Jobad/GetJobAdPageList`(公开、无需登录)一次返回一页,
  `Count` 是总数,`Data[]` 是岗位。**只读公开列表**,不触碰投递/登录(红线 §3.2/§3.3);
  低频、按页限速。
- 列表响应里已带 `Duty`(岗位职责)/`Require`(任职要求),**无需再逐条抓详情页**,保持轻量。
- 海信不在列表里公布薪资(`Salary` 恒为 null),所以 `salary_text` 通常为空——照实留空,
  不硬造数字(评分侧薪资信号缺失是来源本身的性质)。
- `Category:["1"]` = 社会招聘;详情页 url 用岗位的 `Id`(GUID),不是 `JobAdId`(整数)。
- 纯函数为主便于单测;网络抓取集中在 fetch_job_list,可在测试中 monkeypatch。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LIST_URL = "https://jobs.hisense.com/api/Jobad/GetJobAdPageList"
DEFAULT_DETAIL_TEMPLATE = "https://jobs.hisense.com/social/detail?jobAdId={id}"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)
# 列表接口需要的展示字段（照抄门户前端的请求，缺了部分字段不返回）。
DEFAULT_DISPLAY_FIELDS = ["Category", "Kind", "LocId", "PostDate", "ClassificationOne", "ClassificationTwo"]


def fetch_job_list(url: str, page: int, page_size: int, cfg: dict | None = None) -> dict:
    """抓取一页岗位列表 JSON(失败抛异常,由采集器捕获记录)。

    北森接口要求 `Content-Type: application/json` + `X-Requested-With: XMLHttpRequest`,
    缺了会被门户网关退回 SPA 空壳 HTML。`Category` 默认 `["1"]`(社会招聘)。
    """
    import httpx

    cfg = cfg or {}
    headers = {
        "User-Agent": cfg.get("user_agent", DEFAULT_USER_AGENT),
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }
    body = {
        "PageIndex": page,
        "PageSize": page_size,
        "KeyWords": "",
        "Category": [str(cfg.get("category", "1"))],
        "SpecialType": 0,
        "DisplayFields": cfg.get("display_fields", DEFAULT_DISPLAY_FIELDS),
    }
    timeout = float(cfg.get("timeout_seconds", 20))
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


def detail_url(job_guid: Any, template: str = DEFAULT_DETAIL_TEMPLATE) -> str:
    return template.format(id=job_guid)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _location(record: dict) -> str | None:
    """LocNames 是数组(如 ["山东省·青岛市"]);取第一个交给 normalizer.parse_location。"""
    names = record.get("LocNames")
    if isinstance(names, list) and names:
        return _clean(names[0])
    return _clean(names)


def _description(record: dict) -> str | None:
    """岗位职责 + 任职要求拼成描述(列表接口已带,无需抓详情页)。"""
    parts: list[str] = []
    duty = _clean(record.get("Duty"))
    require = _clean(record.get("Require"))
    if duty:
        parts.append(f"岗位职责\n{duty}")
    if require:
        parts.append(f"任职要求\n{require}")
    return "\n\n".join(parts) or None


def parse_jobs(payload: dict, cfg: dict | None = None) -> list[dict]:
    """把一页 GetJobAdPageList JSON 解析成规范化前的 dict 列表(纯函数)。

    只认结构合法(`Code==200` 且 `Data` 是列表)的响应;缺 `Id` 或 `JobAdName` 的条目跳过。
    """
    cfg = cfg or {}
    template = cfg.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
    company_default = str(cfg.get("company_name") or "海信集团").strip() or "海信集团"

    if not isinstance(payload, dict) or payload.get("Code") != 200:
        return []
    items = payload.get("Data")
    if not isinstance(items, list):
        return []

    jobs: list[dict] = []
    for record in items:
        if not isinstance(record, dict):
            continue
        guid = record.get("Id")
        title = _clean(record.get("JobAdName"))
        if not guid or not title:
            continue
        # ClassificationTwo 是事业部(如「空调事业部」),放进 skills 作为部门线索。
        jobs.append(
            {
                "title": title,
                "company_name": company_default,
                "url": detail_url(guid, template),
                "salary_text": _clean(record.get("Salary")),  # 海信列表不公布薪资,通常 None
                "city": _location(record),
                "experience": _clean(record.get("YearsOfWorking")),
                "degree": _clean(record.get("Degree")),
                "skills": _clean(record.get("ClassificationTwo")),
                "description": _description(record),
                "published_at": _clean(record.get("PostDate")),
            }
        )
    return jobs


def total_count(payload: dict) -> int | None:
    if isinstance(payload, dict):
        count = payload.get("Count")
        if isinstance(count, int):
            return count
    return None
