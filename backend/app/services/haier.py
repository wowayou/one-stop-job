"""海尔招聘官网（maker.haier.net）来源:抓公开岗位列表 JSON,解析成规范化前的 dict。

设计要点(见 CLAUDE.md §2):
- 列表走公开 JSON 接口 `GET /client/job/searchdata.html?page=N`(每页 20 条,`data.count` 是总数)。
  只读公开列表,不触碰投递/登录(红线 §3.2/§3.3);低频、按页限速。
- 详情页(`/client/job/detail/id/{id}/...`)是服务端渲染,含岗位职责/任职要求;
  但逐条抓详情=每岗一次请求,默认**不抓**,岗位描述留空,保持轻量。需要时开
  `fetch_detail`(仍受限速)。
- 薪资:接口给的是 `min_yearly_salary` / `yearly_salary`(**万/年**)。normalizer.parse_salary
  把「万」当**月薪**处理,直接塞「万/年」会被放大 10 倍;所以这里先把年薪(万)换算成
  月薪(K)再交出去:`K/月 = 万/年 × 10 ÷ 12`。数值缺失或异常(实测有记录把城市名
  塞进了薪资字段)时回退站点自己的 `salary_label`(通常「薪资面议」)。
- 纯函数为主便于单测;网络抓取集中在 fetch_job_list,可在测试中 monkeypatch。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LIST_URL = "https://maker.haier.net/client/job/searchdata.html"
DEFAULT_DETAIL_TEMPLATE = "https://maker.haier.net/client/job/detail/id/{id}/recommend_record/1"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)


def fetch_job_list(url: str, page: int, cfg: dict | None = None) -> dict:
    """抓取一页岗位列表 JSON(失败抛异常,由采集器捕获记录)。"""
    import httpx

    cfg = cfg or {}
    headers = {
        "User-Agent": cfg.get("user_agent", DEFAULT_USER_AGENT),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }
    timeout = float(cfg.get("timeout_seconds", 20))
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url, params={"page": page})
        resp.raise_for_status()
        return resp.json()


def detail_url(job_id: Any, template: str = DEFAULT_DETAIL_TEMPLATE) -> str:
    return template.format(id=job_id)


def _to_float(value: Any) -> float | None:
    """安全转 float:接口字段是字符串,且实测有脏数据(城市名塞进薪资字段)。"""
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def salary_text(record: dict) -> str | None:
    """年薪(万)→ 月薪(K)区间;数值不可用时回退站点 `salary_label`。"""
    lo = _to_float(record.get("min_yearly_salary"))
    hi = _to_float(record.get("yearly_salary"))
    if lo is not None and hi is not None and lo <= hi:
        lo_k = round(lo * 10 / 12)
        hi_k = round(hi * 10 / 12)
        if lo_k > 0 and hi_k > 0:
            return f"{lo_k}-{hi_k}K" if lo_k != hi_k else f"{lo_k}K"
    label = str(record.get("salary_label") or "").strip()
    return label or None


def parse_jobs(payload: dict, cfg: dict | None = None) -> list[dict]:
    """把一页 searchdata JSON 解析成规范化前的 dict 列表(纯函数)。

    只认结构合法(`status==1` 且 `data.list` 是列表)的响应;缺 `id` 或 `job_name`
    的条目跳过(拼不出稳定 url / 无标题)。
    """
    cfg = cfg or {}
    template = cfg.get("detail_url_template", DEFAULT_DETAIL_TEMPLATE)
    company_default = str(cfg.get("company_name") or "海尔集团").strip() or "海尔集团"

    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("list") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    jobs: list[dict] = []
    for record in items:
        if not isinstance(record, dict):
            continue
        job_id = record.get("id")
        title = str(record.get("job_name") or "").strip()
        if not job_id or not title:
            continue
        # xwinfo 是「生态圈/平台/小微」业务链,没抓详情页时它是唯一的部门上下文。
        xwinfo = str(record.get("xwinfo") or "").strip() or None
        jobs.append(
            {
                "title": title,
                "company_name": company_default,
                "url": detail_url(job_id, template),
                "salary_text": salary_text(record),
                "city": str(record.get("location") or "").strip() or None,
                "experience": str(record.get("work_experience_label") or "").strip() or None,
                "degree": str(record.get("education_required_label") or "").strip() or None,
                "description": xwinfo,
                "published_at": str(record.get("update_time") or "").strip() or None,
            }
        )
    return jobs


def total_count(payload: dict) -> int | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        count = data.get("count")
        if isinstance(count, int):
            return count
    return None
