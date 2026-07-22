from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path


CORE_DOCUMENTS = {
    "entrypoint": Path("README.md"),
    "decision_rules": Path("toolkit/24-job-search-decision-rules.md"),
    "profile": Path("toolkit/job-pipeline/PROFILE.md"),
    "board": Path("toolkit/23-job-pipeline.md"),
}
CARDS_DIR = Path("toolkit/job-pipeline/cards")
# Phase 2（CLAUDE.md §3.10）：仅看板文件允许「确认制」追加写入，其它白名单文档仍然只读。
WRITABLE_DOCUMENTS = {"board"}
UPDATED_PATTERN = re.compile(r"^>\s*Updated:\s*(.+?)\s*$", re.MULTILINE)


class ContextRepositoryError(RuntimeError):
    """外部上下文仓库不可安全读取。"""


class ContextSectionNotFoundError(ContextRepositoryError):
    """看板文件里找不到目标列（`## 标题` 行），结构不符合预期，拒绝写入。"""


def _resolve_within(root: Path, relative_path: Path) -> Path:
    """越界防护：候选路径 resolve 后必须仍落在 root 内，否则拒绝。"""
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ContextRepositoryError("上下文路径越界") from exc
    return candidate


def _insert_line_in_section(content: str, section_heading: str, line: str) -> str:
    """在 `## {section_heading}` 段内插入一行,插到该段最后一个非空行之后;
    段内容为空或只有空行则紧跟标题行之后。段边界 = 下一个 `## ` 标题或文件结束。
    除插入这一整行外,其余内容逐字节不变。"""
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines(keepends=True)
    heading_marker = f"## {section_heading}"

    heading_index = None
    for index, raw in enumerate(lines):
        if raw.rstrip("\r\n") == heading_marker:
            heading_index = index
            break
    if heading_index is None:
        raise ContextSectionNotFoundError(f"看板缺少『{section_heading}』列")

    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].rstrip("\r\n").startswith("## "):
            section_end = index
            break

    insert_at = heading_index + 1
    for index in range(heading_index + 1, section_end):
        if lines[index].strip():
            insert_at = index + 1

    new_line = line if line.endswith(("\n", "\r\n")) else line + newline
    lines.insert(insert_at, new_line)
    return "".join(lines)


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
            message = "路径已配置但目录不可访问（请确认当前 OS 下该路径存在，WSL 用 /mnt/d/...，Windows 用 D:\\...）"
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
        return _resolve_within(self.root, relative_path)


class ContextWriter:
    """个人操作仓库的唯一写入口（Phase 2，CLAUDE.md §3.10）。

    看板文件是 Obsidian Kanban 插件文件（frontmatter + 若干 `## 列名` 段 + 卡片行 +
    尾部 `%% kanban:settings` 块），状态由卡片所在列表达，只能靠本人在 Obsidian 里拖卡
    改变；本类因此只支持「在指定列内插入一行」，不做整段替换或 EOF 追加，避免把新行插到
    kanban 设置块之后。绝不创建新文件、绝不删除或改写既有内容、绝不触碰
    `WRITABLE_DOCUMENTS` 之外的任何路径。真正落盘只能由本人在已入库候选上点
    「写入看板」触发（见 services/board_write.py 与 main.py 的
    `board_write_candidates` 端点）。
    """

    def __init__(self, root: Path | None):
        self.root = root.resolve() if root is not None else None

    def insert_line_in_section(self, key: str, section_heading: str, line: str) -> None:
        if key not in WRITABLE_DOCUMENTS:
            raise ContextRepositoryError(f"不允许写入未列入白名单的上下文：{key}")
        if self.root is None:
            raise ContextRepositoryError("未配置个人上下文仓库")

        relative_path = CORE_DOCUMENTS[key]
        path = _resolve_within(self.root, relative_path)
        if not path.is_file():
            raise ContextRepositoryError(f"上下文文件不存在，拒绝创建：{relative_path.as_posix()}")

        original = path.read_text(encoding="utf-8")
        updated = _insert_line_in_section(original, section_heading, line)

        tmp_path = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
        tmp_path.write_text(updated, encoding="utf-8")
        os.replace(tmp_path, path)
