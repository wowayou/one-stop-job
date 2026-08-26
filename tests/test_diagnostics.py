"""诊断与失败恢复（services/diagnostics.py + routers/diagnostics.py）。

重点断言两件事：**密钥一个字节都不许出现在返回里**，以及**这些端点不发任何出站请求**。
不联网：网络一节只汇总 `updates.cached_result()` 这类已有信号，测试里直接摆好缓存。
"""

from __future__ import annotations

import asyncio
import importlib
import sqlite3

import httpx
import pytest

from backend.app.services import diagnostics as diag
from backend.app.services import updates as updates_module


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    from backend.app import config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "general:\n"
        f"  data_dir: {tmp_path / 'data'}\n"
        "ai:\n  enabled: false\n"
        "telegram:\n  enabled: false\n"
        "updates:\n  enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()
    updates_module.clear_cache()
    yield
    config.get_settings.cache_clear()
    updates_module.clear_cache()


# ==================== 脱敏 ====================


def test_redact_removes_known_secret_values_and_common_shapes():
    secret = "sk-live-abcdefghijklmnop"
    text = (
        f"calling provider with key {secret}\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9\n"
        "api_key=另一个不该露的值\n"
        "telegram 123456789:AAEEabcdefghijklmnopqrstuvwxyz012\n"
    )
    cleaned = diag.redact(text, secrets=[secret])
    assert secret not in cleaned
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "另一个不该露的值" not in cleaned
    assert "AAEEabcdefghijklmnopqrstuvwxyz012" not in cleaned


def test_redact_replaces_longer_secrets_first():
    """短值可能是长值的子串：先替换短的会把长值切碎、留下可辨认的尾巴。"""
    long_secret = "sk-abcdef-tail-part"
    short_secret = "sk-abcdef"
    cleaned = diag.redact(f"key={long_secret}", secrets=sorted({long_secret, short_secret}, key=len, reverse=True))
    assert "tail-part" not in cleaned


def test_secret_values_only_collects_secret_named_env_vars(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "value-should-be-collected")
    monkeypatch.setenv("SOME_PLAIN_SETTING", "value-should-not-be-collected")
    values = diag._secret_values()
    assert "value-should-be-collected" in values
    assert "value-should-not-be-collected" not in values


def test_log_tail_redacts_and_reports_absence(monkeypatch, tmp_path):
    log_file = tmp_path / "backend.log"
    monkeypatch.setattr(diag, "_log_path", lambda: log_file)
    assert diag.log_tail()["available"] is False   # 桌面端不落盘日志：明确说明，不是空串

    monkeypatch.setenv("PROBE_API_KEY", "sk-in-the-log-0001")
    log_file.write_text("line one\nusing sk-in-the-log-0001 now\nline three\n", encoding="utf-8")
    result = diag.log_tail(max_lines=2)
    assert result["available"] is True
    assert result["lines"] == 2
    assert "sk-in-the-log-0001" not in result["text"]
    assert "line three" in result["text"]


# ==================== 只读汇总 ====================


def test_runtime_diagnostics_reports_env_names_and_booleans_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-appear-anywhere-0001")
    payload = diag.runtime_diagnostics()

    assert "sk-must-not-appear-anywhere-0001" not in repr(payload)
    ai_group = next(group for group in payload["env"] if group["group"] == "AI")
    key_var = next(item for item in ai_group["vars"] if item["name"] == "OPENAI_API_KEY")
    assert key_var == {"name": "OPENAI_API_KEY", "configured": True}
    assert set(payload) >= {"version", "process", "env", "config", "ai", "data", "network"}
    assert payload["process"]["pid"] > 0


def test_runtime_diagnostics_never_exposes_context_repo_path(monkeypatch, tmp_path):
    """红线 §10：个人上下文仓库的宿主机绝对路径不许出现在 API 返回里。"""
    secret_repo = tmp_path / "personal-context-repo"
    secret_repo.mkdir()
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", str(secret_repo))
    from backend.app import config

    config.get_settings.cache_clear()

    payload = diag.runtime_diagnostics()
    assert "personal-context-repo" not in repr(payload)
    # 但"这个变量配没配"是可以说的（只有布尔）。
    ctx_group = next(group for group in payload["env"] if group["group"] == "个人上下文")
    assert ctx_group["vars"][0]["configured"] is True


