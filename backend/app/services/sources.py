from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from .collectors import (
    BeBeeCollector,
    OpenCLICommandCollector,
    OpenCLIMultiCommandCollector,
    command_with_query,
    inspect_opencli,
)


@dataclass(frozen=True)
class JobSourceDefinition:
    key: str
    label: str
    kind: str
    enabled: bool
    config: dict[str, Any]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _source_configs(settings: Settings) -> dict[str, Any]:
    return _as_dict(settings.config.get("job_sources"))


def list_source_definitions(settings: Settings) -> list[JobSourceDefinition]:
    configured_sources = _source_configs(settings)
    opencli_cfg = settings.opencli_config
    bebee_cfg = settings.bebee_config

    boss_cfg = {
        "command": opencli_cfg.get("boss_cmd", []),
        "keywords": opencli_cfg.get("boss_keywords", []),
        "timeout_seconds": opencli_cfg.get("timeout_seconds", 120),
        **_as_dict(configured_sources.get("boss")),
    }
    zhilian_cfg = {
        "command": opencli_cfg.get("zhilian_cmd", []),
        "timeout_seconds": opencli_cfg.get("timeout_seconds", 120),
        **_as_dict(configured_sources.get("zhilian")),
    }
    structured_bebee_cfg = {
        **bebee_cfg,
        **_as_dict(configured_sources.get("bebee")),
    }
    if "urls" in structured_bebee_cfg and "role_urls" not in structured_bebee_cfg:
        structured_bebee_cfg["role_urls"] = structured_bebee_cfg["urls"]

    return [
        JobSourceDefinition(
            key="boss",
            label=str(boss_cfg.get("label") or "BOSS直聘"),
            kind="opencli_csv",
            enabled=bool(boss_cfg.get("enabled", True)),
            config=boss_cfg,
        ),
        JobSourceDefinition(
            key="zhilian",
            label=str(zhilian_cfg.get("label") or "智联招聘"),
            kind="opencli_csv",
            enabled=bool(zhilian_cfg.get("enabled", False)),
            config=zhilian_cfg,
        ),
        JobSourceDefinition(
            key="bebee",
            label=str(structured_bebee_cfg.get("source_label") or structured_bebee_cfg.get("label") or "beBee"),
            kind="structured_pages",
            enabled=bool(structured_bebee_cfg.get("enabled", True)),
            config=structured_bebee_cfg,
        ),
    ]


def get_source_definition(settings: Settings, source_key: str) -> JobSourceDefinition | None:
    normalized = source_key.lower().strip()
    return next((source for source in list_source_definitions(settings) if source.key == normalized), None)


def source_health(source: JobSourceDefinition) -> dict[str, Any]:
    if source.kind == "opencli_csv":
        if os.getenv("JOB_ONE_STOP_OPENCLI_SERVER_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
            return {
                "configured": False,
                "status": "host_import_required",
                "message": "Docker 模式不在服务端调用 OpenCLI；请在宿主机运行 tools 里的采集脚本，再导入 CSV。",
                "doctor": {
                    "status": "host_import_required",
                    "configured": False,
                    "message": "OpenCLI 与平台登录态保留在宿主机，主服务只接收 CSV/XLSX 导入。",
                    "runtime": "host",
                },
            }
        doctor = inspect_opencli("", _as_list(source.config.get("command")))
        configured = bool(doctor.get("configured")) and bool(source.config.get("command"))
        message = "来源已禁用。" if not source.enabled else str(doctor.get("message") or "")
        return {
            "configured": configured,
            "status": "disabled" if not source.enabled else str(doctor.get("status") or "unknown"),
            "message": message,
            "doctor": doctor,
        }

    if source.kind == "structured_pages":
        urls = _as_list(source.config.get("role_urls"))
        configured = bool(urls)
        if not source.enabled:
            status = "disabled"
            message = "来源已禁用。"
        elif configured:
            status = "ok"
            message = "已配置页面 URL。"
        else:
            status = "not_configured"
            message = "未配置任何页面 URL。"
        return {"configured": configured, "status": status, "message": message, "doctor": None}

    return {"configured": False, "status": "unsupported", "message": f"不支持的来源类型：{source.kind}", "doctor": None}


def build_source_collector(source: JobSourceDefinition):
    if source.kind == "opencli_csv":
        if os.getenv("JOB_ONE_STOP_OPENCLI_SERVER_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
            raise RuntimeError("Docker 模式不在服务端调用 OpenCLI；请在宿主机采集 CSV 后导入。")
        command = _as_list(source.config.get("command"))
        keywords = _as_list(source.config.get("keywords"))
        timeout_seconds = int(source.config.get("timeout_seconds", 120) or 120)
        # 配了多关键词且命令是 `search` 形态时，逐关键词替换查询词并跨命令去重。
        if keywords and "search" in command:
            return OpenCLIMultiCommandCollector(
                opencli_path="",
                commands=[command_with_query(command, keyword) for keyword in keywords],
                timeout_seconds=timeout_seconds,
                source=source.label,
            )
        return OpenCLICommandCollector(
            opencli_path="",
            command=command,
            timeout_seconds=timeout_seconds,
            source=source.label,
        )
    if source.kind == "structured_pages" and source.key == "bebee":
        return BeBeeCollector(urls=_as_list(source.config.get("role_urls")), cfg=source.config, source=source.label)
    raise ValueError(f"不支持的来源类型：{source.kind}")


def source_public_config(source: JobSourceDefinition) -> dict[str, Any]:
    if source.kind == "opencli_csv":
        return {
            "command": _as_list(source.config.get("command")),
            "timeout_seconds": int(source.config.get("timeout_seconds", 120) or 120),
            "host_collection": {
                "script": "tools\\host_collect_zhilian.bat" if source.key == "zhilian" else "tools\\host_collect_boss.bat",
                "message": "在宿主机运行脚本采集并自动导入；如 PATH 找不到 OpenCLI，可给脚本传 --opencli <path>。",
            },
        }
    if source.kind == "structured_pages":
        return {"role_urls": _as_list(source.config.get("role_urls"))}
    return {}
