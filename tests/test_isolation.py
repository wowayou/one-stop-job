"""绊线：锁住 conftest 的测试隔离机制本身。

这些不变量一旦被悄悄拿掉，症状不是「某个用例红了」，而是整套测试重新开始依赖机主本机的
`.env` / `config.yaml`——那既会让干净检出（含 CI）大面积翻红，也会让测试真的连模型、真的
写进本人看板。它们很难从别的用例失败里反推出来，所以在这里直接钉住。

配套的端到端判据在 `scripts/quality_gate.sh` 的 "Clean Checkout Tests" 段：在一个没有
个人 `.env`、没有个人 `config.yaml` 的检出里跑完整套测试。
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from backend.app import config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dotenv_loading_is_neutralized():
    """`config.load_dotenv` 必须已被换成桩，不再是 python-dotenv 的真身。

    只 `delenv` 是拦不住的：`get_settings()` 每次都重新 `load_dotenv(PROJECT_DIR/".env")`，
    被删掉的变量立刻被填回，「没配 key」的用例反而拿到真 key。

    这里查的是**函数来源**而不是返回值：真身对不存在的路径同样返回 False，拿返回值当判据
    是句假断言（先前就写错过一次，破坏中和后测试照旧全绿）。
    """
    assert getattr(config.load_dotenv, "__module__", "") != "dotenv.main", (
        "config.load_dotenv 仍是 python-dotenv 真身，conftest 的 isolate_local_settings "
        "没有生效；测试会重新读到机主 .env 里的真实密钥与上下文仓库路径。"
    )


def test_settings_never_sees_values_only_present_in_real_dotenv():
    """只存在于机主 `.env` 的变量不得出现在测试进程里。

    对照的是 `conftest.PRISTINE_ENVIRON`（conftest 导入时的父进程环境），而不是直接比
    `os.environ`：开发机的 shell 常导出着和 `.env` 同名同值的变量（实测 `HTTP_PROXY` /
    `HTTPS_PROXY` 就是），直接比对会把它们误判成泄漏。而 `load_dotenv` 从不覆盖已存在的
    变量，所以只有**不在父进程环境里**的那些才可能是它灌进来的——正是这里要查的。

    干净检出没有 `.env`，自动跳过。
    """
    import conftest  # pytest 已把 tests/ 放进 sys.path；tests 不是 package，不能相对导入
    from dotenv import dotenv_values

    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        pytest.skip("没有 .env（干净检出），本条无可对照的真实值")

    # 必须自己触发一次：`load_dotenv` 只在 `get_settings()` 内部被调用。不调的话本条就
    # 变成顺序依赖——单跑本文件时前面没人调过 get_settings，中和失效也照旧全绿（实测过）。
    config.get_settings.cache_clear()
    config.get_settings()

    dotenv_only = {
        name: value
        for name, value in dotenv_values(env_file).items()
        if value and value.strip() and name not in conftest.PRISTINE_ENVIRON
    }
    if not dotenv_only:
        pytest.skip(".env 里的变量都已由父进程环境提供，无可区分的对照项")

    leaked = sorted(name for name, value in dotenv_only.items() if os.environ.get(name) == value)
    assert not leaked, (
        f"这些只存在于机主 .env 的变量泄漏进了测试进程：{leaked}。"
        "说明 conftest 的 load_dotenv 中和失效了。"
    )


def test_baseline_config_disables_outward_capabilities():
    """基线配置必须关掉一切对外/定时能力，且数据目录在临时目录里。"""
    settings = config.get_settings()

    assert settings.telegram_config.get("enabled") is False
    digest = settings.schedule_config.get("digest") or {}
    assert digest.get("enabled") is False
    assert digest.get("collect_first") is False
    assert settings.automation_config.get("mode") == "manual"
    assert settings.updates_config.get("enabled") is False
    # providers 必须缺席：配了它就不再回退 OPENAI_* 环境变量，等于绕过置空的假密钥。
    assert "providers" not in settings.ai_config
    assert settings.context_repo_path is None, "上下文仓库必须表现为「未配置」"

    data_dir = str(settings.data_dir)
    assert str(REPO_ROOT / "data") not in data_dir, f"数据目录落在了真实 data/ 下：{data_dir}"


def test_outbound_network_is_blocked():
    """非回环目标的名字解析与连接都必须当场失败，回环放行。"""
    with pytest.raises(AssertionError, match="测试不得联网"):
        socket.getaddrinfo("api.telegram.org", 443)
    with pytest.raises(AssertionError, match="测试不得联网"):
        socket.getaddrinfo("api.github.com", 443)
    with pytest.raises(AssertionError, match="测试不得联网"):
        socket.socket().connect(("93.184.216.34", 80))

    # 回环不受影响：本地起服务的用例依赖它。
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_unstubbed_model_call_fails_fast():
    """未桩掉的模型调用必须立刻抛错，而不是去连一个不存在的 base_url 后慢慢重试。"""
    from backend.app.services import ai

    with pytest.raises(RuntimeError, match="未桩掉的模型调用"):
        ai._chat("system", "user")


@pytest.mark.exercises_ai_chat
def test_exercises_ai_chat_marker_restores_real_chat():
    """豁免标记必须真的把 `_chat` 还原成实现本身，否则 test_ai_client 那组测的是桩。"""
    from backend.app.services import ai

    assert ai._chat.__module__ == ai.__name__, "标记了 exercises_ai_chat，_chat 却仍是 conftest 的桩"
