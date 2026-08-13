from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCORING_WEIGHTS = {
    "role_match": 25,
    "salary_city": 15,
    "growth": 15,
    "stability": 15,
    "reputation": 10,
    "commute_rest": 10,
    "interview_roi": 10,
}


class ConfigError(RuntimeError):
    """Raised when local configuration cannot be parsed safely."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc
    if parsed < 1:
        raise ConfigError(f"{name} must be greater than 0")
    return parsed


def _resolve_project_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def _to_wsl_path(value: str) -> str:
    """在非 Windows(WSL/Linux)上把 `X:\\dir\\sub` 形式的盘符路径转成 `/mnt/x/dir/sub`。

    让同一份 .env 在 Windows 宿主机和 WSL 里都能读到同一个上下文仓库；
    Windows 上原样返回，其它平台仅在识别到盘符路径时转换。
    """
    import re

    if os.name == "nt":
        return value
    # X:\dir 或 X:/dir 盘符路径。
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
    if match:
        drive, rest = match.group(1).lower(), match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"
    # 手误漏了开头斜杠的 `mnt/x/...`（本应是 `/mnt/x/...`）：补回斜杠。
    if re.match(r"^mnt/[A-Za-z]/", value) or re.match(r"^mnt/[A-Za-z]$", value):
        return "/" + value
    return value


def _env_absolute_path(name: str) -> Path | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    raw = value.strip()
    resolved = _to_wsl_path(raw)
    path = Path(resolved).expanduser()
    if not path.is_absolute():
        os_hint = "posix (WSL/Linux)" if os.name != "nt" else "Windows"
        raise ConfigError(
            f"{name} 必须是绝对路径（当前 OS={os_hint}，收到 {raw!r}）。"
            "WSL 示例：/mnt/d/006-Overseas ；Windows 示例：D:\\\\006-Overseas 。"
            "同一物理目录在两种环境下路径写法不同，请按当前运行环境填写。"
        )
    return path.resolve()


def get_config_path() -> Path:
    return _resolve_project_path(os.getenv("JOB_ONE_STOP_CONFIG", PROJECT_DIR / "config.yaml"))


def load_yaml_config() -> dict[str, Any]:
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败：{config_path}。请检查缩进、冒号和引号。") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"配置文件根节点必须是对象：{config_path}")
    return loaded


def save_yaml_config(config: dict[str, Any]) -> None:
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    tmp_path.replace(config_path)


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_url: str
    config: dict[str, Any]
    cors_origins: list[str]
    ai_enabled: bool
    data_dir: Path
    max_upload_bytes: int
    opencli_server_enabled: bool
    host: str
    port: int
    context_repo_path: Path | None
    config_error: str | None = None

    @property
    def opencli_config(self) -> dict[str, Any]:
        value = self.config.get("opencli", {})
        return value if isinstance(value, dict) else {}

    @property
    def scoring_weights(self) -> dict[str, float]:
        scoring = self.config.get("scoring", {})
        configured = scoring.get("weights", {}) if isinstance(scoring, dict) else {}
        configured = configured if isinstance(configured, dict) else {}
        return {**DEFAULT_SCORING_WEIGHTS, **configured}

    @property
    def research_sources(self) -> list[str]:
        research = self.config.get("research", {})
        sources = research.get("source_allowlist") if isinstance(research, dict) else None
        if isinstance(sources, list) and all(isinstance(item, str) and item.strip() for item in sources):
            return sources
        return ["company_site", "job_post", "search", "xhs", "maimai", "kanzhun", "manual_note"]

    @property
    def wechat_config(self) -> dict[str, Any]:
        value = self.config.get("wechat", {})
        return value if isinstance(value, dict) else {}

    @property
    def bebee_config(self) -> dict[str, Any]:
        value = self.config.get("bebee", {})
        return value if isinstance(value, dict) else {}

    @property
    def telegram_config(self) -> dict[str, Any]:
        value = self.config.get("telegram", {})
        return value if isinstance(value, dict) else {}

    @property
    def ingest_config(self) -> dict[str, Any]:
        value = self.config.get("ingest", {})
        return value if isinstance(value, dict) else {}

    @property
    def ai_config(self) -> dict[str, Any]:
        value = self.config.get("ai", {})
        return value if isinstance(value, dict) else {}

    @property
    def ai_timeout_seconds(self) -> float:
        value = self.ai_config.get("timeout_seconds", 60)
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return 60.0
        return seconds if seconds > 0 else 60.0

    @property
    def followup_stale_days(self) -> int:
        followup = self.config.get("followup", {})
        value = followup.get("stale_days") if isinstance(followup, dict) else None
        try:
            days = int(value)
        except (TypeError, ValueError):
            return 5
        return days if days >= 1 else 5

    @property
    def schedule_config(self) -> dict[str, Any]:
        value = self.config.get("schedule", {})
        return value if isinstance(value, dict) else {}


@lru_cache
def get_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    config_errors: list[str] = []
    try:
        config = load_yaml_config()
    except ConfigError as exc:
        config = {}
        config_errors.append(str(exc))

    general = config.get("general", {})
    if not isinstance(general, dict):
        general = {}

    data_dir = _resolve_project_path(general.get("data_dir", PROJECT_DIR / "data"))
    db_url = os.getenv("JOB_ONE_STOP_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        data_dir.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{data_dir / 'job_one_stop.sqlite3'}"

    try:
        max_upload_bytes = _env_int("JOB_ONE_STOP_MAX_UPLOAD_MB", 20) * 1024 * 1024
    except ConfigError as exc:
        config_errors.append(str(exc))
        max_upload_bytes = 20 * 1024 * 1024

    try:
        port = _env_int("PORT", 8000)
    except ConfigError as exc:
        config_errors.append(str(exc))
        port = 8000

    try:
        context_repo_path = _env_absolute_path("JOB_ONE_STOP_CONTEXT_REPO_PATH")
    except ConfigError as exc:
        config_errors.append(str(exc))
        context_repo_path = None

    origins = os.getenv("JOB_ONE_STOP_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return Settings(
        app_name="job-one-stop",
        database_url=db_url,
        config=config,
        cors_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
        ai_enabled=bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL")),
        data_dir=data_dir,
        max_upload_bytes=max_upload_bytes,
        opencli_server_enabled=_env_bool("JOB_ONE_STOP_OPENCLI_SERVER_ENABLED", True),
        host=os.getenv("HOST", "0.0.0.0"),
        port=port,
        context_repo_path=context_repo_path,
        config_error="; ".join(config_errors) if config_errors else None,
    )
