"""Host-side ratchets for the external PurrA dependency."""

from __future__ import annotations

import ast
from importlib import metadata
from pathlib import Path

import purra


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
PURRA_VERSION = "0.4.0"
PURRA_REQUIREMENT = f"purra=={PURRA_VERSION}"
ALLOWED_PROVIDER_COMPOSITION = {
    "application/agent_composition.py",
}
PUBLIC_PURRA_HOST_MODULES = frozenset({
    "purra.api",
    "purra.artifacts",
    "purra.cancellation",
    "purra.context_budget",
    "purra.context_orchestration",
    "purra.context_strategies",
    "purra.contracts",
    "purra.errors",
    "purra.evaluation",
    "purra.events",
    "purra.evidence",
    "purra.json_values",
    "purra.long_tasks",
    "purra.model_call_parameters",
    "purra.model_invocation",
    "purra.model_protocol",
    "purra.normalization",
    "purra.observability",
    "purra.orphan_recovery",
    "purra.output",
    "purra.ports",
    "purra.recovery",
    "purra.run_control",
    "purra.run_state",
    "purra.stream_ownership",
    "purra.structured_output",
    "purra.task_admission",
    "purra.tools",
})


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return tuple(names)


def _application_python_files() -> tuple[Path, ...]:
    roots = (
        BACKEND_DIR / "application",
        BACKEND_DIR / "domains",
        BACKEND_DIR / "routers",
    )
    return tuple(path for root in roots for path in sorted(root.rglob("*.py")))


def _production_python_files() -> tuple[Path, ...]:
    roots = (
        BACKEND_DIR / "application",
        BACKEND_DIR / "domains",
        BACKEND_DIR / "infrastructure",
        BACKEND_DIR / "routers",
    )
    return tuple(path for root in roots for path in sorted(root.rglob("*.py")))


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


def test_purra_is_pinned_and_loaded_as_an_external_distribution():
    requirement = (BACKEND_DIR / "requirements-purra.txt").read_text(
        encoding="utf-8"
    ).strip()
    package_path = Path(purra.__file__).resolve()

    assert requirement == PURRA_REQUIREMENT
    assert metadata.version("purra") == PURRA_VERSION
    assert metadata.distribution("purra").read_text("direct_url.json") is None
    assert "/site-packages/purra/" in package_path.as_posix()
    assert "/packages/purra/src/" not in package_path.as_posix()
    assert not (ROOT_DIR / "packages" / "purra").exists()


def test_product_code_imports_only_supported_purra_module_roots():
    violations = [
        f"{_relative(path)} imports unsupported {module}"
        for path in _production_python_files()
        for module in _imports(path)
        if module == "purra" or (
            module.startswith("purra.")
            and module not in PUBLIC_PURRA_HOST_MODULES
        )
    ]
    assert not violations, "Private PurrA imports from product code:\n" + "\n".join(
        violations
    )


def test_complete_agent_execution_uses_the_public_api():
    violations: list[str] = []
    for path in sorted((BACKEND_DIR / "application").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "AgentCore" not in source:
            continue
        imports = _imports(path)
        if "purra.engine" in imports:
            violations.append(f"{_relative(path)} imports purra.engine")
    assert not violations, "Agent execution bypasses purra.api:\n" + "\n".join(
        violations
    )


def test_raw_provider_gateway_is_confined_to_the_composition_root():
    observed = {
        _relative(path)
        for path in _application_python_files()
        if "infrastructure.models.provider_model_gateway" in _imports(path)
    }
    unmanaged = observed - ALLOWED_PROVIDER_COMPOSITION
    assert not unmanaged, "Unmanaged ProviderModelGateway imports:\n" + "\n".join(
        sorted(unmanaged)
    )


def test_business_code_cannot_construct_direct_model_invocations():
    observed: set[str] = set()
    for path in _application_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ModelInvocation"
            for node in ast.walk(tree)
        ):
            observed.add(_relative(path))
    assert not observed, "Direct ModelInvocation construction:\n" + "\n".join(
        sorted(observed)
    )
