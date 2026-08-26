"""升级发现（services/updates.py）：语义版本、正式版筛选、安装包匹配与离线口径。

不联网：`_fetch_releases` 一律被桩掉；只有 `test_endpoint_*` 会经过路由层，但同样
桩掉网络。断言重点是三条容易被写错的口径：
1. 版本号按语义比较（`0.10.0 > 0.9.0`），不按字符串。
2. draft / prerelease 一律不算候选。
3. 网络不可达返回 `offline`，绝不误报 `latest`。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.app.services import updates as updates_module


@pytest.fixture(autouse=True)
def _isolated_updates_config(monkeypatch, tmp_path):
    """把 `updates` 段指向临时 config，并清掉进程内缓存（缓存是模块级的，会串味）。"""
    from backend.app import config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "updates:\n  enabled: true\n  repo: acme/demo\n  cache_ttl_hours: 6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()
    updates_module.clear_cache()
    yield
    config.get_settings.cache_clear()
    updates_module.clear_cache()


def _release(tag: str, *, draft: bool = False, prerelease: bool = False, assets: list[dict] | None = None) -> dict:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/acme/demo/releases/tag/{tag}",
        "body": f"notes for {tag}",
        "published_at": "2026-08-26T00:00:00Z",
        "assets": assets or [],
    }


def _asset(name: str, size: int = 1024) -> dict:
    return {"name": name, "size": size, "browser_download_url": f"https://example.invalid/{name}"}


# ==================== 语义版本 ====================


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("0.10.0", "0.9.0", 1),          # 字符串比较会把 0.10.0 判成更小，这里必须是更大
        ("0.9.0", "0.10.0", -1),
        ("1.2.3", "1.2.3", 0),
        ("v1.2.4", "1.2.3", 1),
        ("2.0.0", "1.99.99", 1),
        ("1.0.0", "1.0.0-beta.1", 1),    # 正式版大于同核心的预发布版
        ("1.0.0-beta.2", "1.0.0-beta.1", 1),
        ("1.0.0", None, 1),
        (None, "1.0.0", -1),
        ("not-a-version", "1.0.0", -1),
    ],
)
def test_compare_versions(left, right, expected):
    assert updates_module.compare_versions(left, right) == expected


def test_parse_version_rejects_garbage():
    assert updates_module.parse_version("latest") is None
    assert updates_module.parse_version("") is None
    assert updates_module.parse_version("1.2") is None


# ==================== 只认正式 Release ====================


def test_select_latest_skips_draft_and_prerelease_and_ignores_api_order():
    releases = [
        _release("v0.9.0"),                      # GitHub 按创建时间返回，顺序不能当版本序用
        _release("v1.1.0", draft=True),
        _release("v1.2.0", prerelease=True),
        _release("v0.10.0"),
        _release("not-a-tag"),
    ]
    latest = updates_module.select_latest_release(releases)
    assert latest is not None and latest["tag_name"] == "v0.10.0"


def test_select_latest_returns_none_without_any_stable_release():
    assert updates_module.select_latest_release([_release("v1.0.0", draft=True)]) is None
    assert updates_module.select_latest_release([]) is None
    assert updates_module.select_latest_release(None) is None


# ==================== 平台与安装包 ====================


@pytest.mark.parametrize(
    ("os_name", "machine", "expected_os", "expected_arch", "expected_label"),
    [
        ("win32", "AMD64", "windows", "x64", "Windows x64"),
        ("darwin", "arm64", "macos", "arm64", "macOS Apple Silicon"),
        ("darwin", "x86_64", "macos", "x64", "macOS Intel"),
        ("linux", "x86_64", "linux", "x64", "Linux x64"),
    ],
)
def test_detect_platform(os_name, machine, expected_os, expected_arch, expected_label):
    info = updates_module.detect_platform(os_name, machine)
    assert (info["os"], info["arch"], info["label"]) == (expected_os, expected_arch, expected_label)


def test_match_asset_picks_per_platform_and_arch():
    assets = [
        _asset("job-one-stop_0.3.0_x64_en-US.msi"),
        _asset("job-one-stop_0.3.0_x64-setup.exe"),
        _asset("job-one-stop_0.3.0_aarch64.dmg"),
        _asset("job-one-stop_0.3.0_x64.dmg"),
        _asset("job-one-stop_0.3.0_amd64.AppImage"),
        _asset("job-one-stop_0.3.0_amd64.deb"),
    ]
    windows = updates_module.match_asset(assets, updates_module.detect_platform("win32", "AMD64"))
    mac_arm = updates_module.match_asset(assets, updates_module.detect_platform("darwin", "arm64"))
    mac_intel = updates_module.match_asset(assets, updates_module.detect_platform("darwin", "x86_64"))
    linux = updates_module.match_asset(assets, updates_module.detect_platform("linux", "x86_64"))

    assert windows["name"].endswith(".msi")            # Windows 优先 .msi，不是 -setup.exe
    assert mac_arm["name"] == "job-one-stop_0.3.0_aarch64.dmg"
    assert mac_intel["name"] == "job-one-stop_0.3.0_x64.dmg"
    assert linux["name"].endswith(".AppImage")         # Linux 优先 AppImage，不是 .deb


def test_match_asset_returns_none_when_only_other_arch_is_published():
    """只发了 Intel 包时，Apple Silicon 机器不能被塞一个错架构的 dmg。"""
    assets = [_asset("job-one-stop_0.3.0_x64.dmg")]
    assert updates_module.match_asset(assets, updates_module.detect_platform("darwin", "arm64")) is None


def test_match_asset_accepts_single_arch_neutral_asset():
    assets = [_asset("job-one-stop-0.3.0.AppImage")]
    matched = updates_module.match_asset(assets, updates_module.detect_platform("linux", "x86_64"))
    assert matched is not None and matched["name"].endswith(".AppImage")


def test_find_checksum_asset_prefers_sidecar_then_single_sums_file():
    assets = [
        _asset("job-one-stop_0.3.0_x64_en-US.msi"),
        _asset("job-one-stop_0.3.0_x64_en-US.msi.sha256"),
        _asset("SHA256SUMS-windows.txt"),
    ]
    assert updates_module.find_checksum_asset(assets, "job-one-stop_0.3.0_x64_en-US.msi").endswith(".msi.sha256")
    assert updates_module.find_checksum_asset(assets, "missing.msi").endswith("SHA256SUMS-windows.txt")
    assert updates_module.find_checksum_asset([], "x") is None


def test_find_checksum_asset_declines_to_guess_among_per_platform_sums_files():
    """每个平台各发一份汇总校验和时，没有旁挂文件就不给链接——给错平台的那份比不给更糟。"""
    assets = [
        _asset("job-one-stop_0.3.0_x64.dmg"),
        _asset("SHA256SUMS-macos-arm.txt"),
        _asset("SHA256SUMS-macos-intel.txt"),
    ]
    assert updates_module.find_checksum_asset(assets, "job-one-stop_0.3.0_x64.dmg") is None


# ==================== check_for_updates：状态、缓存、离线 ====================


def test_update_available_carries_download_and_checksum(monkeypatch):
    monkeypatch.setattr(updates_module, "APP_VERSION", "0.3.0")
    monkeypatch.setattr(updates_module, "detect_platform", lambda *a, **k: {"os": "linux", "arch": "x64", "label": "Linux x64"})
    assets = [_asset("job-one-stop_0.4.0_amd64.AppImage"), _asset("job-one-stop_0.4.0_amd64.AppImage.sha256")]
    monkeypatch.setattr(updates_module, "_fetch_releases", lambda timeout: [_release("v0.4.0", assets=assets)])

    result = updates_module.check_for_updates()

    assert result["status"] == "update_available"
    assert (result["current_version"], result["latest_version"]) == ("0.3.0", "0.4.0")
    assert result["download"]["name"].endswith(".AppImage")
    assert result["checksum_url"].endswith(".sha256")
    assert result["release_url"].endswith("v0.4.0")
    # .sha256 是校验文件，不该混进给用户看的资产清单里。
    assert all(not a["name"].endswith(".sha256") for a in result["assets"])


def test_same_version_reports_latest(monkeypatch):
    monkeypatch.setattr(updates_module, "APP_VERSION", "0.4.0")
    monkeypatch.setattr(updates_module, "_fetch_releases", lambda timeout: [_release("v0.4.0")])
    assert updates_module.check_for_updates()["status"] == "latest"


def test_older_remote_version_reports_latest(monkeypatch):
    """本机跑的是尚未发布的开发版时，不能被"降级提示"骚扰。"""
    monkeypatch.setattr(updates_module, "APP_VERSION", "0.5.0")
    monkeypatch.setattr(updates_module, "_fetch_releases", lambda timeout: [_release("v0.4.0")])
    assert updates_module.check_for_updates()["status"] == "latest"


def test_network_error_reports_offline_not_latest(monkeypatch):
    def boom(timeout):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(updates_module, "_fetch_releases", boom)
    result = updates_module.check_for_updates()
    assert result["status"] == "offline"
    assert result["latest_version"] is None
    assert "无法连接" in result["message"]
    # 当前版本在任何状态下都要能显示，否则「关于」面板会空着。
    assert result["current_version"]


@pytest.mark.parametrize(("code", "needle"), [(403, "限流"), (429, "限流"), (404, "updates.repo"), (500, "HTTP 500")])
def test_http_errors_report_error_with_reason(monkeypatch, code, needle):
    def boom(timeout):
        request = httpx.Request("GET", "https://api.github.invalid/x")
        raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(code, request=request))

    monkeypatch.setattr(updates_module, "_fetch_releases", boom)
    result = updates_module.check_for_updates()
    assert result["status"] == "error"
    assert needle in result["message"]


def test_successful_result_is_cached_until_force(monkeypatch):
    calls = {"n": 0}

    def counted(timeout):
        calls["n"] += 1
        return [_release("v9.9.9")]

    monkeypatch.setattr(updates_module, "_fetch_releases", counted)

    first = updates_module.check_for_updates()
    second = updates_module.check_for_updates()
    assert calls["n"] == 1
    assert first["cached"] is False and second["cached"] is True

    updates_module.check_for_updates(force=True)
    assert calls["n"] == 2


def test_failure_cache_expires_much_sooner_than_success_cache(monkeypatch):
    """离线结果只短暂缓存：启动检查与「关于」面板的连续两次调用不该各吃一个超时，
    但也不能像成功结果那样缓存 6 小时，否则网络恢复后要等半天才发现新版本。"""
    assert updates_module.FAILURE_CACHE_SECONDS < updates_module.DEFAULT_CACHE_TTL_HOURS * 3600

    def boom(timeout):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(updates_module, "_fetch_releases", boom)
    updates_module.check_for_updates()
    # 把缓存时间推到失败 TTL 之外，下一次必须重新发请求。
    monkeypatch.setattr(updates_module, "_cached_at", 0.0)
    calls = {"n": 0}

    def counted(timeout):
        calls["n"] += 1
        return [_release("v1.0.0")]

    monkeypatch.setattr(updates_module, "_fetch_releases", counted)
    updates_module.check_for_updates()
    assert calls["n"] == 1


def test_disabled_config_never_touches_network(monkeypatch, tmp_path):
    from backend.app import config

    config_path = tmp_path / "off.yaml"
    config_path.write_text("updates:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()
    updates_module.clear_cache()

    def boom(timeout):
        raise AssertionError("updates.enabled=false 时不该发出任何请求")

    monkeypatch.setattr(updates_module, "_fetch_releases", boom)
    result = updates_module.check_for_updates()
    assert result["status"] == "disabled"
    assert updates_module.check_on_startup() is False


def test_startup_check_respects_check_on_startup_flag(monkeypatch, tmp_path):
    from backend.app import config

    config_path = tmp_path / "no-startup.yaml"
    config_path.write_text("updates:\n  enabled: true\n  check_on_startup: false\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()
    assert updates_module.is_enabled() is True
    assert updates_module.check_on_startup() is False


def test_endpoint_returns_current_version_and_honours_startup_flag(monkeypatch, tmp_path):
    """经路由层跑一遍：startup=true + check_on_startup=false 必须直接回 disabled 且不发请求。"""
    import importlib

    from backend.app import config

    config_path = tmp_path / "endpoint.yaml"
    config_path.write_text("updates:\n  enabled: true\n  check_on_startup: false\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / 'updates.sqlite3'}")
    config.get_settings.cache_clear()

    import backend.app.db as db
    import backend.app.main as main

    importlib.reload(db).init_db()
    app = importlib.reload(main).app

    def boom(timeout):
        raise AssertionError("startup 静默检查关闭时不该发请求")

    monkeypatch.setattr(updates_module, "_fetch_releases", boom)

    async def run() -> tuple[dict, dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            startup = await client.get("/api/updates/check", params={"startup": True})
            health = await client.get("/api/health")
        return startup.json(), health.json()

    startup_payload, health_payload = asyncio.run(run())
    assert startup_payload["status"] == "disabled"
    assert startup_payload["current_version"] == updates_module.APP_VERSION
    # 「本版新增」弹窗靠 /api/health 的 version 判断是否升级后首次启动。
    assert health_payload["version"] == updates_module.APP_VERSION


@pytest.mark.parametrize(
    "configured",
    [
        "acme/demo",
        "acme/demo/",
        "  acme/demo  ",
        "https://github.com/acme/demo",
        "https://github.com/acme/demo.git",
        "git@github.com:acme/demo.git",
        "github.com/acme/demo",
    ],
)
def test_repo_slug_tolerates_pasted_urls_and_git_suffix(monkeypatch, tmp_path, configured):
    """整条粘了仓库网址或带 .git 后缀时也要拼出正确的 API 地址。

    不规整的话只会得到一个 404，而「找不到发布仓库」的提示会让人去查网络而不是查这行配置。
    """
    from backend.app import config

    config_path = tmp_path / "repo.yaml"
    config_path.write_text(f'updates:\n  enabled: true\n  repo: "{configured}"\n', encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()

    assert updates_module._repo() == "acme/demo"
    assert updates_module._releases_url() == "https://api.github.com/repos/acme/demo/releases?per_page=30"


def test_repo_falls_back_to_default_when_blank(monkeypatch, tmp_path):
    from backend.app import config

    config_path = tmp_path / "blank.yaml"
    config_path.write_text('updates:\n  enabled: true\n  repo: "   "\n', encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()

    assert updates_module._repo() == updates_module.DEFAULT_REPO
