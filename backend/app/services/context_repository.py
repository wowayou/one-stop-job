from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


CORE_DOCUMENTS = {
    "entrypoint": Path("README.md"),
    "decision_rules": Path("toolkit/24-job-search-decision-rules.md"),
    "profile": Path("toolkit/job-pipeline/PROFILE.md"),
    "board": Path("toolkit/23-job-pipeline.md"),
}
CARDS_DIR = Path("toolkit/job-pipeline/cards")
UPDATED_PATTERN = re.compile(r"^>\s*Updated:\s*(.+?)\s*$", re.MULTILINE)


class ContextRepositoryError(RuntimeError):
    """外部上下文仓库不可安全读取。"""


@dataclass(frozen=True)
class ContextDocument:
    key: str
    relative_path: str
    content: str
    updated: str | None


class ContextRepository:
    """只读访问外部个人操作仓库中的白名单 Markdown。

    Phase 0 不提供任何写方法。所有路径都从固定白名单或 cards 目录解析，
    且 API 状态不暴露宿主机绝对路径。
    """

    def __init__(self, root: Path | None):
        self.root = root.resolve() if root is not None else None

    def status(self) -> dict:
        if self.root is None:
            return {
                "configured": False,
                "available": False,
                "documents": {key: False for key in CORE_DOCUMENTS},
                "message": "未配置个人上下文仓库",
            }

        root_available = self.root.is_dir()
        documents = {
            key: root_available and self._resolve(relative_path).is_file()
            for key, relative_path in CORE_DOCUMENTS.items()
        }
        missing = [key for key, available in documents.items() if not available]
        available = root_available and not missing
        if not root_available:
            message = "个人上下文仓库不可访问"
        elif missing:
            message = f"个人上下文仓库缺少白名单文件：{', '.join(missing)}"
        else:
            message = "个人上下文仓库只读连接正常"
        return {
            "configured": True,
            "available": available,
            "documents": documents,
            "message": message,
        }

    def read_document(self, key: str) -> ContextDocument:
        relative_path = CORE_DOCUMENTS.get(key)
        if relative_path is None:
            raise ContextRepositoryError(f"不允许读取未列入白名单的上下文：{key}")
        path = self._required_file(relative_path)
        content = path.read_text(encoding="utf-8")
        updated_match = UPDATED_PATTERN.search(content)
        return ContextDocument(
            key=key,
            relative_path=relative_path.as_posix(),
            content=content,
            updated=updated_match.group(1).strip() if updated_match else None,
        )

    def read_core_context(self) -> dict[str, ContextDocument]:
        return {key: self.read_document(key) for key in CORE_DOCUMENTS}

    def list_job_cards(self) -> list[str]:
        if self.root is None:
            return []
        cards_dir = self._resolve(CARDS_DIR)
        if not cards_dir.is_dir():
            return []
        return sorted(
            path.name
            for path in cards_dir.glob("*.md")
            if path.is_file() and path.name != "_template.md"
        )

    def read_job_card(self, card_name: str) -> ContextDocument:
        candidate = Path(card_name)
        if candidate.name != card_name or candidate.suffix.lower() != ".md" or card_name == "_template.md":
            raise ContextRepositoryError("岗位卡名称必须是 cards 目录下的 Markdown 文件名")
        relative_path = CARDS_DIR / candidate.name
        path = self._required_file(relative_path)
        content = path.read_text(encoding="utf-8")
        updated_match = UPDATED_PATTERN.search(content)
        return ContextDocument(
            key="job_card",
            relative_path=relative_path.as_posix(),
            content=content,
            updated=updated_match.group(1).strip() if updated_match else None,
        )

    def _required_file(self, relative_path: Path) -> Path:
        if self.root is None:
            raise ContextRepositoryError("未配置个人上下文仓库")
        path = self._resolve(relative_path)
        if not path.is_file():
            raise ContextRepositoryError(f"上下文文件不存在：{relative_path.as_posix()}")
        return path

    def _resolve(self, relative_path: Path) -> Path:
        if self.root is None:
            raise ContextRepositoryError("未配置个人上下文仓库")
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ContextRepositoryError("上下文路径越界") from exc
        return candidate
