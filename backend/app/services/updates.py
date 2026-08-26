"""升级发现（P0）：查 GitHub Releases，判断本机是否有新版本可下载。

**只发现，不安装。** 本模块不下载安装包、不改文件、不重启进程——它唯一的副作用是
一次出站 GET（`api.github.com`，公开只读接口，不带任何凭据和个人数据）。一键更新
需要代码签名与 updater 公钥，属于后续阶段，这里刻意不做。

放在后端而不是前端的原因（不是随手选的）：桌面端 CSP 只放行
`connect-src 'self' http://127.0.0.1:* http://localhost:*`（见 tauri.conf.json），
webview 直接 fetch `api.github.com` 会被拦；而 CLAUDE.md §3.9 也要求出站请求统一走
带超时/UA/限速的 httpx。顺带的好处是平台与架构由后端用 `sys.platform` /
`platform.machine()` 直接判定，不用在 webview 里猜 UA。

口径（对应 P0 的硬要求）：
- 语义版本比较，绝不按字符串比（`0.10.0` > `0.9.0`）。
- 只认正式 Release：`draft` 与 `prerelease` 一律跳过。
- 结果本地缓存，默认 6 小时内不重复请求；手动检查 `force=True` 绕过缓存。
- 离线与"已是最新"必须区分：网络不可达返回 `status="offline"`，绝不误报 `latest`。
"""

from __future__ import annotations

import logging
import platform
import re
import sys
import time
from typing import Any

import httpx

from ..config import get_settings
from ..version import APP_VERSION

logger = logging.getLogger(__name__)

USER_AGENT = f"job-one-stop/{APP_VERSION} (+update-check)"
DEFAULT_REPO = "wowayou/one-stop-job"
DEFAULT_API_BASE = "https://api.github.com"
DEFAULT_CACHE_TTL_HOURS = 6.0
DEFAULT_TIMEOUT_SECONDS = 10.0
# 失败结果也短暂缓存：启动静默检查与「关于」面板挂载会在几秒内各查一次，离线时
# 不该让用户连吃两个超时。手动「检查更新」走 force=True，不受这一层影响。
FAILURE_CACHE_SECONDS = 300.0
RELEASE_NOTES_MAX_CHARS = 4000

_PRERELEASE_SEP = re.compile(r"[.\-]")
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+](.+))?$")


# ==================== 纯函数：版本比较 ====================


def parse_version(value: str | None) -> tuple[tuple[int, int, int], tuple[Any, ...]] | None:
    """把 `v1.2.3` / `1.2.3-beta.1` 解析成 `((1,2,3), 预发布标识)`；解析不了返回 None。

    预发布标识按 semver 规则拆成可比较的元组：数字段按数值比，其它按字符串比，
    空元组（正式版）在比较时要排在任何预发布之**后**（见 `compare_versions`）。
    """
    if not value:
        return None
    match = _VERSION_RE.match(str(value).strip())
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    core = (int(major), int(minor), int(patch))
    if not suffix:
        return core, ()
    parts: list[Any] = []
    for chunk in _PRERELEASE_SEP.split(suffix):
        if not chunk:
            continue
        # (0, 数字) 排在 (1, 字符串) 之前，避免 int 与 str 直接比较报 TypeError。
        parts.append((0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk))
    return core, tuple(parts)


def compare_versions(left: str | None, right: str | None) -> int:
    """语义版本比较：left > right 返回 1，相等 0，left < right -1；无法解析的一侧算更小。"""
    a, b = parse_version(left), parse_version(right)
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    if a[0] != b[0]:
        return 1 if a[0] > b[0] else -1
    # 核心版本相同：正式版（空预发布）大于任何预发布版本。
    if not a[1] and not b[1]:
        return 0
    if not a[1]:
        return 1
    if not b[1]:
        return -1
    return 0 if a[1] == b[1] else (1 if a[1] > b[1] else -1)


def select_latest_release(releases: Any) -> dict[str, Any] | None:
    """从 GitHub releases 列表里挑出版本号最大的**正式** Release。

    跳过 draft 与 prerelease（P0 硬要求）；GitHub 的返回顺序按创建时间，不能当版本序用，
    所以这里自己按语义版本取最大值。
    """
    if not isinstance(releases, list):
        return None
    best: dict[str, Any] | None = None
    for item in releases:
        if not isinstance(item, dict):
            continue
        if item.get("draft") or item.get("prerelease"):
            continue
        if parse_version(item.get("tag_name")) is None:
            continue
        if best is None or compare_versions(item.get("tag_name"), best.get("tag_name")) > 0:
            best = item
    return best


# ==================== 纯函数：平台与安装包匹配 ====================


