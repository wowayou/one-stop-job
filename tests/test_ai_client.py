"""AI 客户端构造：确保调用超时上限生效，不会用 SDK 默认的约 10 分钟超时卡住请求。

不联网：OpenAI 构造函数本身被替换成一个只记录 kwargs 的桩，不会发起任何网络连接。
"""

from __future__ import annotations

import pytest

from backend.app.services import ai as ai_module


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """测试内会把 get_settings 缓存指向临时 config，结束后必须清掉，避免泄漏给后续测试。"""
    yield
    from backend.app import config

    config.get_settings.cache_clear()


def _reload_settings(monkeypatch, tmp_path, config_yaml: str) -> None:
    from backend.app import config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_yaml, encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()


class _FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeOpenAI.last_kwargs = kwargs


def test_client_passes_configured_timeout(monkeypatch, tmp_path):
    _reload_settings(monkeypatch, tmp_path, "ai:\n  enabled: true\n  timeout_seconds: 12\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)

    ai_module._client()

    assert _FakeOpenAI.last_kwargs.get("timeout") == 12.0


def test_client_defaults_timeout_to_60_when_unset(monkeypatch, tmp_path):
    _reload_settings(monkeypatch, tmp_path, "ai:\n  enabled: true\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)

    ai_module._client()

    assert _FakeOpenAI.last_kwargs.get("timeout") == 60.0
