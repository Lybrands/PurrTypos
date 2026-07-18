"""Static guards for application orchestration and HTTP transport ownership."""

from __future__ import annotations

import ast
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
RUN_SERVICE = BACKEND_DIR / "application" / "agent_run_service.py"
AI_ROUTER = BACKEND_DIR / "routers" / "ai.py"
PORT_ONLY_SERVICES = (
    BACKEND_DIR / "application" / "agent_delegation_service.py",
    BACKEND_DIR / "application" / "agent_run_queries.py",
    BACKEND_DIR / "application" / "run_execution_control.py",
    BACKEND_DIR / "application" / "agent_orchestrator.py",
)


def test_agent_run_service_has_no_http_or_sse_dependency():
    tree = ast.parse(RUN_SERVICE.read_text(encoding="utf-8"))
    forbidden_roots = {"fastapi", "sse_starlette", "routers"}
    violations: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.split(".", 1)[0] in forbidden_roots:
                violations.append(f"line {node.lineno} imports {name}")
    assert not violations, "RunService transport leaks:\n" + "\n".join(violations)


def test_ai_router_does_not_assemble_agent_core():
    tree = ast.parse(AI_ROUTER.read_text(encoding="utf-8"))
    forbidden_names = {
        "create_core",
        "to_writing_agent_request",
        "writing_run_options",
    }
    violations = [
        f"line {getattr(node, 'lineno', 0)} names {node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_names
    ]
    violations.extend(
        f"line {getattr(node, 'lineno', 0)} names {node.id}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in forbidden_names
    )
    assert not violations, "AI router owns Agent assembly:\n" + "\n".join(violations)


def test_reusable_agent_services_depend_on_ports_not_infrastructure():
    forbidden_roots = {
        "database",
        "dependencies",
        "fastapi",
        "infrastructure",
        "routers",
        "sse_starlette",
    }
    violations: list[str] = []
    for path in PORT_ONLY_SERVICES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0] in forbidden_roots:
                    violations.append(
                        f"{path.name}:{node.lineno} imports {name}"
                    )
    assert not violations, "Reusable service dependency leaks:\n" + "\n".join(violations)
