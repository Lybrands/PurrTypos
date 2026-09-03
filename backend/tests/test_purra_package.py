"""Host-side ratchets for the external PurrA dependency."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

import purra
import purra_anthropic
import purra_mem0
import purra_openai
import pytest


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
PURRA_VERSION = "0.5.0"
PURRA_MEM0_VERSION = "0.5.0"
PURRA_REQUIREMENTS = (
    "./backend/vendor/purra-0.5.0-py3-none-any.whl",
    "./backend/vendor/purra_openai-0.5.0-py3-none-any.whl",
    "./backend/vendor/purra_anthropic-0.5.0-py3-none-any.whl",
    "-e ../purra/integrations/mem0/python[managed]",
)
PURRA_WHEEL_SHA256 = {
    "purra": "54205c17c840dd28d2748dd237514ce280cb98ef388131ad6862f36c8fa97f7b",
    "purra_openai": "0519aca750861206564431fd50153db669c56dd713d575ec2a658f577e76faf4",
    "purra_anthropic": "b26b999470a7d4e29bc92478fd86527cb332fc0801a1356b7157d1705634dff9",
}
RUNTIME_CONSTRAINTS = {
    "httpx>=0.28.0,<1",
    "httpx2>=2.7.0,<3",
    "openai==3.7.0",
    "anthropic==1.3.0",
    "pydantic>=2.9.0,<3",
}
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
    # Narrow exported lifecycle contracts; do not import controller internals
    # or reimplement operation state/timing in the host.
    "purra.operations",
    "purra.orphan_recovery",
    "purra.output",
    "purra.ports",
    "purra.recovery",
    "purra.retrieval",
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


@pytest.mark.parametrize("package", [purra, purra_openai, purra_anthropic])
def test_purra_is_loaded_from_the_pinned_local_wheel(package):
    requirements = tuple(
        line.strip()
        for line in (BACKEND_DIR / "requirements-purra.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    )
    name = package.__name__
    wheel_path = BACKEND_DIR / "vendor" / f"{name}-{PURRA_VERSION}-py3-none-any.whl"
    package_path = Path(package.__file__).resolve()
    distribution = metadata.distribution(name)
    distribution_root = Path(distribution.locate_file("")).resolve()

    assert requirements == PURRA_REQUIREMENTS
    assert hashlib.sha256(wheel_path.read_bytes()).hexdigest() == PURRA_WHEEL_SHA256[name]
    assert metadata.version(name) == PURRA_VERSION
    direct_url = json.loads(distribution.read_text("direct_url.json"))
    installed_from = Path(unquote(urlparse(direct_url["url"]).path)).resolve()
    assert installed_from == wheel_path.resolve()
    assert direct_url["archive_info"]["hashes"]["sha256"] == PURRA_WHEEL_SHA256[name]
    assert package_path == distribution_root / name / "__init__.py"
    assert "site-packages" in package_path.parts
    assert not package_path.is_relative_to((ROOT_DIR.parent / "purra").resolve())
    assert "/packages/purra/src/" not in package_path.as_posix()
    assert not (ROOT_DIR / "packages" / "purra").exists()

    script = (ROOT_DIR / "scripts" / "prepare-backend-resources.cjs").read_text(encoding="utf-8")
    assert PURRA_WHEEL_SHA256[name] in script
    assert f"./backend/vendor/{wheel_path.name}" in script


def test_provider_gateway_exports_come_from_installed_wheels():
    for package, gateway in (
        (purra_openai, purra_openai.OpenAIChatCompletionsGateway),
        (purra_openai, purra_openai.OpenAIResponsesGateway),
        (purra_anthropic, purra_anthropic.AnthropicMessagesGateway),
    ):
        assert Path(inspect.getfile(gateway)).resolve().is_relative_to(
            Path(package.__file__).resolve().parent
        )


def test_planning_mode_and_public_progress_contracts_come_from_installed_wheel():
    from purra.api import PlanningMode
    from purra.contracts import AgentRunRequest
    from purra.output import AgentOutputEvent, OutputEventKind

    source = Path(metadata.distribution("purra").locate_file("purra")).resolve()
    exported = (AgentRunRequest, PlanningMode, AgentOutputEvent, OutputEventKind)

    assert all(
        Path(inspect.getfile(contract)).resolve().is_relative_to(source)
        for contract in exported
    )
    assert AgentRunRequest.__dataclass_fields__["planning_mode"].default is PlanningMode.AUTO
    assert OutputEventKind.PLANNING_PROGRESS.value == "planning.progress"


def test_product_code_has_no_removed_planning_policy_compatibility_layer():
    forbidden = (
        "ReactivePlanningPolicy",
        "ToolPlanningPolicy",
        "RequiredToolPlanningPolicy",
        "should_plan",
        "planning_policies",
        "application.planning_constraints",
    )
    violations = [
        f"{_relative(path)} contains {symbol}"
        for path in _production_python_files()
        for symbol in forbidden
        if symbol in path.read_text(encoding="utf-8")
    ]

    assert not violations, "Removed planning compatibility remains:\n" + "\n".join(
        violations
    )


def test_purra_mem0_is_loaded_from_the_local_editable_distribution():
    package_path = Path(purra_mem0.__file__).resolve()
    source = (ROOT_DIR.parent / "purra" / "integrations" / "mem0" / "python").resolve()

    assert metadata.version("purra-mem0") == PURRA_MEM0_VERSION
    direct_url = json.loads(
        metadata.distribution("purra-mem0").read_text("direct_url.json")
    )
    assert direct_url == {"url": source.as_uri(), "dir_info": {"editable": True}}
    assert package_path == source / "src" / "purra_mem0" / "__init__.py"


def test_runtime_distribution_constrains_shared_provider_dependencies():
    requirements = {
        line.strip()
        for line in (BACKEND_DIR / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert RUNTIME_CONSTRAINTS <= requirements

    script = (ROOT_DIR / "scripts" / "prepare-backend-resources.cjs").read_text(
        encoding="utf-8"
    )
    assert "requirements-runtime.txt" in script
    assert "'--no-deps'" not in script


def test_product_code_never_imports_the_mem0_sdk_directly():
    violations = [
        _relative(path)
        for path in _production_python_files()
        if any(
            module == "mem0" or module.startswith("mem0.")
            for module in _imports(path)
        )
    ]
    assert not violations, "Direct Mem0 SDK imports:\n" + "\n".join(violations)


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


def test_installed_operation_lifecycle_exports_support_shared_planning():
    from purra import operations

    required = {
        "AgentOperationController", "OperationDisplay", "OperationKind",
        "OperationScope", "OperationStarted", "OperationFinished",
    }
    assert required <= set(operations.__all__)
    assert all(getattr(operations, name) is not None for name in required)


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
