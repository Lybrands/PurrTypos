"""Structural ratchets for PurrA package boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT_DIR / "packages" / "purra" / "src" / "purra"

ORCHESTRATOR_LINE_CAPS = {
    "runtime/orchestrator.py": 2899,
    "engine/orchestrator.py": 1975,
}

PACKAGE_LINE_CAPS = {
    "contracts/__init__.py": 1947,
    "ports/__init__.py": 58,
}

MOVED_TOP_LEVEL_DEFINITIONS = {
    "runtime/orchestrator.py": {
        "_agent_model_call",
        "_ModelRoundAccumulator",
        "_PendingProviderAttempt",
        "_QueueEventSink",
        "_declined_final_response",
        "_exact_item_count_repair_guidance",
        "_failed_tool_final_response",
        "_is_deferred_action_only_response",
        "_is_textual_tool_call",
        "_is_unstructured_tool_output",
        "_response_constraint_repair_guidance",
        "_should_hold_potential_deferred_response",
        "_stream_tool_batch",
        "_top_level_numbered_items",
    },
    "engine/orchestrator.py": {
        "AgentCoreRunOptions",
        "_AugmentedToolCatalog",
        "_BufferedEventSink",
        "_DynamicPlanningOrchestrator",
        "_assemble_messages",
        "_bind_event_to_run",
        "_compile_task_context_request",
        "_complete_admitted_task",
        "_context_demand_diagnostics",
        "_effective_registrations",
        "_host_planning_facts",
        "_merge_context_claims",
        "_planned_tool_names",
        "_planning_tool_guidance",
        "_runtime_output_event",
        "_validate_context_allocations",
        "_validate_plan_authority",
        "_validate_planning_constraints",
        "_validate_task_admission_coverage",
        "_validate_task_constraint_refinement",
        "_safe_model_only_plan",
    },
    "contracts/__init__.py": {
        "ApprovalDecision",
        "ApprovalStatus",
        "DelegationStatus",
        "MessageOrigin",
        "MessageRole",
        "ModelFinishReason",
        "PlanningKind",
        "ReasoningMode",
        "RunStatus",
        "RuntimeOutcome",
        "StepExecutor",
        "StepStatus",
        "StepType",
        "ToolBatchOutcome",
        "ToolChoiceMode",
        "ToolExecutionMode",
        "ToolRiskLevel",
    },
    "ports/__init__.py": {
        "RunBeginResult",
        "RunCommit",
        "RunRepository",
        "validate_run_commit_lifecycle",
    },
}

REQUIRED_CORE_MODULES = {
    "contracts/enums.py",
    "contracts/host.py",
    "engine/canonical_sink.py",
    "engine/context_phase.py",
    "engine/durable_execution.py",
    "engine/dynamic_planning.py",
    "engine/options.py",
    "engine/planning_validation.py",
    "engine/tool_catalog.py",
    "ports/context.py",
    "ports/model.py",
    "ports/persistence.py",
    "ports/planning.py",
    "ports/projection.py",
    "ports/run_lifecycle.py",
    "ports/tools.py",
    "runtime/model_round.py",
    "runtime/response_finalization.py",
    "runtime/tool_round.py",
}


def _top_level_definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))
    }


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_purra_uses_focused_packages():
    for legacy_name in ("contracts.py", "engine.py", "ports.py", "runtime.py"):
        assert not (CORE_DIR / legacy_name).exists(), legacy_name
    missing = sorted(
        relative
        for relative in REQUIRED_CORE_MODULES
        if not (CORE_DIR / relative).is_file()
    )
    assert not missing, "Missing PurrA modules: " + ", ".join(missing)


def test_extracted_definitions_cannot_return_to_facades_or_orchestrators():
    violations: list[str] = []
    for relative, forbidden in MOVED_TOP_LEVEL_DEFINITIONS.items():
        observed = _top_level_definitions(CORE_DIR / relative)
        for name in sorted(observed & forbidden):
            violations.append(f"{relative} defines {name}")
    assert not violations, "PurrA responsibilities moved backwards:\n" + "\n".join(
        violations
    )


def test_orchestrators_and_packages_can_only_shrink():
    violations: list[str] = []
    for relative, cap in {**ORCHESTRATOR_LINE_CAPS, **PACKAGE_LINE_CAPS}.items():
        observed = _line_count(CORE_DIR / relative)
        if observed > cap:
            violations.append(f"{relative}: {observed} lines, cap {cap}")
    assert not violations, "PurrA monoliths grew:\n" + "\n".join(violations)
