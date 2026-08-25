from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from . import bebee, wechat
from .normalizer import dataframe_from_csv_text, normalize_dataframe, normalize_record


class Collector(Protocol):
    source: str

    def collect(self) -> list[dict]:
        ...


def _is_windows_style_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value)) or value.startswith("\\\\")


def _windows_opencli_candidates() -> list[str]:
    if not shutil.which("cmd.exe"):
        return []
    try:
        result = subprocess.run(
            ["cmd.exe", "/c", "where", "opencli"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def recommended_opencli_path() -> str | None:
    candidates = _windows_opencli_candidates()
    for candidate in candidates:
        if candidate.lower().endswith(".cmd"):
            return candidate
    return candidates[0] if candidates else shutil.which("opencli")


def _build_opencli_command(opencli_path: str, boss_cmd: list[str]) -> tuple[list[str] | str, bool]:
    if not boss_cmd:
        raise RuntimeError("config.yaml 未配置 opencli.boss_cmd")

    candidate = (opencli_path or boss_cmd[0] or "opencli").strip()
    args = boss_cmd[1:]
    normalized_candidate = re.sub(r"/+", "/", candidate.replace("\\", "/").lower())

    if normalized_candidate.startswith("c:/path/to/"):
        raise RuntimeError(
            "OpenCLI 路径仍是占位值。请在宿主机终端运行 `where opencli` 或 `which opencli`，"
            "把真实路径作为宿主机脚本的 --opencli 参数传入，或加入 PATH。"
        )

    if _is_windows_style_path(candidate):
        if os.name == "nt":
            return subprocess.list2cmdline([candidate, *args]), True
        if shutil.which("cmd.exe"):
            return ["cmd.exe", "/c", candidate, *args], False
        raise RuntimeError(
            "OpenCLI 配置为 Windows 路径，但当前运行环境找不到 cmd.exe。"
            "如果后端运行在 WSL，请确认 WSL 可调用 Windows 命令；宿主机采集可改用 --opencli 临时指定路径。"
        )

    if "/" in candidate or "\\" in candidate:
        path = Path(candidate)
        if path.exists():
            return [str(path), *args], False
        raise RuntimeError(
            f"OpenCLI 路径无效: {candidate}。请在宿主机终端运行 `where opencli` 或 `which opencli`，"
            "再通过宿主机脚本的 --opencli 参数指定。"
        )

    resolved = shutil.which(candidate)
    if not resolved:
        raise RuntimeError(
            f"找不到 OpenCLI 命令: {candidate}。请在宿主机终端运行 `where opencli` 或 `which opencli`，"
            "并确保 opencli 在 PATH 中；宿主机脚本也支持 --opencli 临时指定。"
        )
    return [resolved, *args], False


def inspect_opencli(opencli_path: str, command: list[str]) -> dict[str, Any]:
    recommendation = recommended_opencli_path()
    if not command:
        return {
            "status": "not_configured",
            "configured": False,
            "message": "未配置 OpenCLI 命令。",
            "recommended_path": recommendation,
        }

    try:
        resolved_command, use_shell = _build_opencli_command(opencli_path, command)
    except RuntimeError as exc:
        return {
            "status": "error",
            "configured": False,
            "message": str(exc),
            "recommended_path": recommendation,
        }

    runtime = "windows_cmd_proxy" if isinstance(resolved_command, list) and resolved_command[:2] == ["cmd.exe", "/c"] else "local"
    return {
        "status": "ok",
        "configured": True,
        "message": "OpenCLI 已配置，可由当前运行环境调用。",
        "runtime": "shell" if use_shell else runtime,
        "recommended_path": recommendation,
    }


@dataclass
class OpenCLICommandCollector:
    opencli_path: str
    command: list[str]
    timeout_seconds: int = 120
    source: str = "BOSS直聘"

    def collect(self) -> list[dict]:
        command, use_shell = _build_opencli_command(self.opencli_path, self.command)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            shell=use_shell,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "OpenCLI 执行失败")
        df = dataframe_from_csv_text(result.stdout)
        return normalize_dataframe(df, source=self.source)


@dataclass
class BossOpenCLICollector(OpenCLICommandCollector):
    def __init__(self, opencli_path: str, boss_cmd: list[str], timeout_seconds: int = 120, source: str = "BOSS直聘"):
        super().__init__(opencli_path=opencli_path, command=boss_cmd, timeout_seconds=timeout_seconds, source=source)


def command_with_query(command: list[str], query: str) -> list[str]:
    """把 OpenCLI 搜索命令里 `search` 后面的关键词替换成 query；找不到 `search` 则原样返回。"""
    result = list(command)
    for index, token in enumerate(result[:-1]):
        if token == "search":
            result[index + 1] = query
            return result
    return result


def command_with_query_limit(command: list[str], query: str, limit: int | None = None) -> list[str]:
    result = command_with_query(command, query)
    if limit is None:
        return result
    for index, token in enumerate(result[:-1]):
        if token == "--limit":
            result[index + 1] = str(max(1, limit))
            return result
    return [*result, "--limit", str(max(1, limit))]


@dataclass
class OpenCLIMultiCommandCollector:
    """按多关键词依次跑同一 OpenCLI 搜索命令，并按 external_id 跨命令去重。

    单个关键词失败进 report.skipped（不静默丢，§3.7）；全部失败才抛错让
    SourceRun 记为 failed。命令间 sleep 限速，符合低频抓取边界（§3.3）。
    """

    opencli_path: str
    commands: list[list[str]]
    timeout_seconds: int = 120
    rate_limit_seconds: float = 2.0
    source: str = "BOSS直聘"

    def __post_init__(self) -> None:
        self.report: dict = {"commands_total": len(self.commands), "commands_ok": 0, "jobs": 0, "skipped": []}

    def collect(self) -> list[dict]:
        records: list[dict] = []
        seen_external: set[str] = set()
        for index, command in enumerate(self.commands):
            if index > 0 and self.rate_limit_seconds:
                time.sleep(self.rate_limit_seconds)
            label = " ".join(command[1:4]) if len(command) > 1 else str(command)
            try:
                rows = OpenCLICommandCollector(
                    opencli_path=self.opencli_path,
                    command=command,
                    timeout_seconds=self.timeout_seconds,
                    source=self.source,
                ).collect()
            except Exception as exc:  # noqa: BLE001 - 单关键词失败不终止整批
                self.report["skipped"].append({"command": label, "reason": str(exc)})
                continue
            self.report["commands_ok"] += 1
            for row in rows:
                ext = row.get("external_id")
                if ext in seen_external:
                    continue
                seen_external.add(ext)
                records.append(row)
        if self.report["commands_ok"] == 0 and self.report["skipped"]:
            raise RuntimeError(f"全部关键词采集失败: {self.report['skipped']}")
        self.report["jobs"] = len(records)
        return records


@dataclass
class TabularFileCollector:
    df: pd.DataFrame
    source: str = "导入文件"

    def collect(self) -> list[dict]:
        return normalize_dataframe(self.df, source=self.source)


@dataclass
class WeChatPasteCollector:
    """公众号渠道采集器：链接 → 抓取/手动正文 → 拆分多岗位 → 规范化记录。

    元宝（或公众号后台搜索、手动浏览）得到的 mp.weixin 链接经端点归一化后传入；
    每条链接的正文优先用 bodies 中手动粘贴的内容，否则按需自动抓取。
    """

    links: list[str]
    bodies: dict[str, str] = field(default_factory=dict)  # canon_url -> 手动粘贴的正文
    cfg: dict = field(default_factory=dict)               # config.yaml 的 wechat 段
    ai_enabled: bool = False
    min_jobs: int = 1
    rate_limit_seconds: float = 0.0
    source: str = "公众号"

    def __post_init__(self) -> None:
        # 批次报告（写入 SourceRun.raw_config），记录跳过原因，不静默丢失
        self.report: dict = {"urls_total": 0, "urls_ok": 0, "jobs": 0, "skipped": []}

    def collect(self) -> list[dict]:
        bodies = {wechat.canonicalize_mp_url(k): v for k, v in (self.bodies or {}).items()}
        links = self.links or []
        self.report["urls_total"] = len(links)

        fetch_cfg = (self.cfg or {}).get("fetch", {})
        fetch_enabled = fetch_cfg.get("enabled", True)

        records: list[dict] = []
        seen_external: set[str] = set()

        for idx, link in enumerate(links):
            canon = wechat.canonicalize_mp_url(link)
            og_title: str | None = None
            body_text: str | None = None

            if canon in bodies and str(bodies[canon]).strip():
                body_text = bodies[canon]
            elif fetch_enabled:
                if idx > 0 and self.rate_limit_seconds:
                    time.sleep(self.rate_limit_seconds)
                fetched = wechat.fetch_article(canon, fetch_cfg)
                if not fetched.ok:
                    self.report["skipped"].append({"url": canon, "reason": fetched.reason})
                    continue
                og_title, body_text = fetched.og_title, fetched.body_text
            else:
                self.report["skipped"].append({"url": canon, "reason": "未提供正文且自动抓取已关闭"})
                continue

            jobs = wechat.extract_jobs(
                body_text or "", canon, og_title, ai_enabled=self.ai_enabled, min_jobs=self.min_jobs
            )
            self.report["urls_ok"] += 1

            for raw in jobs:
                raw.setdefault("url", canon)
                normalized = normalize_record(raw, source=self.source)
                # 关键：一篇文章多个岗位共享同一 url，sha1(url) 会让它们互相覆盖；
                # 覆写为 sha1(url|title|company)，同时保留 url 为干净永久链。
                title = normalized.get("title") or ""
                company = normalized.get("company_name") or ""
                ext = hashlib.sha1(f"{canon}|{title}|{company}".encode("utf-8")).hexdigest()
                normalized["external_id"] = ext
                normalized["url"] = canon
                if ext in seen_external:
                    continue
                seen_external.add(ext)
                records.append(normalized)

        self.report["jobs"] = len(records)
        return records


@dataclass
class BeBeeCollector:
    """beBee 渠道采集器:抓配置里的角色/列表页 → 解析 JobPosting → 规范化记录。

    每个岗位有自己的详情 url,external_id 走默认 sha1(url) 即天然唯一,无需覆写。
    """

    urls: list[str]
    cfg: dict = field(default_factory=dict)  # config.yaml 的 bebee 段
    source: str = "beBee"

    def __post_init__(self) -> None:
        self.report: dict = {"urls_total": 0, "urls_ok": 0, "jobs": 0, "skipped": []}

    def collect(self) -> list[dict]:
        urls = self.urls or []
        self.report["urls_total"] = len(urls)
        rate = float(self.cfg.get("rate_limit_seconds", 0) or 0)

        records: list[dict] = []
        seen_external: set[str] = set()

        for idx, url in enumerate(urls):
            try:
                if idx > 0 and rate:
                    time.sleep(rate)
                html = bebee.fetch_listing(url, self.cfg)
                jobs = bebee.extract_jobs(html, base_url=url)
            except Exception as exc:  # 网络/解析失败 → 跳过并记录,不中断整批
                self.report["skipped"].append({"url": url, "reason": f"抓取/解析失败: {exc}"})
                continue

            if not jobs:
                self.report["skipped"].append(
                    {
                        "url": url,
                        "reason": bebee.diagnose_empty_html(html),
                    }
                )
                continue

            self.report["urls_ok"] += 1
            for raw in jobs:
                normalized = normalize_record(raw, source=self.source)
                ext = normalized.get("external_id")
                if ext in seen_external:
                    continue
                seen_external.add(ext)
                records.append(normalized)

        self.report["jobs"] = len(records)
        return records
