from pathlib import Path

from tools import host_opencli_import


def test_resolve_opencli_prefers_explicit_cli_arg(monkeypatch):
    monkeypatch.setattr(
        host_opencli_import.shutil,
        "which",
        lambda name: "/custom/opencli.cmd" if name == "custom-opencli" else None,
    )

    assert host_opencli_import._resolve_opencli("custom-opencli", ["opencli", "boss"]) == "/custom/opencli.cmd"


def test_resolve_opencli_uses_command_name_from_path(monkeypatch):
    monkeypatch.setattr(
        host_opencli_import.shutil,
        "which",
        lambda name: "/usr/local/bin/opencli" if name == "opencli" else None,
    )

    assert host_opencli_import._resolve_opencli(None, ["opencli", "boss"]) == "/usr/local/bin/opencli"


def test_resolve_opencli_skips_placeholder(monkeypatch):
    monkeypatch.setattr(host_opencli_import.shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    assert host_opencli_import._resolve_opencli("C:\\Path\\To\\opencli.cmd", ["opencli", "boss"]) is None


def test_replace_opencli_keeps_non_opencli_command():
    command = ["python", "-m", "some_module"]

    assert host_opencli_import._replace_opencli(command, "/usr/local/bin/opencli") == command
