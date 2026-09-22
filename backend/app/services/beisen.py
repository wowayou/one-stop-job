"""北森(Beisen)iTalent 招聘门户通用来源:抓公开岗位列表 JSON,解析成规范化前的 dict。

大量企业招聘官网(海信、以及其它用北森 iTalent 的公司)都是同一套 SPA + 同一套公开
接口:`POST {host}/api/Jobad/GetJobAdPageList`(需 `Content-Type: application/json`
+ `X-Requested-With`,`Category:["1"]`=社会招聘,`Count` 是总数,`Data[]` 是岗位,
列表已带 `Duty`/`Require`,无需抓详情页)。所以只写一份解析,靠配置里的 `list_url`
+ `company_name` + `detail_url_template` 接入不同公司。

设计要点(见 CLAUDE.md §2):
- **只读公开列表**,不触碰投递/登录(红线 §3.2/§3.3);低频、按页限速。
- 北森列表通常不公布薪资(`Salary` 多为 null),照实留空,不硬造数字。
- 详情页 url 用岗位的 `Id`(GUID),不是 `JobAdId`(整数)。
- 纯函数为主便于单测;网络抓取集中在 fetch_job_list,可在测试中 monkeypatch。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 北森门户列表接口的相对路径(拼在各公司 host 后);也可在 portal 配置里用 list_url 整段覆盖。
DEFAULT_LIST_PATH = "/api/Jobad/GetJobAdPageList"
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


def detail_url(job_guid: Any, template: str) -> str:
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

    `cfg` 是**单个门户**的配置(至少含 `detail_url_template`、`company_name`)。
    只认结构合法(`Code==200` 且 `Data` 是列表)的响应;缺 `Id` 或 `JobAdName` 的条目跳过。
    """
    cfg = cfg or {}
    template = cfg.get("detail_url_template")
    company_default = str(cfg.get("company_name") or "").strip() or "未知公司"

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
        # detail_url_template 缺失时不硬拼 url（external_id 会退到 title+company），但正常配置都应有。
        url = detail_url(guid, template) if template else None
        # ClassificationTwo 是事业部(如「空调事业部」),放进 skills 作为部门线索。
        jobs.append(
            {
                "title": title,
                "company_name": company_default,
                "url": url,
                "salary_text": _clean(record.get("Salary")),  # 北森列表通常不公布薪资,多为 None
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
