"""Static dependency guard for the business-agnostic Agent Core package."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = BACKEND_DIR / "agent_core"
ALLOWED_IMPORT_ROOTS = set(sys.stdlib_module_names) | {"__future__", "agent_core"}
FORBIDDEN_IMPORT_ROOTS = {"importlib", "sqlite3"}
BANNED_WRITING_IDENTIFIERS = {
    "bookId",
    "book_id",
    "chapterId",
    "chapter_id",
    "outlineId",
    "outline_id",
    "characterId",
    "character_id",
    "memoryId",
    "memory_id",
    "foreshadowingId",
    "foreshadowing_id",
    "settingEntityId",
    "setting_entity_id",
}
BANNED_WRITING_TEXT_FRAGMENTS = {
    "queryoutline",
    "getchaptercontent",
    "associatedoutlines",
    "associated chapters",
    "selected memories",
    "大纲",
    "章节",
    "人物",
    "伏笔",
}
BANNED_SCREENPLAY_TEXT_FRAGMENTS = {
    "continuity_review",
    "durableexecutionplan",
    "episodes",
    "proposescenedraft",
    "scenes",
    "scene_generation",
    "screenplay",
    "taskadmissionvocabulary",
    "剧本",
}


def _source_files() -> list[Path]:
    return sorted(CORE_DIR.rglob("*.py"))


def _module_for(path: Path) -> str:
    relative = path.relative_to(BACKEND_DIR).with_suffix("")
    return ".".join(relative.parts)


def _resolved_import(module_name: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module_name.rpartition(".")[0]
    return importlib.util.resolve_name("." * node.level + (node.module or ""), package)


def test_agent_core_imports_only_stdlib_and_itself():
    violations: list[str] = []
    for path in _source_files():
        module_name = _module_for(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [_resolved_import(module_name, node)]
            for name in imported:
                root = name.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS or root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(
                        f"{path.relative_to(BACKEND_DIR)}:{node.lineno} imports {name}"
                    )
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    violations.append(
                        f"{path.relative_to(BACKEND_DIR)}:{node.lineno} uses __import__"
                    )

    assert not violations, "Agent Core dependency violations:\n" + "\n".join(violations)


def test_agent_core_does_not_name_writing_scope_fields():
    violations: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value: str | None = None
            if isinstance(node, ast.Name):
                value = node.id
            elif isinstance(node, ast.arg):
                value = node.arg
            elif isinstance(node, ast.Attribute):
                value = node.attr
            elif isinstance(node, ast.keyword):
                value = node.arg
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value if node.value in BANNED_WRITING_IDENTIFIERS else None
            if value in BANNED_WRITING_IDENTIFIERS:
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{getattr(node, 'lineno', 0)} names {value}"
                )

    assert not violations, "Agent Core writing-scope leaks:\n" + "\n".join(violations)


def test_agent_core_prompts_do_not_embed_writing_domain_language():
    violations: list[str] = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8").casefold()
        for fragment in sorted(BANNED_WRITING_TEXT_FRAGMENTS):
            if fragment.casefold() in source:
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)} contains {fragment!r}"
                )

    assert not violations, "Agent Core writing-text leaks:\n" + "\n".join(violations)


def test_agent_core_does_not_embed_screenplay_business_protocols():
    violations: list[str] = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8").casefold()
        for fragment in sorted(BANNED_SCREENPLAY_TEXT_FRAGMENTS):
            if fragment in source:
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)} contains {fragment!r}"
                )

    assert not violations, "Agent Core screenplay leaks:\n" + "\n".join(violations)