def detect_platform(os_name: str | None = None, machine: str | None = None) -> dict[str, str]:
    """判定本机平台与架构，用于挑对应的安装包（参数只为测试注入，生产走 sys/platform）。"""
    raw_os = (os_name or sys.platform).lower()
    raw_machine = (machine or platform.machine() or "").lower()
    if raw_os.startswith("win"):
        os_key = "windows"
    elif raw_os == "darwin" or raw_os.startswith("mac"):
        os_key = "macos"
    elif raw_os.startswith("linux"):
        os_key = "linux"
    else:
        os_key = "unknown"
    arch = "arm64" if raw_machine in {"arm64", "aarch64"} else ("x64" if raw_machine in {"x86_64", "amd64", "x64"} else raw_machine or "unknown")
    labels = {
        ("windows", "x64"): "Windows x64",
        ("windows", "arm64"): "Windows ARM64",
        ("macos", "arm64"): "macOS Apple Silicon",
        ("macos", "x64"): "macOS Intel",
        ("linux", "x64"): "Linux x64",
        ("linux", "arm64"): "Linux ARM64",
    }
    return {
        "os": os_key,
        "arch": arch,
        "label": labels.get((os_key, arch), f"{os_key} {arch}"),
    }


# 每个平台按优先级列出「后缀 -> 架构关键字」；架构关键字用来在同后缀的多个资产里挑对的那个。
_SUFFIX_PRIORITY: dict[str, tuple[str, ...]] = {
    "windows": (".msi", "-setup.exe", ".exe"),
    "macos": (".dmg", ".app.tar.gz"),
    "linux": (".appimage", ".deb", ".rpm"),
}
_ARCH_TOKENS: dict[str, tuple[str, ...]] = {
    "x64": ("x64", "x86_64", "amd64"),
    "arm64": ("aarch64", "arm64"),
}


def match_asset(assets: Any, platform_info: dict[str, str]) -> dict[str, Any] | None:
    """在 Release 资产里挑出与本机平台/架构匹配的安装包；挑不到返回 None（前端只显示下载页）。

    先按后缀优先级分组（Windows 优先 .msi），再在组内按架构关键字过滤；组内只有一个
    候选且没有任何架构关键字时（单架构发布）直接采用，避免因为命名里没写架构就漏掉。
    """
    if not isinstance(assets, list):
        return None
    usable = [a for a in assets if isinstance(a, dict) and isinstance(a.get("name"), str) and a.get("browser_download_url")]
    if not usable:
        return None
    tokens = _ARCH_TOKENS.get(platform_info.get("arch", ""), ())
    other_tokens = tuple(t for arch, group in _ARCH_TOKENS.items() if arch != platform_info.get("arch") for t in group)
    for suffix in _SUFFIX_PRIORITY.get(platform_info.get("os", ""), ()):
        group = [a for a in usable if a["name"].lower().endswith(suffix)]
        if not group:
            continue
        exact = [a for a in group if any(token in a["name"].lower() for token in tokens)]
        if exact:
            return exact[0]
        # 没有本架构关键字：只有在其余候选也都没写「别的架构」时才敢兜底（单架构发布）。
        neutral = [a for a in group if not any(token in a["name"].lower() for token in other_tokens)]
        if len(neutral) == 1:
            return neutral[0]
    return None


def find_checksum_asset(assets: Any, asset_name: str | None) -> str | None:
    """找出校验和文件的下载地址：优先该安装包的 `<name>.sha256`，否则汇总的 SHA256SUMS 文件。

    汇总文件按平台各发一份（`SHA256SUMS-windows.txt` 等），所以**只有恰好一份时才兜底**：
    发了多份却无从判断是哪一份属于本机时，宁可不给链接，也不要把别的平台的校验和列表
    塞给用户——那比没有链接更容易让人以为文件被篡改了。
    """
    if not isinstance(assets, list):
        return None
    if asset_name:
        for item in assets:
            if isinstance(item, dict) and item.get("name") == f"{asset_name}.sha256":
                return item.get("browser_download_url")
    sums = [
        item
        for item in assets
        if isinstance(item, dict) and str(item.get("name", "")).upper().startswith("SHA256SUMS")
    ]
    return sums[0].get("browser_download_url") if len(sums) == 1 else None


def asset_payload(asset: dict[str, Any] | None) -> dict[str, Any] | None:
    if not asset:
        return None
    return {
        "name": asset.get("name"),
        "url": asset.get("browser_download_url"),
        "size": asset.get("size"),
    }


# ==================== 配置 + 缓存 ====================

_cache: dict[str, Any] | None = None
_cached_at: float = 0.0


def clear_cache() -> None:
    """清掉进程内缓存（测试与配置变更后调用）。"""
    global _cache, _cached_at
    _cache, _cached_at = None, 0.0


def _updates_config() -> dict[str, Any]:
    return get_settings().updates_config


def _float_option(cfg: dict[str, Any], key: str, default: float) -> float:
    try:
        parsed = float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def is_enabled() -> bool:
    cfg = _updates_config()
    return bool(cfg.get("enabled", True))


def check_on_startup() -> bool:
    cfg = _updates_config()
    return bool(cfg.get("enabled", True)) and bool(cfg.get("check_on_startup", True))


