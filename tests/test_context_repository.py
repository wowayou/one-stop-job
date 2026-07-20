from pathlib import Path

import pytest

from backend.app.services.context_repository import ContextRepository, ContextRepositoryError


def _build_context_repo(root: Path) -> Path:
    (root / "toolkit/job-pipeline/cards").mkdir(parents=True)
    (root / "README.md").write_text("# Entry\n\n> Updated: 2026-07-20\n", encoding="utf-8")
    (root / "toolkit/24-job-search-decision-rules.md").write_text(
        "# Rules\n\n> Updated: 2026-07-19\n",
        encoding="utf-8",
    )
    (root / "toolkit/job-pipeline/PROFILE.md").write_text("# Profile\n", encoding="utf-8")
    (root / "toolkit/23-job-pipeline.md").write_text("# Board\n", encoding="utf-8")
    (root / "toolkit/job-pipeline/cards/_template.md").write_text("# Template\n", encoding="utf-8")
    (root / "toolkit/job-pipeline/cards/2026-07-20-acme-seo.md").write_text(
        "# Acme SEO\n\n> Updated: 2026-07-20\n",
        encoding="utf-8",
    )
    return root


def test_context_repository_reads_only_allowlisted_documents(tmp_path):
    root = _build_context_repo(tmp_path / "context")
    repository = ContextRepository(root)

    status = repository.status()
    assert status == {
        "configured": True,
        "available": True,
        "documents": {"entrypoint": True, "decision_rules": True, "profile": True, "board": True},
        "message": "个人上下文仓库只读连接正常",
    }
    assert repository.read_document("decision_rules").relative_path == "toolkit/24-job-search-decision-rules.md"
    assert repository.read_core_context()["entrypoint"].content.startswith("# Entry")
    assert repository.read_job_card("2026-07-20-acme-seo.md").key == "job_card"


def test_context_repository_blocks_arbitrary_paths(tmp_path):
    root = _build_context_repo(tmp_path / "context")
    repository = ContextRepository(root)

    with pytest.raises(ContextRepositoryError, match="白名单"):
        repository.read_document("secrets")
    with pytest.raises(ContextRepositoryError, match="岗位卡名称"):
        repository.read_job_card("../PROFILE.md")
    with pytest.raises(ContextRepositoryError, match="岗位卡名称"):
        repository.read_job_card("_template.md")


def test_context_repository_reports_missing_without_exposing_root(tmp_path):
    root = tmp_path / "missing-context"
    repository = ContextRepository(root)

    status = repository.status()
    assert status["configured"] is True
    assert status["available"] is False
    assert str(root) not in str(status)


def test_context_repository_can_be_disabled():
    status = ContextRepository(None).status()
    assert status["configured"] is False
    assert status["available"] is False
