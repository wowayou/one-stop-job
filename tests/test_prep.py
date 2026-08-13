import json

from backend.app.models import Company, Job, UserProfile
from backend.app.services import ai
from backend.app.services.prep import build_interview_prep


PREP_KEYS = {
    "jd_summary",
    "skill_gaps",
    "resume_points",
    "star_stories",
    "questions_to_ask",
    "core_pitch",
    "communication_draft",
    "tailored_resume",
}


class _FakeResp:
    def __init__(self, content: str):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    """最小化模拟 openai 客户端：create(**kwargs) 永远返回预置内容。"""

    def __init__(self, content: str):
        completions = type("Completions", (), {"create": lambda _self, **_kw: _FakeResp(content)})()
        self.chat = type("Chat", (), {"completions": completions})()


def _minimal_ai_config(monkeypatch, tmp_path):
    """把 `ai.enabled` 打开、且**不配 providers**，让 `is_ai_available()` 走
    `OPENAI_API_KEY` 环境变量这条路。

    不隔离的话会继承开发机真实 config.yaml 的 `ai.providers`——按 `services/ai.py::_providers`
    的语义，一旦配了 providers 就不再回退 `OPENAI_*` 环境变量，于是本文件里"设了 key 就该
    调模型"的用例全部拿到 None（曾导致 2 个用例失败）。
    """
    from backend.app import config

    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    config.get_settings.cache_clear()


def test_build_interview_prep_returns_all_template_keys():
    job = Job(source="fixture", external_id="1", title="独立站运营", company_name="示例公司", city="示例市", skills="独立站,SEO,数据分析")
    payload = build_interview_prep(job, Company(name="示例公司"), UserProfile())
    assert set(payload) == PREP_KEYS
    assert all(isinstance(value, str) and value for value in payload.values())


def test_tailor_returns_none_when_ai_unavailable(monkeypatch, tmp_path):
    _minimal_ai_config(monkeypatch, tmp_path)
    # 置空而非 delenv：delenv 掉的变量会被 get_settings 里的 load_dotenv 从真 .env 填回。
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    base = {key: f"BASE-{key}" for key in PREP_KEYS}
    assert ai.tailor_interview_prep_llm({"title": "独立站运营"}, base) is None


def test_tailor_merges_full_ai_output(monkeypatch, tmp_path):
    _minimal_ai_config(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    full = {key: f"AI-{key}" for key in PREP_KEYS}
    monkeypatch.setattr(ai, "_client", lambda: _FakeClient(json.dumps(full, ensure_ascii=False)))
    base = {key: f"BASE-{key}" for key in PREP_KEYS}
    out = ai.tailor_interview_prep_llm({"title": "独立站运营"}, base)
    assert out == full


def test_tailor_falls_back_per_missing_or_empty_key(monkeypatch, tmp_path):
    _minimal_ai_config(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    partial = {"communication_draft": "你好，专门为示例公司写的打招呼", "jd_summary": "AI 摘要", "resume_points": "   "}
    monkeypatch.setattr(ai, "_client", lambda: _FakeClient(json.dumps(partial, ensure_ascii=False)))
    base = {key: f"BASE-{key}" for key in PREP_KEYS}
    out = ai.tailor_interview_prep_llm({}, base)
    assert set(out) == PREP_KEYS
    assert out["communication_draft"] == "你好，专门为示例公司写的打招呼"
    assert out["jd_summary"] == "AI 摘要"
    assert out["resume_points"] == "BASE-resume_points"  # 空白 → 回退模板
    assert out["star_stories"] == "BASE-star_stories"  # 缺失 → 回退模板


def test_tailor_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(ai, "_client", lambda: _FakeClient("抱歉我无法以 JSON 回答"))
    assert ai.tailor_interview_prep_llm({}, {key: "x" for key in PREP_KEYS}) is None


def test_tailor_returns_none_on_client_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # 多 provider 容错引入了退避重试；桩掉 sleep，避免这个「全失败」用例真等退避秒数。
    monkeypatch.setattr(ai.time, "sleep", lambda _s: None)

    def boom():
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(ai, "_client", boom)
    assert ai.tailor_interview_prep_llm({}, {key: "x" for key in PREP_KEYS}) is None
