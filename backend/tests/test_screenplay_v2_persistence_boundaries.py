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
OPERATION_REPOSITORY = (
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_operation_repository.py"
)
OPERATION_FINALIZER = (
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_operation_finalizer.py"
)
ROOT_COMPLETION_PROJECTOR = (
    BACKEND_DIR
    / "infrastructure"
    / "screenplay"
    / "agent_root_completion_projector.py"
)
AGENT_PROFILE = BACKEND_DIR / "application" / "screenplay_agent_profile.py"
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
    assert "screenplay_agent_events" not in source
    assert "screenplay_agent_chunks" not in source
    assert "screenplay_agent_operations" in source
    assert OPERATION_REPOSITORY.exists()
    assert "screenplay_agent_operations" in OPERATION_REPOSITORY.read_text(
        encoding="utf-8"
    )


def test_retired_screenplay_output_stores_have_no_production_references():
    production_roots = (
        BACKEND_DIR / "main.py",
        BACKEND_DIR / "application",
        BACKEND_DIR / "database",
        BACKEND_DIR / "domains",
        BACKEND_DIR / "infrastructure",
        BACKEND_DIR / "routers",
    )
    allowed = BACKEND_DIR / "database" / "crud" / "screenplay_agent_runtime_cleanup.py"
    violations: list[str] = []
    forbidden = (
        "screenplay_agent_chunks",
        "screenplay_agent_events",
        "ScreenplayAgentChunkStore",
        "ScreenplayAgentChunkProjector",
        "protocol_version",
    )
    for root in production_roots:
        paths = (root,) if root.is_file() else root.rglob("*.py")
        for path in paths:
            if path == allowed:
                continue
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in source:
                    violations.append(f"{path.relative_to(BACKEND_DIR)}: {token}")
    assert violations == []


def test_new_screenplay_agent_code_never_writes_legacy_turn_task_authority():
    source = AGENT_REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(AGENT_REPOSITORY))
    legacy_columns = {
        "task_id",
        "result_revision_id",
        "target_role",
    }
    violations = sorted({
        column
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "UPDATE screenplay_agent_turns" in node.value
        for column in legacy_columns
        if f"{column} =" in node.value
    })
    assert violations == []


def test_operation_finalizer_is_the_only_success_commit_boundary():
    profile = AGENT_PROFILE.read_text(encoding="utf-8")
    projector = ROOT_COMPLETION_PROJECTOR.read_text(encoding="utf-8")
    finalizer = OPERATION_FINALIZER.read_text(encoding="utf-8")

    assert OPERATION_FINALIZER.exists()
    assert ROOT_COMPLETION_PROJECTOR.exists()
    assert "SqliteScreenplayOperationFinalizer" in projector
    assert "self._finalizer.finalize(" in projector
    assert "SqliteScreenplayOperationFinalizer" not in profile
    assert "self._operations.succeed(" not in profile
    assert "self._repository.complete_operation(" not in profile
    assert "publish_candidate_revision" not in profile
    assert "publish_screenplay_agent_task_candidate(" in finalizer
    assert "UPDATE screenplay_agent_turns SET status = 'completed'" in finalizer
    assert "UPDATE screenplay_agent_operations SET status = 'succeeded'" in finalizer
    assert "command_type, request_digest" in finalizer
