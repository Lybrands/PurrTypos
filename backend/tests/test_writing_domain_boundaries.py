"""Static dependency guard for the writing business layer."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
WRITING_DIR = BACKEND_DIR / "domains" / "writing"
ALLOWED_IMPORT_ROOTS = set(sys.stdlib_module_names) | {
    "__future__",
    "agent_core",
    "domains",
}
FORBIDDEN_IDENTIFIERS = {
    "get_db",
    "FastAPI",
    "APIRouter",
    "Request",
    "EventSourceResponse",
}


def _module_for(path: Path) -> str:
    relative = path.relative_to(BACKEND_DIR).with_suffix("")
    return ".".join(relative.parts)


def _resolved_import(module_name: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module_name.rpartition(".")[0]
    return importlib.util.resolve_name(
        "." * node.level + (node.module or ""),
        package,
    )


def test_writing_domain_depends_only_on_core_itself_and_stdlib():
    violations: list[str] = []
    for path in sorted(WRITING_DIR.rglob("*.py")):
        module_name = _module_for(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [_resolved_import(module_name, node)]
            for name in imports:
                root = name.split(".", 1)[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(
                        f"{path.relative_to(BACKEND_DIR)}:{node.lineno} imports {name}"
                    )

    assert not violations, "Writing domain dependency violations:\n" + "\n".join(violations)


def test_writing_domain_has_no_transport_or_global_database_access():
    violations: list[str] = []
    for path in sorted(WRITING_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value: str | None = None
            if isinstance(node, ast.Name):
                value = node.id
            elif isinstance(node, ast.Attribute):
                value = node.attr
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value if node.value in FORBIDDEN_IDENTIFIERS else None
            if value in FORBIDDEN_IDENTIFIERS:
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{getattr(node, 'lineno', 0)} names {value}"
                )

    assert not violations, "Writing domain boundary violations:\n" + "\n".join(violations)
