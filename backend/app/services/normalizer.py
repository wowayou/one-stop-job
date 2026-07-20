from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from io import StringIO
from typing import Any

import pandas as pd


TITLE_KEYS = ("name", "title", "职位", "岗位", "job")
COMPANY_KEYS = ("company", "公司", "企业")
URL_KEYS = ("url", "link", "链接")
SALARY_KEYS = ("salary", "薪资", "薪水")
CITY_KEYS = ("city", "城市")
AREA_KEYS = ("area", "区域", "地址", "地点", "工作地")
EXPERIENCE_KEYS = ("experience", "经验")
DEGREE_KEYS = ("degree", "学历")
SKILL_KEYS = ("skills", "技能", "标签")
RECRUITER_KEYS = ("boss", "recruiter", "hr", "招聘")
DESCRIPTION_KEYS = ("description", "desc", "详情", "职责", "要求")
PUBLISHED_KEYS = ("published_at", "posted_at", "publish_time", "发布时间", "发布日期", "发布")
RECRUITMENT_STATUS_KEYS = ("recruitment_status", "招聘状态", "职位状态")
UNKNOWN_TITLES = {"", "未命名岗位", "未知岗位", "unknown", "n/a", "na", "-"}
UNKNOWN_COMPANIES = {"", "未知公司", "unknown", "n/a", "na", "-"}


def clean_csv_output(raw_output: str) -> str:
    lines = [line for line in raw_output.splitlines() if line.strip()]
    if not lines:
        return ""

    header_idx = 0
    for idx, line in enumerate(lines):
        lower = line.lower()
        if ("name" in lower and "company" in lower) or ("职位" in line and "公司" in line):
            header_idx = idx
            break
    return "\n".join(lines[header_idx:])


def dataframe_from_csv_text(raw_output: str) -> pd.DataFrame:
    cleaned = clean_csv_output(raw_output)
    if not cleaned.strip():
        return pd.DataFrame()
    return pd.read_csv(StringIO(cleaned), encoding="utf-8", on_bad_lines="skip")


def parse_salary(salary_text: Any) -> dict[str, float | None]:
    text = str(salary_text or "").strip().upper()
    min_k = max_k = avg_k = annual_w = None
    months = 12

    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-~—]\s*(\d+(?:\.\d+)?)\s*K", text)
    single_match = re.search(r"(\d+(?:\.\d+)?)\s*K", text)
    wan_match = re.search(r"(\d+(?:\.\d+)?)\s*[-~—]\s*(\d+(?:\.\d+)?)\s*万", text)
    months_match = re.search(r"(\d+)\s*薪", text)

    if months_match:
        months = int(months_match.group(1))

    if range_match:
        min_k = float(range_match.group(1))
        max_k = float(range_match.group(2))
    elif wan_match:
        min_k = float(wan_match.group(1)) * 10
        max_k = float(wan_match.group(2)) * 10
    elif single_match:
        min_k = max_k = float(single_match.group(1))

    if min_k is not None and max_k is not None:
        avg_k = round((min_k + max_k) / 2, 2)
        annual_w = round(avg_k * months / 10, 2)

    return {
        "salary_min_k": min_k,
        "salary_max_k": max_k,
        "salary_avg_k": avg_k,
        "annual_salary_w": annual_w,
    }


def parse_recruiter(recruiter: Any) -> dict[str, Any]:
    text = str(recruiter or "").strip()
    title = "未知"
    if "·" in text:
        title = text.split("·")[-1].strip()
    elif "-" in text:
        title = text.split("-")[-1].strip()
    elif text:
        title = text
    is_hr = any(k in title.upper() for k in ["HR", "人事", "招聘", "猎头", "顾问", "人才"])
    return {"recruiter_title": title, "recruiter_is_hr": is_hr}


