"""决策聊天专属 helper（Phase R · R2，从 main.py 下沉）。

`_decision_context` / `_job_context` / `_recent_conversation`：`chat_context_preview`
（预览）与 `create_chat_message`（真正发送）共用的三个纯读取函数，避免预览与实际发送
的上下文漂移。只读外部个人上下文仓库与本地聊天记录，不写任何东西。

依赖方向：只依赖 `config` / `models` / `services.context_repository`，不得 import
`main`（会形成循环 import）——main.py 反过来从本模块 import 这些函数使用。
"""

from __future__ import annotations

from ..config import get_settings
from ..models import ChatMessage, Job
from .context_repository import ContextRepository, ContextRepositoryError


def decision_context() -> tuple[str, str, bool]:
    repository = ContextRepository(get_settings().context_repo_path)
    parts: list[str] = []
    rules_version = "local-profile"
    rules_loaded = False
    for key in ("decision_rules", "profile", "board"):
        try:
            document = repository.read_document(key)
        except ContextRepositoryError:
            continue
        if key == "decision_rules" and document.updated:
            rules_version = document.updated
        if key == "decision_rules":
            rules_loaded = True
        parts.append(f"[{key}]\n{document.content}")
    return "\n\n".join(parts)[:32000], rules_version, rules_loaded


def job_context(job: Job | None) -> dict:
    """岗位事实（发送给 AI 的字段）。preview 与真正发送共用，避免两处漂移。"""
    if not job:
        return {}
    return {
        "title": job.title,
        "company_name": job.company_name,
        "salary": job.salary_text,
        "location": " · ".join(filter(None, [job.city, job.area])),
        "skills": job.skills,
        "description": job.description,
        "recruiter_message": job.recruiter,
    }


def recent_conversation(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """最近对话（最多 12 条，每条截断到 4000 字）。preview 与真正发送共用。"""
    return [{"role": item.role, "content": item.content[:4000]} for item in messages[-12:]]
