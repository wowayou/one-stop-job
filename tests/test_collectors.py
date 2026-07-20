import pytest

from backend.app.services import collectors
from backend.app.services.collectors import _build_opencli_command


def test_opencli_placeholder_path_has_actionable_error():
    with pytest.raises(RuntimeError, match="占位值"):
        _build_opencli_command(
            "C:\\Path\\To\\opencli.cmd",
            ["opencli", "boss", "search", "SEO", "--format", "csv"],
        )


def test_opencli_command_name_resolves_from_path(monkeypatch):
    monkeypatch.setattr(collectors.shutil, "which", lambda name: "/usr/local/bin/opencli" if name == "opencli" else None)

    command, use_shell = _build_opencli_command("opencli", ["opencli", "boss", "search", "SEO"])

    assert command == ["/usr/local/bin/opencli", "boss", "search", "SEO"]
    assert use_shell is False


def test_windows_opencli_path_uses_cmd_proxy_in_wsl(monkeypatch):
    monkeypatch.setattr(collectors.os, "name", "posix", raising=False)
    monkeypatch.setattr(collectors.shutil, "which", lambda name: "/mnt/c/Windows/System32/cmd.exe" if name == "cmd.exe" else None)

    command, use_shell = _build_opencli_command(
        "C:\\Users\\YourName\\AppData\\Roaming\\npm\\opencli.cmd",
        ["opencli", "boss", "search", "SEO"],
    )

    assert command == [
        "cmd.exe",
        "/c",
        "C:\\Users\\YourName\\AppData\\Roaming\\npm\\opencli.cmd",
        "boss",
        "search",
        "SEO",
    ]
    assert use_shell is False
