"""AI 客户端构造：确保调用超时上限生效，不会用 SDK 默认的约 10 分钟超时卡住请求。

不联网：OpenAI 构造函数本身被替换成一个只记录 kwargs 的桩，不会发起任何网络连接。
"""

from __future__ import annotations

import logging

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


def _unset_env(monkeypatch, *names: str) -> None:
    """把变量置空而不是 delenv。

    `get_settings()` 每次都 `load_dotenv(PROJECT_DIR/".env")`，被 `delenv` 删掉的变量
    会立刻从开发机 `.env` 里重新填上，于是"没配 key"的用例反而拿到真 key。置空则不同：
    load_dotenv 不覆盖已存在的变量，而读取侧一律把空串当未配置。
    """
    for name in names:
        monkeypatch.setenv(name, "")


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


# ==================== 多 provider 自动容错 ====================
# 下面这组测试针对 ai.py 的 `_providers()` / `_chat()`：config.yaml 里 `ai.providers`
# 是可选的候选列表，密钥各进不同 .env 变量；`_chat` 按序尝试，单个 provider 失败先
# 退避重试有限次数再切下一个，全部失败才抛出最后一个异常交调用方走既有降级路径。


class _FakeResp:
    def __init__(self, content: str):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeCompletions:
    """完成接口的最小桩：`create(**kwargs)` 委托给一个可控的 behavior 回调。"""

    def __init__(self, behavior):
        self._behavior = behavior

    def create(self, **kwargs):
        return self._behavior(**kwargs)


class _FakeChat:
    def __init__(self, behavior):
        self.completions = _FakeCompletions(behavior)


class _KeyRoutedOpenAI:
    """依构造时的 api_key 分流行为的桩：不同 provider（不同 api_key）各自的调用结果由
    测试用例通过 `behaviors[api_key]` 指定；`calls` 记录每次构造用的 api_key，用于断言
    重试/切换次数。测试之间通过 `reset()` 清空，避免状态泄漏。
    """

    behaviors: dict[str, object] = {}
    calls: list[str] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        api_key = kwargs.get("api_key")
        _KeyRoutedOpenAI.calls.append(api_key)
        self.chat = _FakeChat(_KeyRoutedOpenAI.behaviors[api_key])

    @classmethod
    def reset(cls) -> None:
        cls.behaviors = {}
        cls.calls = []


def _no_sleep(monkeypatch, recorder: list[float] | None = None) -> None:
    """把 ai.py 里的 `time.sleep` 换成记录调用而不真睡的桩，测试不为退避实际等待。"""

    def fake_sleep(seconds: float) -> None:
        if recorder is not None:
            recorder.append(seconds)

    monkeypatch.setattr(ai_module.time, "sleep", fake_sleep)


_PROVIDERS_CONFIG_YAML = (
    "ai:\n"
    "  enabled: true\n"
    "  providers:\n"
    "    - api_key_env: OPENAI_API_KEY\n"
    "      model: gpt-first\n"
    "    - api_key_env: OPENAI_API_KEY_BACKUP\n"
    "      model: gpt-second\n"
)


