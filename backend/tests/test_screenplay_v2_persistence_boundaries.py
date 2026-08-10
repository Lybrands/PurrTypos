"""Dependency guards for the screenplay v2 persistence boundary."""

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
AGENT_REPOSITORY = (
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_agent_repository.py"
)
REMOVED_RUNTIME_FILES = (
    BACKEND_DIR / "database" / "crud" / "screenplay_read_model.py",
    BACKEND_DIR / "routers" / "screenplay.py",
    BACKEND_DIR / "application" / "agent_errors.py",
    BACKEND_DIR / "domains" / "screenplay" / "legacy_migration.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_v2_migration_repository.py",
)


def test_v2_repository_does_not_call_legacy_screenplay_crud():
    tree = ast.parse(
        V2_REPOSITORY.read_text(encoding="utf-8"),
        filename=str(V2_REPOSITORY),
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "database.crud.screenplay":
                violations.append(f"line {node.lineno}: imports legacy CRUD")
            if node.module == "database.crud" and any(
                alias.name == "screenplay" for alias in node.names
            ):
                violations.append(f"line {node.lineno}: imports legacy CRUD")

    assert not violations, "v2 persistence boundary violations: " + "; ".join(
        violations
    )


def test_removed_screenplay_runtime_files_are_not_restored():
    assert [path for path in REMOVED_RUNTIME_FILES if path.exists()] == []


def test_screenplay_agent_repository_does_not_duplicate_purra_task_state():
    source = AGENT_REPOSITORY.read_text(encoding="utf-8")
    assert "screenplay_agent_turns" in source
    assert "screenplay_agent_jobs" not in source
    assert "screenplay_agent_job_steps" not in source
    assert "ai_agent_long_tasks" in source
    assert "screenplay_agent_events" in source
    assert "screenplay_agent_chunks" in source
    assert "screenplay_operations" not in source