def test_network_report_makes_no_outbound_request(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("诊断页不许为了画绿点发起网络请求")

    monkeypatch.setattr(updates_module, "_fetch_releases", boom)
    monkeypatch.setattr(httpx, "Client", boom)

    report = diag.runtime_diagnostics()["network"]
    assert report["probed"] is False
    github = next(item for item in report["signals"] if item["name"].startswith("GitHub"))
    assert github["status"] == "unknown"   # 没检查过就说未知，不猜"正常"


def test_network_report_reuses_cached_update_result(monkeypatch):
    monkeypatch.setattr(updates_module, "_fetch_releases", lambda timeout: [
        {"tag_name": "v9.9.9", "draft": False, "prerelease": False, "html_url": "u", "body": "", "assets": []}
    ])
    updates_module.check_for_updates(force=True)

    github = next(item for item in diag.runtime_diagnostics()["network"]["signals"] if item["name"].startswith("GitHub"))
    assert github["status"] == "ok"
    assert github["checked_at"]


# ==================== 备份 ====================


def test_create_backup_copies_db_and_attachments_without_touching_original(monkeypatch, tmp_path):
    from backend.app import config

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "job_one_stop.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO jobs (id, title) VALUES (1, '独立站运营')")
    conn.commit()
    conn.close()
    (data_dir / "chat_attachments").mkdir()
    (data_dir / "chat_attachments" / "shot.png").write_bytes(b"fake-png")

    monkeypatch.setattr(diag, "PROJECT_DIR", tmp_path)
    config.get_settings.cache_clear()

    result = diag.create_backup()

    assert result["ok"] is True
    from pathlib import Path

    backup_dir = Path(result["path"])
    assert backup_dir.parent == tmp_path / "data" / "backups"
    # 备份出来的库必须真的能读，而不只是文件存在。
    restored = sqlite3.connect(str(backup_dir / "job_one_stop.sqlite3"))
    assert restored.execute("SELECT title FROM jobs WHERE id = 1").fetchone()[0] == "独立站运营"
    restored.close()
    assert (backup_dir / "chat_attachments" / "shot.png").read_bytes() == b"fake-png"
    # 原库原样保留（在线备份不搬走数据）。
    assert db_path.is_file()
    assert result["size_bytes"] > 0


def test_create_backup_reports_when_there_is_no_database(monkeypatch, tmp_path):
    from backend.app import config

    monkeypatch.setattr(diag, "PROJECT_DIR", tmp_path)
    config.get_settings.cache_clear()
    result = diag.create_backup()
    assert result["ok"] is False and result["path"] is None


def test_backup_does_not_delete_or_overwrite_existing_backups(monkeypatch, tmp_path):
    from backend.app import config
    from pathlib import Path

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(data_dir / "job_one_stop.sqlite3"))
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    older = tmp_path / "data" / "backups" / "19990101-000000"
    older.mkdir(parents=True)
    (older / "keepme.txt").write_text("previous backup", encoding="utf-8")

    monkeypatch.setattr(diag, "PROJECT_DIR", tmp_path)
    config.get_settings.cache_clear()
    diag.create_backup()

    assert (older / "keepme.txt").read_text(encoding="utf-8") == "previous backup"
    assert len(list(Path(tmp_path / "data" / "backups").iterdir())) == 2


# ==================== 端点 ====================


def test_endpoints_return_payloads_and_backup_writes_into_data_backups(monkeypatch, tmp_path):
    from backend.app import config

    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / 'endpoint.sqlite3'}")
    config.get_settings.cache_clear()

    import backend.app.db as db
    import backend.app.main as main

    importlib.reload(db).init_db()
    app = importlib.reload(main).app
    monkeypatch.setattr(diag, "PROJECT_DIR", tmp_path)

    async def run() -> tuple[dict, dict, dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            runtime = await client.get("/api/diagnostics/runtime")
            logs = await client.get("/api/diagnostics/logs", params={"lines": 5})
            backup = await client.post("/api/diagnostics/backup")
        return runtime.json(), logs.json(), backup.json()

    runtime_payload, logs_payload, backup_payload = asyncio.run(run())
    assert runtime_payload["version"] == diag.APP_VERSION
    assert "available" in logs_payload
    assert "ok" in backup_payload
