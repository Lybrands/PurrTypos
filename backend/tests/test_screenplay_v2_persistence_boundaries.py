"""Dependency guards after removal of the frozen Screenplay runtime."""

from __future__ import annotations

import ast
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
V2_REPOSITORY = (
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_v2_repository.py"
)
REMOVED_RUNTIME_PATHS = (
    BACKEND_DIR / "application" / "screenplay_agent_profile.py",
    BACKEND_DIR / "application" / "screenplay_agent_service.py",
    BACKEND_DIR / "application" / "screenplay_agent_stream.py",
    BACKEND_DIR / "application" / "screenplay_agent_task_executor.py",
    BACKEND_DIR / "application" / "screenplay_v2_service.py",
    BACKEND_DIR / "domains" / "screenplay_agent",
    BACKEND_DIR / "infrastructure" / "screenplay",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_agent_repository.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_operation_repository.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_operation_finalizer.py",
    BACKEND_DIR / "database" / "crud" / "screenplay_read_model.py",
    BACKEND_DIR / "routers" / "screenplay.py",
    BACKEND_DIR / "application" / "agent_errors.py",
    BACKEND_DIR / "domains" / "screenplay" / "legacy_migration.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_v2_migration_repository.py",
)
RETIRED_IMPORT_PREFIXES = (
    "application.screenplay_agent_",
    "application.screenplay_candidate_",
    "application.screenplay_checkpoint_",
    "application.screenplay_manifest_",
    "application.screenplay_part_",
    "application.screenplay_step_",
    "application.screenplay_structured_",
    "application.screenplay_task_",
    "application.screenplay_tool_",
    "application.screenplay_v2_service",
    "domains.screenplay_agent",
    "infrastructure.screenplay",
    "infrastructure.persistence.sqlite_screenplay_agent_repository",
    "infrastructure.persistence.sqlite_screenplay_operation_repository",
    "infrastructure.persistence.sqlite_screenplay_operation_finalizer",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return tuple(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
    ) + tuple(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )


def test_v2_repository_does_not_call_legacy_screenplay_crud():
    assert "database.crud.screenplay" not in _imports(V2_REPOSITORY)


def test_frozen_screenplay_runtime_stays_deleted():
    assert [path for path in REMOVED_RUNTIME_PATHS if path.exists()] == []


def test_production_python_has_no_retired_screenplay_imports():
    roots = (
        BACKEND_DIR / "agents",
        BACKEND_DIR / "application",
        BACKEND_DIR / "database",
        BACKEND_DIR / "domains",
        BACKEND_DIR / "infrastructure",
        BACKEND_DIR / "routers",
        BACKEND_DIR / "schemas",
    )
    violations = [
        f"{path.relative_to(BACKEND_DIR)} imports {module}"
        for root in roots
        for path in root.rglob("*.py")
        for module in _imports(path)
        if module.startswith(RETIRED_IMPORT_PREFIXES)
    ]
    assert violations == []


def test_retired_screenplay_output_stores_have_no_production_references():
    roots = (
        BACKEND_DIR / "main.py",
        BACKEND_DIR / "agents",
        BACKEND_DIR / "application",
        BACKEND_DIR / "database",
        BACKEND_DIR / "domains",
        BACKEND_DIR / "infrastructure",
        BACKEND_DIR / "routers",
    )
    allowed = BACKEND_DIR / "database" / "crud" / "screenplay_agent_runtime_cleanup.py"
    forbidden = (
        "screenplay_agent_chunks",
        "screenplay_agent_events",
        "ScreenplayAgentChunkStore",
        "ScreenplayAgentChunkProjector",
    )
    violations: list[str] = []
    for root in roots:
        paths = (root,) if root.is_file() else root.rglob("*.py")
        for path in paths:
            if path == allowed:
                continue
            source = path.read_text(encoding="utf-8")
            violations.extend(
                f"{path.relative_to(BACKEND_DIR)}: {token}"
                for token in forbidden
                if token in source
            )
    assert violations == []
