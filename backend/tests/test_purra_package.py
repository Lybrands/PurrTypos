"""Host-side ratchets for the independently packaged PurrA framework."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
PACKAGE_DIR = ROOT_DIR / "packages" / "purra"
PORTABLE_CONFORMANCE = (
    PACKAGE_DIR / "tests" / "test_standalone_agent_conformance.py"
)
SECOND_HOST_CONFORMANCE = (
    PACKAGE_DIR / "tests" / "test_second_host_conformance.py"
)
HOST_ADAPTER_CONFORMANCE = PACKAGE_DIR / "src" / "purra" / "testing.py"
PACKAGE_READMES = (
    PACKAGE_DIR / "README.md",
    PACKAGE_DIR / "README.zh-CN.md",
)

ALLOWED_PROVIDER_COMPOSITION = {
    "application/agent_composition.py",
}


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


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


def test_purra_is_an_independent_dependency_free_distribution():
    assert not (BACKEND_DIR / "purra").exists()
    metadata = tomllib.loads(
        (PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = metadata["project"]
    assert project["name"] == "purra"
    assert project.get("dependencies") == []
    assert (PACKAGE_DIR / "src" / "purra" / "api" / "__init__.py").is_file()
    assert not (BACKEND_DIR / "application" / "screenplay_model_run.py").exists()


def test_public_purra_readmes_do_not_bind_the_framework_to_a_product():
    forbidden = ("purrtypos", "screenplay", "writing-owned", "剧本", "写作领域")
    violations = [
        f"{path.name} contains {term!r}"
        for path in PACKAGE_READMES
        for term in forbidden
        if term in path.read_text(encoding="utf-8").casefold()
    ]
    assert not violations, "PurrA public docs contain product semantics:\n" + "\n".join(
        violations
    )


def test_portable_agent_conformance_uses_only_public_purra_boundaries():
    source = PORTABLE_CONFORMANCE.read_text(encoding="utf-8")
    imported_roots = {
        name.split(".", 1)[0]
        for name in _imports(PORTABLE_CONFORMANCE)
    }

    assert imported_roots <= {
        "__future__",
        "asyncio",
        "dataclasses",
        "datetime",
        "pytest",
        "purra",
    }
    assert "_execute_run" not in source
    assert "core.submit(" in source
    assert "backend" not in imported_roots


def test_host_adapter_conformance_has_no_test_framework_or_host_dependency():
    imported_roots = {
        name.split(".", 1)[0]
        for name in _imports(HOST_ADAPTER_CONFORMANCE)
    }

    assert imported_roots <= {
        "__future__",
        "asyncio",
        "collections",
        "dataclasses",
        "datetime",
        "json",
        "purra",
        "uuid",
    }
    assert "pytest" not in imported_roots
    assert "backend" not in imported_roots


def test_second_host_uses_public_composition_and_keeps_its_style_out_of_core():
    source = SECOND_HOST_CONFORMANCE.read_text(encoding="utf-8")
    imported_roots = {
        name.split(".", 1)[0]
        for name in _imports(SECOND_HOST_CONFORMANCE)
    }
    core_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PACKAGE_DIR / "src" / "purra").rglob("*.py"))
    )

    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "json",
        "pytest",
        "purra",
    }
    assert "core.submit(" in source
    assert "MessageOrigin.HOST_CONTEXT" in source
    assert "_execute_run" not in source
    assert "backend" not in imported_roots
    assert "operations.incident" not in core_source
    assert "readServiceStatus" not in core_source


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