def _repo() -> str:
    """取 `updates.repo` 并规整成 `owner/name`。

    容忍最常见的两种手误：整条粘了仓库网址（`https://github.com/owner/name`）和带 `.git`
    后缀（`owner/name.git`）。不规整的话拼出来的 API 地址会得到一个语焉不详的 404，
    而"找不到发布仓库"这类提示只会让人反复检查网络而不是检查这一行配置。
    """
    value = _updates_config().get("repo")
    if not isinstance(value, str) or not value.strip():
        return DEFAULT_REPO
    slug = value.strip()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:", "github.com/"):
        if slug.lower().startswith(prefix.lower()):
            slug = slug[len(prefix) :]
            break
    slug = slug.strip("/")
    if slug.lower().endswith(".git"):
        slug = slug[: -len(".git")]
    return slug or DEFAULT_REPO


def _releases_url() -> str:
    base = _updates_config().get("api_base")
    base = str(base).rstrip("/") if isinstance(base, str) and base.strip() else DEFAULT_API_BASE
    return f"{base}/repos/{_repo()}/releases?per_page=30"


def _base_result(status: str, message: str) -> dict[str, Any]:
    """所有分支共用的结果骨架：当前版本与平台在任何状态下都要能显示。"""
    return {
        "status": status,
        "message": message,
        "current_version": APP_VERSION,
        "latest_version": None,
        "release_url": None,
        "release_notes": None,
        "published_at": None,
        "download": None,
        "checksum_url": None,
        "assets": [],
        "platform": detect_platform(),
        "repo": _repo(),
        "checked_at": time.time(),
        "cached": False,
    }


def disabled_result(message: str) -> dict[str, Any]:
    """给调用方（路由层）用的「已关闭」结果，避免外部直接摸 `_base_result`。"""
    return _base_result("disabled", message)


def _fetch_releases(timeout: float) -> Any:
    """出站 GET GitHub Releases（公开只读，无凭据、无个人数据）。异常原样抛给调用方归类。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(_releases_url(), headers=headers)
        response.raise_for_status()
        return response.json()


def _build_result(releases: Any) -> dict[str, Any]:
    latest = select_latest_release(releases)
    if latest is None:
        result = _base_result("error", "更新服务没有返回可用的正式版本。")
        return result

    tag = str(latest.get("tag_name") or "")
    latest_version = tag.lstrip("vV")
    assets = latest.get("assets")
    platform_info = detect_platform()
    matched = match_asset(assets, platform_info)
    newer = compare_versions(latest_version, APP_VERSION) > 0

    result = _base_result(
        "update_available" if newer else "latest",
        f"发现新版本 v{latest_version}。" if newer else f"已是最新版本 v{APP_VERSION}。",
    )
    result.update(
        {
            "latest_version": latest_version,
            "release_url": latest.get("html_url"),
            "release_notes": (latest.get("body") or "")[:RELEASE_NOTES_MAX_CHARS] or None,
            "published_at": latest.get("published_at"),
            "download": asset_payload(matched),
            "checksum_url": find_checksum_asset(assets, matched.get("name") if matched else None),
            "assets": [
                payload
                for payload in (
                    asset_payload(a)
                    for a in (assets if isinstance(assets, list) else [])
                    if isinstance(a, dict) and not str(a.get("name", "")).endswith(".sha256")
                )
                if payload
            ],
            "platform": platform_info,
        }
    )
    return result


def check_for_updates(*, force: bool = False) -> dict[str, Any]:
    """返回升级检查结果。`force=True` 是手动点「检查更新」，绕过缓存。

    状态取值：`disabled` / `update_available` / `latest` / `offline` / `error`。
    `offline` 与 `latest` 必须分得开——网络不通时绝不能显示「已是最新」。
    """
    global _cache, _cached_at

    if not is_enabled():
        return _base_result("disabled", "升级检查已在 config.yaml 里关闭（updates.enabled=false）。")

    cfg = _updates_config()
    if not force and _cache is not None:
        ttl = FAILURE_CACHE_SECONDS if _cache.get("status") in {"offline", "error"} else _float_option(cfg, "cache_ttl_hours", DEFAULT_CACHE_TTL_HOURS) * 3600
        if time.time() - _cached_at < ttl:
            return {**_cache, "cached": True}

    timeout = _float_option(cfg, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    try:
        releases = _fetch_releases(timeout)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in {403, 429}:
            message = "更新服务暂时限流（GitHub API 速率限制），请稍后再试。"
        elif code == 404:
            message = f"找不到发布仓库 {_repo()}，请检查 config.yaml 的 updates.repo。"
        else:
            message = f"更新服务返回错误（HTTP {code}）。"
        result = _base_result("error", message)
    except httpx.RequestError:
        logger.info("升级检查网络不可达", exc_info=True)
        result = _base_result("offline", "无法连接更新服务（网络不可达或被代理拦截），未能确认是否有新版本。")
    except ValueError:
        result = _base_result("error", "更新服务返回了无法解析的内容。")
    else:
        try:
            result = _build_result(releases)
        except Exception:  # noqa: BLE001 - 解析异常不应影响主流程
            logger.warning("升级检查解析失败", exc_info=True)
            result = _base_result("error", "解析更新信息失败，请稍后重试或直接打开发布页。")

    _cache, _cached_at = result, time.time()
    return dict(result)