def parse_city_area(value: Any) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    parts = [p.strip() for p in re.split(r"[·/|,，\s]+", text) if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return text, text


def parse_location(city_value: Any, area_value: Any) -> tuple[str | None, str | None]:
    city_text = str(city_value or "").strip()
    area_text = str(area_value or "").strip()
    if city_text and area_text:
        parsed_city, parsed_area = parse_city_area(area_text)
        if parsed_city == city_text and parsed_area:
            return city_text, parsed_area
        return city_text, area_text
    return parse_city_area(city_text or area_text)


def parse_published_at(value: Any) -> date | None:
    if value is None:
        return None
    try:
        missing = pd.isna(value)
        if not hasattr(missing, "__len__") and bool(missing):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None
    if "今天" in text:
        return date.today()
    if "昨天" in text:
        return date.fromordinal(date.today().toordinal() - 1)
    relative_days = re.search(r"(\d+)\s*天前", text)
    if relative_days:
        return date.fromordinal(date.today().toordinal() - int(relative_days.group(1)))
    match = re.search(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})", text)
    if not match:
        match = re.search(r"(\d{1,2})[月./-](\d{1,2})", text)
        if match:
            year = date.today().year
            month = int(match.group(1))
            day = int(match.group(2))
            return safe_date(year, month, day)
        return None
    return safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_recruitment_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if any(k in text for k in ["停止", "关闭", "下架", "暂停", "过期", "招满", "closed", "inactive", "已结束"]):
        return "closed"
    if any(k in text for k in ["招聘中", "开放", "在招", "active", "open", "有效"]):
        return "active"
    return "unknown"


def first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in row:
        lower = str(key).lower()
        if any(candidate.lower() in lower for candidate in keys):
            value = row.get(key)
            if pd.notna(value) and str(value).strip():
                return value
    return None


def stable_external_id(source: str, title: str, company_name: str, url: str | None, salary_text: str | None) -> str:
    raw = url or f"{source}|{title}|{company_name}|{salary_text or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def canonical_job_key(title: str, company_name: str, city: str | None, area: str | None) -> str | None:
    normalized_title = re.sub(r"\s+", "", (title or "").lower())
    normalized_company = re.sub(r"\s+", "", (company_name or "").lower())
    if normalized_title in UNKNOWN_TITLES or normalized_company in UNKNOWN_COMPANIES:
        return None
    raw = "|".join(
        re.sub(r"\s+", "", part.lower())
        for part in [title or "", company_name or "", city or "", area or ""]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def normalize_record(raw: dict[str, Any], source: str) -> dict[str, Any]:
    title = str(first_value(raw, TITLE_KEYS) or "未命名岗位").strip()
    company_name = str(first_value(raw, COMPANY_KEYS) or "未知公司").strip()
    url = first_value(raw, URL_KEYS)
    salary_text = first_value(raw, SALARY_KEYS)
    city_value = first_value(raw, CITY_KEYS)
    area_value = first_value(raw, AREA_KEYS)
    city, area = parse_location(city_value, area_value)
    recruiter = first_value(raw, RECRUITER_KEYS)
    published_at = parse_published_at(first_value(raw, PUBLISHED_KEYS))
    recruitment_status = parse_recruitment_status(first_value(raw, RECRUITMENT_STATUS_KEYS))
    salary = parse_salary(salary_text)

    normalized = {
        "source": source,
        "external_id": stable_external_id(source, title, company_name, str(url) if url else None, str(salary_text) if salary_text else None),
        "url": str(url).strip() if url else None,
        "title": title,
        "company_name": company_name,
        "salary_text": str(salary_text).strip() if salary_text else None,
        "city": city,
        "area": area,
        "experience": str(first_value(raw, EXPERIENCE_KEYS) or "").strip() or None,
        "degree": str(first_value(raw, DEGREE_KEYS) or "").strip() or None,
        "skills": str(first_value(raw, SKILL_KEYS) or "").strip() or None,
        "description": str(first_value(raw, DESCRIPTION_KEYS) or "").strip() or None,
        "recruiter": str(recruiter).strip() if recruiter else None,
        "published_at": published_at,
        "recruitment_status": recruitment_status,
        "canonical_key": canonical_job_key(title, company_name, city, area),
        **salary,
        **parse_recruiter(recruiter),
    }
    return normalized


def normalize_dataframe(df: pd.DataFrame, source: str) -> list[dict[str, Any]]:
    if df.empty:
        return []
    records = []
    for raw in df.fillna("").to_dict(orient="records"):
        records.append(normalize_record(raw, source=source))
    return records
