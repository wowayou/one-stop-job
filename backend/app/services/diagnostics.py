"""运行时诊断与失败恢复（P3/P4.5）：只读状态汇总、脱敏日志、本地备份。

三条边界：
- **不回传任何密钥值。** `.env` 一律只报「变量名 + 是否有值」的布尔；日志按已知密钥值
  逐一替换后再返回，再叠一层正则兜底。
- **不做新的出站探测。** "网络连接状态"只汇总**已有**信号（升级检查上次的结果、最近一次
  采集运行、Telegram 是否启用），绝不为了画一个绿点去连别人的服务器（红线 §3.9）。
- 个人上下文仓库的绝对路径绝不出现在返回里（红线 §10）；应用自己的 data_dir 会返回——
  那是应用自己的目录，「打开数据目录」按钮要靠它。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_DIR, get_config_path, get_settings
from ..version import APP_VERSION
from . import updates as updates_service
from .ai import active_provider_display, is_ai_available

# 进程启动时刻：用于「当前后端进程」里的运行时长。模块首次 import 即等于进程启动。
_STARTED_AT = time.time()

# `.env` 里值得报「是否已配置」的变量。分组只为让前端能把 .env 与 config.yaml 分开显示。
# 只报布尔，绝不读出值——名单里全是变量名。
ENV_GROUPS: dict[str, tuple[str, ...]] = {
    "AI": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"),
    "通知": ("TELEGRAM_BOT_TOKEN",),
    "运行": ("PORT", "HOST", "JOB_ONE_STOP_DATABASE_URL", "JOB_ONE_STOP_CONFIG", "JOB_ONE_STOP_MAX_UPLOAD_MB"),
    "个人上下文": ("JOB_ONE_STOP_CONTEXT_REPO_PATH",),
    "代理": ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"),
}

# 判定「这个变量名装的是密钥」——脱敏时要把它的值从日志里抹掉。
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.IGNORECASE)

# 正则兜底：即使某个密钥没进环境变量（比如从别处贴进日志的），也不让它原样返回。
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{20,}"), "<telegram-token>"),   # Telegram bot token
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[=:]\s*)\S+"), r"\1\2***"),
)

LOG_TAIL_MAX_LINES = 400
LOG_TAIL_MAX_BYTES = 256 * 1024


def _log_path() -> Path:
    """`scripts/app.sh` 的看门狗把 uvicorn 输出重定向到这里；Tauri 模式下 stdout 继承给
    父进程，不落文件，所以这个路径可能根本不存在——调用方必须按"没有日志"处理。"""
    return PROJECT_DIR / "data" / "app" / "backend.log"


def _secret_values() -> list[str]:
    """当前环境里所有"看起来是密钥"的值，供日志脱敏做精确替换。"""
    values: list[str] = []
    for name, value in os.environ.items():
        if not value or len(value) < 6:
            continue
        if _SECRET_NAME_RE.search(name):
            values.append(value)
    # 长的先替换：短值可能是长值的子串，先换短的会把长值切碎、留下可辨认的尾巴。
    return sorted(set(values), key=len, reverse=True)


def redact(text: str, secrets: list[str] | None = None) -> str:
    """把已知密钥值与常见密钥形态从文本里抹掉。先精确替换，再上正则兜底。"""
    cleaned = text
    for value in secrets if secrets is not None else _secret_values():
        cleaned = cleaned.replace(value, "***")
    for pattern, replacement in _REDACTION_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def log_tail(max_lines: int = LOG_TAIL_MAX_LINES) -> dict[str, Any]:
    """返回脱敏后的日志尾部，供设置页「复制脱敏日志」使用。

    只读最后 `LOG_TAIL_MAX_BYTES` 字节（日志会滚动到 10MB，全读没意义也很慢），
    再取最后 `max_lines` 行。日志不存在时明确说明原因而不是回空串——Tauri 模式下
    后端 stdout 由父进程继承，本来就不落文件，那不是故障。
    """
    path = _log_path()
    lines = max(1, min(int(max_lines or LOG_TAIL_MAX_LINES), LOG_TAIL_MAX_LINES))
    if not path.is_file():
        return {
            "available": False,
            "message": "没有日志文件。桌面端（Tauri）后端输出继承给父进程、不落盘；"
            "单进程部署（scripts/app.sh）才会写 data/app/backend.log。",
            "lines": 0,
            "text": "",
        }
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > LOG_TAIL_MAX_BYTES:
                f.seek(size - LOG_TAIL_MAX_BYTES)
                f.readline()  # 丢掉被切开的半行
            raw = f.read()
    except OSError as exc:
        return {"available": False, "message": f"日志读取失败：{exc.__class__.__name__}", "lines": 0, "text": ""}

    text = raw.decode("utf-8", errors="replace")
    tail = text.splitlines()[-lines:]
    redacted = redact("\n".join(tail))
    return {
        "available": True,
        "message": f"已脱敏（密钥值与常见密钥形态已替换为 ***），取最后 {len(tail)} 行。",
        "lines": len(tail),
        "text": redacted,
    }


def _env_report() -> list[dict[str, Any]]:
    """`.env` / 环境变量一侧：只有变量名与「是否有值」，没有任何值。"""
    return [
        {
            "group": group,
            "vars": [{"name": name, "configured": bool((os.getenv(name) or "").strip())} for name in names],
        }
        for group, names in ENV_GROUPS.items()
    ]


def _config_report() -> dict[str, Any]:
    """`config.yaml` 一侧：路径、可读性、以及各功能段是否存在（不回显段内容）。"""
    settings = get_settings()
    config_path = get_config_path()
    sections = [
        "opencli", "job_sources", "general", "research", "wechat", "bebee",
        "collect", "scoring", "followup", "ai", "ingest", "telegram",
        "schedule", "automation", "reach", "updates",
    ]
    return {
        "path": str(config_path),
        "exists": config_path.exists(),
        "error": settings.config_error,
        "sections": [{"name": name, "present": isinstance(settings.config.get(name), dict)} for name in sections],
    }


def _process_report() -> dict[str, Any]:
    """当前后端进程：pid / Python / 绑定端口 / 是否由桌面端拉起。

    `tauri_mode` 来自 Rust 侧启动时设的 `JOB_ONE_STOP_TAURI_MODE`；桌面端每次启动都随机
    选一个空闲回环端口（避免误连已有的 :8000），所以「当前 Tauri 后端端口」就是这里的 port。
    """
    settings = get_settings()
    uptime = max(0.0, time.time() - _STARTED_AT)
    return {
        "pid": os.getpid(),
        "python": platform.python_version(),
        "executable_frozen": bool(getattr(sys, "frozen", False)),  # True = PyInstaller 打包的桌面端后端
        "platform": f"{platform.system()} {platform.release()}",
        "host": settings.host,
        "port": settings.port,
        "tauri_mode": bool((os.getenv("JOB_ONE_STOP_TAURI_MODE") or "").strip()),
        "started_at": datetime.fromtimestamp(_STARTED_AT).isoformat(timespec="seconds"),
        "uptime_seconds": int(uptime),
    }


def _data_dir_report() -> dict[str, Any]:
    """应用自己的数据目录（「打开数据目录」按钮要用它的绝对路径）。

    这里回绝对路径是有意的：那是应用自己创建、只属于本机用户的目录。红线 §10 禁止回传的是
    **个人上下文仓库**的宿主机路径，那个由 `ContextRepository.status()` 单独处理，不在这里。
    """
    settings = get_settings()
    data_dir = settings.data_dir
    backups = PROJECT_DIR / "data" / "backups"
    return {
        "path": str(data_dir),
        "exists": data_dir.is_dir(),
        "writable": os.access(data_dir if data_dir.is_dir() else data_dir.parent, os.W_OK),
        "log_path": str(_log_path()),
        "log_exists": _log_path().is_file(),
        "backups_path": str(backups),
        "backup_count": len([p for p in backups.iterdir() if p.is_dir()]) if backups.is_dir() else 0,
    }


def _ai_report() -> dict[str, Any]:
    """当前 AI Provider：会先用哪张卡、模型、是否具备调用条件。只有布尔与展示名。"""
    settings = get_settings()
    ai_cfg = settings.ai_config
    active = active_provider_display()
    providers = ai_cfg.get("providers")
    return {
        "enabled_in_config": bool(ai_cfg.get("enabled")),
        "available": bool(ai_cfg.get("enabled")) and is_ai_available(),
        "provider_label": active["label"],
        "model": active["model"],
        "api_key_configured": active["api_key_configured"],
        "base_url_configured": active["base_url_configured"],
        "provider_count": len(providers) if isinstance(providers, list) else 0,
        "timeout_seconds": settings.ai_timeout_seconds,
    }


def _network_report() -> dict[str, Any]:
    """网络连接状态：**只汇总已有信号，不发任何新请求。**

    画一个"网络正常"的绿点需要去连别人的服务器；这里刻意不做（红线 §3.9 出站请求要低频且
    有明确用途）。能说的是：升级检查上一次的结果（GitHub 可达性的现成证据）、最近一次采集
    运行的成败、代理是否配了、Telegram 是否启用。判断不了的一律写"未知"，不猜。
    """
    settings = get_settings()
    telegram_cfg = settings.telegram_config
    cached = updates_service.cached_result()
    signals: list[dict[str, Any]] = [
        {
            "name": "GitHub（升级检查）",
            "status": {
                "latest": "ok", "update_available": "ok",
                "offline": "error", "error": "warning", "disabled": "unknown",
            }.get(str(cached.get("status")) if cached else "", "unknown"),
            "detail": (cached or {}).get("message") or "本次运行还没检查过更新，打开「关于」或点「检查更新」即可。",
            "checked_at": (cached or {}).get("checked_at"),
        },
        {
            "name": "出站代理",
            "status": "ok" if (os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")) else "unknown",
            "detail": "已配置 HTTP(S)_PROXY（Telegram 等被污染的域名靠它出站）。"
            if (os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY"))
            else "未配置代理。国内直连 api.telegram.org 常见 Network is unreachable。",
            "checked_at": None,
        },
        {
            "name": "Telegram 通知",
            "status": "ok" if telegram_cfg.get("enabled") and os.getenv("TELEGRAM_BOT_TOKEN") else "unknown",
            "detail": "已启用并配置了 Bot Token（只向白名单 chat 发本人回执）。"
            if telegram_cfg.get("enabled") and os.getenv("TELEGRAM_BOT_TOKEN")
            else "未启用或未配置 TELEGRAM_BOT_TOKEN。",
            "checked_at": None,
        },
    ]
    return {"probed": False, "note": "以下只汇总已有信号，本页面不会为了检测而发起任何网络请求。", "signals": signals}


def runtime_diagnostics() -> dict[str, Any]:
    """诊断页要的全部只读信息：版本 / 进程 / .env / config.yaml / AI / 数据目录 / 网络信号。"""
    return {
        "version": APP_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "process": _process_report(),
        "env": _env_report(),
        "config": _config_report(),
        "ai": _ai_report(),
        "data": _data_dir_report(),
        "network": _network_report(),
    }


def create_backup() -> dict[str, Any]:
    """把 SQLite 在线备份 + 聊天附件复制到 `data/backups/<时间戳>/`。

    口径与 `scripts/app.sh backup` **保持一致**（同样的目录布局、同样用 SQLite 的
    `Connection.backup()`），这样两条入口产出的备份可以互相还原。只新建文件，
    不删除、不覆盖既有备份，也不动 `data/job_one_stop/` 里的原库。
    """
    settings = get_settings()
    db_path = settings.data_dir / "job_one_stop.sqlite3"
    if not db_path.is_file():
        return {"ok": False, "message": f"尚无数据可备份（未找到 {db_path.name}）。", "path": None, "size_bytes": 0}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROJECT_DIR / "data" / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Connection.backup() 是并发安全的在线备份：后端正在跑也能备，不需要停服。
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_dir / "job_one_stop.sqlite3"))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    attachments = settings.data_dir / "chat_attachments"
    if attachments.is_dir():
        shutil.copytree(attachments, backup_dir / "chat_attachments", dirs_exist_ok=True)

    size = sum(p.stat().st_size for p in backup_dir.rglob("*") if p.is_file())
    return {
        "ok": True,
        "message": f"备份完成：data/backups/{stamp}",
        "path": str(backup_dir),
        "size_bytes": size,
        "restore_hint": "还原：先停后端，把备份目录里的 job_one_stop.sqlite3（和 chat_attachments/，如有）"
        "复制回数据目录，再启动。",
    }