def test_providers_defaults_to_single_openai_env_when_not_configured(monkeypatch, tmp_path):
    """不配置 ai.providers 时回退现状：单一 provider，client_factory 就是 `_client` 本身。"""
    _reload_settings(monkeypatch, tmp_path, "ai:\n  enabled: true\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _unset_env(monkeypatch, "OPENAI_MODEL", "OPENAI_BASE_URL")

    providers = ai_module._providers()

    assert len(providers) == 1
    assert providers[0]["client_factory"] is ai_module._client
    assert providers[0]["model"] == "gpt-4o-mini"


def test_providers_empty_when_no_key_configured_anywhere(monkeypatch, tmp_path):
    _reload_settings(monkeypatch, tmp_path, "ai:\n  enabled: true\n")
    _unset_env(monkeypatch, "OPENAI_API_KEY", "OPENAI_BASE_URL")

    assert ai_module._providers() == []
    assert ai_module.is_ai_available() is False


def test_providers_normalizes_configured_list_and_skips_missing_keys(monkeypatch, tmp_path):
    """配置了 3 个 provider，其中一个的 api_key_env 没有对应值：应被跳过，不回退单一 provider。"""
    _reload_settings(
        monkeypatch,
        tmp_path,
        "ai:\n"
        "  enabled: true\n"
        "  timeout_seconds: 30\n"
        "  providers:\n"
        "    - api_key_env: OPENAI_API_KEY\n"
        "      base_url_env: OPENAI_BASE_URL\n"
        "      model_env: OPENAI_MODEL_PRIMARY\n"
        "      model: should-be-overridden-by-model-env\n"
        "    - api_key_env: OPENAI_API_KEY_MISSING\n"
        "      model: unreachable\n"
        "    - api_key_env: OPENAI_API_KEY_BACKUP\n"
        "      timeout_seconds: 5\n",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-primary")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("OPENAI_MODEL_PRIMARY", "model-from-env")
    _unset_env(monkeypatch, "OPENAI_MODEL", "OPENAI_API_KEY_MISSING")  # 全局默认不设置，验证兜底 gpt-4o-mini
    monkeypatch.setenv("OPENAI_API_KEY_BACKUP", "sk-backup")

    providers = ai_module._providers()

    assert len(providers) == 2  # 中间那个缺 key 的被跳过
    assert providers[0]["model"] == "model-from-env"  # model_env 优先于字面量 model
    assert providers[1]["model"] == "gpt-4o-mini"  # 没给 model/model_env 也没有全局 OPENAI_MODEL，回退默认值

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    client_1 = providers[0]["client_factory"]()
    client_2 = providers[1]["client_factory"]()
    assert client_1.kwargs["api_key"] == "sk-primary"
    assert client_1.kwargs["base_url"] == "https://primary.example/v1"
    assert client_1.kwargs["timeout"] == 30.0  # 未单独设置，回退顶层 timeout_seconds
    assert client_2.kwargs["api_key"] == "sk-backup"
    assert client_2.kwargs["timeout"] == 5.0  # 该项自带 timeout_seconds，优先于顶层
    assert "base_url" not in client_2.kwargs


def test_is_ai_available_true_when_any_configured_provider_has_key(monkeypatch, tmp_path):
    _reload_settings(
        monkeypatch,
        tmp_path,
        "ai:\n"
        "  enabled: true\n"
        "  providers:\n"
        "    - api_key_env: OPENAI_API_KEY_MISSING\n"
        "    - api_key_env: OPENAI_API_KEY_BACKUP\n",
    )
    monkeypatch.delenv("OPENAI_API_KEY_MISSING", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_BACKUP", "sk-backup")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert ai_module.is_ai_available() is True


def test_chat_uses_single_default_provider_when_no_providers_configured(monkeypatch, tmp_path):
    """端到端确认：不配置 ai.providers 时，`_chat` 仍走单一 OPENAI_* 环境变量。"""
    _reload_settings(monkeypatch, tmp_path, "ai:\n  enabled: true\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "solo-model")

    _KeyRoutedOpenAI.reset()
    _KeyRoutedOpenAI.behaviors = {"sk-test": lambda **_kw: _FakeResp("hello")}
    monkeypatch.setattr("openai.OpenAI", _KeyRoutedOpenAI)
    _no_sleep(monkeypatch)

    content = ai_module._chat("sys", "user")

    assert content == "hello"
    assert _KeyRoutedOpenAI.calls == ["sk-test"]


def test_chat_falls_back_to_next_provider_after_retries_with_backoff(monkeypatch, tmp_path):
    _reload_settings(monkeypatch, tmp_path, _PROVIDERS_CONFIG_YAML)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-first")
    monkeypatch.setenv("OPENAI_API_KEY_BACKUP", "sk-second")

    def first_boom(**_kw):
        raise RuntimeError("first provider unreachable")

    _KeyRoutedOpenAI.reset()
    _KeyRoutedOpenAI.behaviors = {
        "sk-first": first_boom,
        "sk-second": lambda **_kw: _FakeResp('{"jobs": []}'),
    }
    monkeypatch.setattr("openai.OpenAI", _KeyRoutedOpenAI)

    sleeps: list[float] = []
    _no_sleep(monkeypatch, sleeps)

    content = ai_module._chat("sys", "user")

    assert content == '{"jobs": []}'
    # 第一个 provider 首次 + 重试用尽（_MAX_RETRIES_PER_PROVIDER 次）才换到第二个，
    # 第二个一次就成功。
    expected_first_attempts = ai_module._MAX_RETRIES_PER_PROVIDER + 1
    assert _KeyRoutedOpenAI.calls == ["sk-first"] * expected_first_attempts + ["sk-second"]
    assert sleeps == list(ai_module._RETRY_BACKOFF_SECONDS)


def test_chat_raises_after_all_providers_exhausted_and_never_logs_raw_key(monkeypatch, tmp_path, caplog):
    _reload_settings(monkeypatch, tmp_path, _PROVIDERS_CONFIG_YAML)
    secret_1 = "sk-super-secret-first-0001"
    secret_2 = "sk-super-secret-second-0002"
    monkeypatch.setenv("OPENAI_API_KEY", secret_1)
    monkeypatch.setenv("OPENAI_API_KEY_BACKUP", secret_2)

    def boom_with_key(api_key: str):
        def _inner(**_kw):
            raise RuntimeError(f"auth rejected for Bearer {api_key}")

        return _inner

    _KeyRoutedOpenAI.reset()
    _KeyRoutedOpenAI.behaviors = {
        secret_1: boom_with_key(secret_1),
        secret_2: boom_with_key(secret_2),
    }
    monkeypatch.setattr("openai.OpenAI", _KeyRoutedOpenAI)
    _no_sleep(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=ai_module.logger.name):
        with pytest.raises(RuntimeError):
            ai_module._chat("sys", "user")

    expected_calls_per_provider = ai_module._MAX_RETRIES_PER_PROVIDER + 1
    assert len(_KeyRoutedOpenAI.calls) == 2 * expected_calls_per_provider
    assert secret_1 not in caplog.text
    assert secret_2 not in caplog.text
