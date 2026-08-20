"""Ratchet guards for the PurrA and screenplay conversation rebuild.

The allowlists in this file describe *known architectural debt*, not approved
design.  A refactor may remove any listed occurrence without updating the
baseline.  Adding a new dependency, route, field, or generic UI reference must
fail until the architecture review deliberately changes this guard.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
PURRA_DIR = ROOT_DIR / "packages" / "purra" / "src" / "purra"
SCREENPLAY_DOMAIN_DIR = BACKEND_DIR / "domains" / "screenplay"
GENERIC_CHUNK_HANDLER_DIR = (
    ROOT_DIR / "src" / "agent-runtime" / "chunkHandlers"
)
SHARED_AGENT_FRONTEND_DIRS = (
    ROOT_DIR / "src" / "agent-runtime",
    ROOT_DIR / "src" / "components" / "AgentConversation",
)
LEGACY_AGENT_UI_PATHS = (
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "ChatMessageList",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "WorkLog",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "TaskPlanCard",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "Markdown",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "ModelPicker",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "ContextUsageIndicator",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "hooks" / "chat.types.ts",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "hooks" / "chunkHandlers",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "hooks" / "chatHistory.ts",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "hooks" / "streamOptions.ts",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "taskPlanSelection.ts",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "contextUsage.ts",
)
SCREENPLAY_CONVERSATION_FRONTEND_FILES = (
    ROOT_DIR / "src" / "ScreenplayAgentPage" / "conversationClient.ts",
    ROOT_DIR / "src" / "ScreenplayAgentPage" / "conversationState.ts",
)
SCREENPLAY_PAGE = ROOT_DIR / "src" / "ScreenplayAgentPage" / "index.tsx"
SCREENPLAY_CONVERSATION_CONTROLLER = (
    ROOT_DIR
    / "src"
    / "ScreenplayAgentPage"
    / "useScreenplayConversationController.ts"
)
SHARED_AGENT_CONVERSATION_PANEL = (
    ROOT_DIR / "src" / "components" / "AgentConversation" / "Panel.tsx"
)
SCREENPLAY_CONVERSATION_PRODUCTION_PATHS = (
    BACKEND_DIR / "application" / "screenplay_agent_service.py",
    BACKEND_DIR / "application" / "screenplay_agent_stream.py",
    BACKEND_DIR / "application" / "screenplay_agent_task_executor.py",
    BACKEND_DIR / "application" / "screenplay_structured_call.py",
    ROOT_DIR / "src" / "ScreenplayAgentPage" / "conversationState.ts",
    ROOT_DIR
    / "src"
    / "components"
    / "AgentConversation"
    / "AssistantOutput"
    / "index.tsx",
    ROOT_DIR
    / "src"
    / "components"
    / "AgentConversation"
    / "AssistantOutput"
    / "timeline.ts",
    SCREENPLAY_CONVERSATION_CONTROLLER,
    SCREENPLAY_PAGE,
)

RETIRED_SCREENPLAY_PLANNER = (
    BACKEND_DIR / "application" / "screenplay_agent_planner.py"
)
REMOVED_COMPATIBILITY_PATHS = (
    BACKEND_DIR / "domains" / "agent_roles.py",
    BACKEND_DIR / "domains" / "writing" / "agent_roles.py",
    ROOT_DIR / "src" / "ScreenplayAgentPage" / "longTaskConversationAdapter.ts",
    ROOT_DIR / "src" / "ScreenplayAgentPage" / "proposalProvenance.ts",
    BACKEND_DIR / "application" / "screenplay_long_task_conversation.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_screenplay_proposal_source.py",
    BACKEND_DIR / "schemas" / "screenplay_agent_run.py",
    BACKEND_DIR / "schemas" / "screenplay_conversation.py",
    ROOT_DIR / "src" / "ScreenplayAgentPage" / "screenplayConversationRuntime.ts",
)
GENERIC_RUNTIME_PERSISTENCE_FILES = (
    BACKEND_DIR / "infrastructure" / "persistence" / "sqlite_run_repository.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_long_task_repository.py",
    BACKEND_DIR
    / "infrastructure"
    / "persistence"
    / "sqlite_artifact_repository.py",
)

FORBIDDEN_SCREENPLAY_DOMAIN_IMPORT_ROOTS = {
    "application",
    "dependencies",
    "infrastructure",
    "routers",
    "schemas",
}

# Phase 2 closed this debt completely. Any database import is now a regression.
SCREENPLAY_DOMAIN_DATABASE_DEBT: set[tuple[str, str]] = set()

# These files are generic application surfaces.  Their screenplay references
# must monotonically decrease until product composition and transport are split.
GENERIC_APPLICATION_SCREENPLAY_DEBT_CAPS = {
    "application/agent_composition.py": 0,
    "application/agent_run_queries.py": 0,
    "application/agent_run_service.py": 0,
    "application/request_mapping.py": 0,
    "application/run_execution_control.py": 0,
    "application/sse_mapping.py": 0,
}

CHAT_STREAM_SCREENPLAY_FIELD_DEBT: set[str] = set()

AI_ROUTER_PRODUCT_ROUTE_DEBT: set[str] = set()

GENERIC_FRONTEND_SCREENPLAY_DEBT_CAPS: dict[str, int] = {}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            result.append(node.module)
    return result


def _relative_backend(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


def _screenplay_count(path: Path) -> int:
    return path.read_text(encoding="utf-8").casefold().count("screenplay")


def _source_between(source: str, start: str, end: str) -> str:
    assert source.count(start) == 1, f"Expected one source anchor: {start}"
    assert source.count(end) == 1, f"Expected one source anchor: {end}"
    assert source.index(start) < source.index(end), (
        "start anchor must precede end anchor"
    )
    return source.split(start, 1)[1].split(end, 1)[0]


def _class_fields(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
    raise AssertionError(f"{class_name} not found in {path}")


def _router_paths(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                result.add(first.value)
    return result


def test_screenplay_domain_never_depends_on_upper_or_infrastructure_layers():
    violations: list[str] = []
    for path in sorted(SCREENPLAY_DOMAIN_DIR.rglob("*.py")):
        for imported in _imports(path):
            if imported.split(".", 1)[0] in FORBIDDEN_SCREENPLAY_DOMAIN_IMPORT_ROOTS:
                violations.append(f"{_relative_backend(path)} imports {imported}")

    assert not violations, "Screenplay domain dependency violations:\n" + "\n".join(
        violations
    )


def test_screenplay_domain_database_debt_can_only_shrink():
    observed: set[tuple[str, str]] = set()
    for path in sorted(SCREENPLAY_DOMAIN_DIR.rglob("*.py")):
        for imported in _imports(path):
            if imported.split(".", 1)[0] == "database":
                observed.add((_relative_backend(path), imported))

    unexpected = observed - SCREENPLAY_DOMAIN_DATABASE_DEBT
    assert not unexpected, "New screenplay domain database dependencies:\n" + "\n".join(
        f"{path} imports {module}" for path, module in sorted(unexpected)
    )


def test_generic_application_screenplay_debt_can_only_shrink():
    violations: list[str] = []
    for relative, cap in GENERIC_APPLICATION_SCREENPLAY_DEBT_CAPS.items():
        observed = _screenplay_count(BACKEND_DIR / relative)
        if observed > cap:
            violations.append(f"{relative}: {observed} references, baseline cap {cap}")

    assert not violations, "Generic application screenplay debt grew:\n" + "\n".join(
        violations
    )


def test_generic_chat_request_screenplay_fields_can_only_shrink():
    fields = _class_fields(BACKEND_DIR / "schemas" / "ai.py", "ChatStreamRequest")
    product_fields = {
        field
        for field in fields
        if "screenplay" in field.casefold()
        or field in {"activeDocumentId", "activeStage", "sourceBookId"}
    }
    unexpected = product_fields - CHAT_STREAM_SCREENPLAY_FIELD_DEBT

    assert not unexpected, "New product fields in ChatStreamRequest: " + ", ".join(
        sorted(unexpected)
    )


def test_generic_ai_router_product_routes_can_only_shrink():
    routes = _router_paths(BACKEND_DIR / "routers" / "ai.py")
    product_routes = {
        route
        for route in routes
        if "screenplay" in route.casefold() or "long-tasks" in route.casefold()
    }
    unexpected = product_routes - AI_ROUTER_PRODUCT_ROUTE_DEBT

    assert not unexpected, "New product routes in generic AI router: " + ", ".join(
        sorted(unexpected)
    )


def test_generic_frontend_screenplay_debt_can_only_shrink():
    generic_paths = [
        *sorted(GENERIC_CHUNK_HANDLER_DIR.glob("*.ts")),
    ]
    violations: list[str] = []
    for path in generic_paths:
        relative = path.relative_to(ROOT_DIR).as_posix()
        observed = _screenplay_count(path)
        cap = GENERIC_FRONTEND_SCREENPLAY_DEBT_CAPS.get(relative, 0)
        if observed > cap:
            violations.append(f"{relative}: {observed} references, baseline cap {cap}")

    assert not violations, "Generic frontend screenplay debt grew:\n" + "\n".join(
        violations
    )


def test_shared_agent_frontend_never_depends_on_product_directories():
    violations: list[str] = []
    for directory in SHARED_AGENT_FRONTEND_DIRS:
        for path in sorted((*directory.rglob("*.ts"), *directory.rglob("*.tsx"))):
            text = path.read_text(encoding="utf-8")
            if (
                "Workspace/AiPanel" in text
                or "ScreenplayAgentPage" in text
                or "/services" in text
            ):
                violations.append(path.relative_to(ROOT_DIR).as_posix())
    assert not violations, "Shared Agent frontend imports product code:\n" + "\n".join(
        violations
    )


def test_legacy_agent_ui_paths_are_removed():
    restored = [
        path.relative_to(ROOT_DIR).as_posix()
        for path in LEGACY_AGENT_UI_PATHS
        if path.exists()
    ]
    assert not restored, "Legacy Agent UI paths returned:\n" + "\n".join(restored)


def test_rewritten_screenplay_agent_routes_are_complete():
    routes = _router_paths(
        BACKEND_DIR / "routers" / "screenplay_conversations.py"
    )
    required = {
        "/projects/{project_id}/conversation/turns",
        "/projects/{project_id}/conversation/snapshot",
        "/projects/{project_id}/conversation/events",
        "/conversation/turns/{turn_id}/and-after",
        "/conversation/turns/{turn_id}/cancel",
    }
    assert required <= routes, "Missing native Conversation routes: " + ", ".join(
        sorted(required - routes)
    )


def test_screenplay_transport_does_not_parse_or_aggregate_model_runs():
    forbidden = {
        "agent-runtime",
        "AiPanel/hooks/chunkHandlers",
        "aiChatStream",
        "onAiChunk",
        "dispatchChunk",
        "parseConversationsFromApi",
        "ScreenplayAgentRunEvent",
        "agent_run_events",
        "JSON.parse",
    }
    violations: list[str] = []
    for path in SCREENPLAY_CONVERSATION_FRONTEND_FILES:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                violations.append(f"{path.name} contains {token}")
    assert not violations, "Screenplay transport owns model stream logic:\n" + "\n".join(
        violations
    )


def test_screenplay_conversation_never_invents_assistant_copy():
    forbidden = {
        "请求理解：",
        "结构化剧本任务已完成。",
        "剧本任务已完成，候选稿已生成。请在下方预览并应用。",
        "剧本创作任务",
        "Durable task completed.",
        "理解请求",
        "拆解任务",
        "组织回复",
    }
    violations = [
        f"{path.relative_to(ROOT_DIR).as_posix()}: {text}"
        for path in SCREENPLAY_CONVERSATION_PRODUCTION_PATHS
        for text in forbidden
        if text in path.read_text(encoding="utf-8")
    ]
    assert not violations, "Host-authored assistant copy returned:\n" + "\n".join(
        violations
    )

    service = (
        BACKEND_DIR / "application" / "screenplay_agent_service.py"
    ).read_text(encoding="utf-8")
    assert "assistant_content=result.final_response" not in service


def test_screenplay_page_projects_durable_content_from_the_canonical_entry():
    source = SCREENPLAY_PAGE.read_text(encoding="utf-8")
    projection = _source_between(
        source,
        "  const agentMessages = React.useMemo<AgentConversationMessage[]>(() => (",
        "  const activeConversationTask = React.useMemo",
    )

    assert len(re.findall(r"\bcontent:\s*entry\.content\s*,", projection)) == 1
    assert re.search(
        r"\bcontent:\s*operation\s*\?\s*canonical\.content\s*:",
        projection,
    )


def test_screenplay_page_consumes_the_shared_agent_chunk_runtime():
    forbidden = {
        "aiChatStream",
        "startScreenplayV2Operation",
        "dispatchChunk",
        "services.conversations",
        "getLatestSessionAgentRun",
        "streamLongTaskConversation",
        "cancelAgentRun",
        "abortAiStream",
        "longTaskConversationAdapter",
        "proposalProvenance",
    }
    source = SCREENPLAY_PAGE.read_text(encoding="utf-8")
    violations = sorted(token for token in forbidden if token in source)
    assert not violations, "Legacy screenplay page runtime returned: " + ", ".join(
        violations
    )
    assert "AgentChunkReplay" in source
    assert "onChunks:" in source
    assert "truncateFromTurn" in source


def test_screenplay_edit_wiring_reaches_the_shared_panel_controller():
    source = SCREENPLAY_PAGE.read_text(encoding="utf-8")
    controller_source = SCREENPLAY_CONVERSATION_CONTROLLER.read_text(
        encoding="utf-8"
    )
    panel_source = SHARED_AGENT_CONVERSATION_PANEL.read_text(encoding="utf-8")
    actions = _source_between(
        source,
        "  const screenplayConversationActions = React.useMemo<",
        "  const screenplayConversationBindings = React.useMemo<",
    )
    bindings = _source_between(
        source,
        "  const screenplayConversationBindings = React.useMemo<",
        "  const screenplayConversationController = useScreenplayConversationController(",
    )
    controller = _source_between(
        source,
        "  const screenplayConversationController = useScreenplayConversationController(",
        "  const screenplayConversationExtensions = useScreenplayConversationExtensions(",
    )
    controller_mapping = _source_between(
        controller_source,
        "export function createScreenplayConversationController(",
        "export function useScreenplayConversationController(",
    )
    controller_hook = controller_source.split(
        "export function useScreenplayConversationController(",
        1,
    )[1]

    assert re.search(
        r"\beditMessage:\s*\(index,\s*content\)\s*=>\s*"
        r"runAgent\(content,\s*index\)\s*,",
        actions,
    )
    assert re.search(r"\bactions:\s*screenplayConversationActions\s*,", bindings)
    assert re.search(r"^\s*screenplayConversationBindings,\s*\)$", controller)
    assert len(re.findall(
        r"\{screenplayConversationController\s*\?\s*\(\s*"
        r"<AgentConversationPanel\s+"
        r"controller=\{screenplayConversationController\}",
        source,
    )) == 1
    assert re.search(r"\bactions:\s*bindings\.actions\s*,", controller_mapping)
    assert re.search(
        r"createScreenplayConversationController\(bindings\)",
        controller_hook,
    )
    assert re.search(r"\[bindings\]", controller_hook)
    assert "onEditMessage={controller.actions.editMessage}" in panel_source


def test_removed_compatibility_modules_stay_deleted():
    restored = [
        path.relative_to(ROOT_DIR).as_posix()
        for path in REMOVED_COMPATIBILITY_PATHS
        if path.exists()
    ]
    assert not restored, "Deleted compatibility modules returned: " + ", ".join(
        restored
    )


_RETIRED_SCREENPLAY_SYMBOLS = frozenset({
    "ModelScreenplayIntentPlanner",
    "screenplay_intent_planning",
    "_ANSWER_INSTRUCTION",
    "_PLANNER_INSTRUCTION",
    "_PLANNER_REPAIR",
})
_LEGACY_ROOT_JSON_ALIAS = "plannerRunId"
_LEGACY_ROOT_COLUMN = "planner_run_id"
_BOUNDARY_TEST = Path(__file__).resolve()

_PLANNER_STORAGE_ROLES: dict[str, frozenset[str]] = {
    "backend/application/screenplay_agent_service.py": frozenset({"read"}),
    "backend/database/crud/screenplay_agent_runtime_cleanup.py": frozenset(
        {"cleanup"}
    ),
    "backend/database/screenplay_agent_schema.py": frozenset({"schema"}),
    "backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py":
        frozenset({"read", "write"}),
    "backend/infrastructure/persistence/sqlite_screenplay_operation_finalizer.py":
        frozenset({"read"}),
    "backend/infrastructure/persistence/sqlite_screenplay_operation_repository.py":
        frozenset({"read"}),
    "backend/infrastructure/screenplay/agent_continuation_begin_projector.py":
        frozenset({"read", "write"}),
    "backend/infrastructure/screenplay/agent_run_cancellation_projector.py":
        frozenset({"read"}),
    "backend/infrastructure/screenplay/agent_root_completion_projector.py":
        frozenset({"read"}),
    "backend/tests/test_agent_run_queries.py": frozenset({"write"}),
    "backend/tests/test_screenplay_agent_durable_service.py": frozenset(
        {"read", "write"}
    ),
    "backend/tests/test_screenplay_agent_runtime_cleanup.py": frozenset(
        {"write"}
    ),
}


def _backend_python_paths() -> tuple[Path, ...]:
    return tuple(
        path for path in sorted(BACKEND_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def _boundary_negative_literal(
    path: Path,
    node: ast.AST,
    needle: str,
) -> bool:
    return (
        path.resolve() == _BOUNDARY_TEST
        and isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and needle in node.value
    )


def _retired_screenplay_violations() -> list[str]:
    violations: list[str] = []
    for path in _backend_python_paths():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        relative = path.relative_to(ROOT_DIR).as_posix()
        for token in _RETIRED_SCREENPLAY_SYMBOLS:
            allowed_lines = {
                node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and node.value == token
                and _boundary_negative_literal(path, node, token)
            }
            violations.extend(
                f"{relative}:{line_number}: {token}"
                for line_number, line in enumerate(source.splitlines(), 1)
                if token in line and line_number not in allowed_lines
            )
    return violations


def _planner_column_violations(
    source_overrides: dict[Path, str] | None = None,
    paths: tuple[Path, ...] | None = None,
) -> list[str]:
    overrides = {
        path.resolve(): source
        for path, source in (source_overrides or {}).items()
    }
    violations: list[str] = []
    for path in paths or _backend_python_paths():
        source = overrides.get(path.resolve())
        if source is None:
            source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        relative = path.relative_to(ROOT_DIR).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name) and node.id == _LEGACY_ROOT_COLUMN
                or isinstance(node, ast.arg) and node.arg == _LEGACY_ROOT_COLUMN
                or isinstance(node, ast.Attribute)
                and node.attr == _LEGACY_ROOT_COLUMN
                or isinstance(node, ast.keyword)
                and node.arg == _LEGACY_ROOT_COLUMN
            ):
                violations.append(
                    f"{relative}:{getattr(node, 'lineno', 0)}: legacy identifier"
                )
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if _LEGACY_ROOT_JSON_ALIAS in node.value:
                if not _boundary_negative_literal(
                    path,
                    node,
                    _LEGACY_ROOT_JSON_ALIAS,
                ):
                    violations.append(
                        f"{relative}:{node.lineno}: legacy JSON alias"
                    )
            if _LEGACY_ROOT_COLUMN not in node.value:
                continue
            if _boundary_negative_literal(path, node, _LEGACY_ROOT_COLUMN):
                continue
            normalized = " ".join(node.value.split())
            upper = normalized.upper()
            if normalized == _LEGACY_ROOT_COLUMN:
                role = "cleanup"
            elif "CREATE TABLE" in upper or "CREATE INDEX" in upper:
                role = "schema"
            elif "SELECT" in upper:
                role = "read"
            elif "INSERT" in upper or "UPDATE" in upper:
                role = "write"
            elif "DELETE" in upper:
                role = "cleanup"
            else:
                role = "unknown"
            if role not in _PLANNER_STORAGE_ROLES.get(relative, frozenset()):
                violations.append(
                    f"{relative}:{node.lineno}: unapproved {role} storage role"
                )
                continue
            if re.search(r"\bAS\s+planner_run_id\b", normalized, re.IGNORECASE):
                violations.append(
                    f"{relative}:{node.lineno}: legacy output alias"
                )
            if re.search(
                r'''["']planner_run_id["']\s*:''',
                normalized,
                re.IGNORECASE,
            ):
                violations.append(
                    f"{relative}:{node.lineno}: legacy JSON key"
                )
            if role == "read" and not re.search(
                r"(?:\b\w+\.)?planner_run_id\s+AS\s+root_run_id\b",
                normalized,
                re.IGNORECASE,
            ):
                violations.append(
                    f"{relative}:{node.lineno}: read must alias root_run_id"
                )
            if role == "schema" and normalized.count(_LEGACY_ROOT_COLUMN) != 1:
                violations.append(
                    f"{relative}:{node.lineno}: schema must define one physical column"
                )
    return violations


def test_independent_screenplay_planner_stays_deleted_everywhere():
    assert not RETIRED_SCREENPLAY_PLANNER.exists()
    violations = _retired_screenplay_violations()
    assert not violations, "Independent Screenplay planner returned:\n" + "\n".join(
        violations
    )


def test_planner_run_alias_is_confined_to_storage_roles_and_v1_decoder():
    violations = _planner_column_violations()
    assert not violations, (
        "legacy Planner identity escaped its storage role allowlist:\n"
        + "\n".join(violations)
    )

    legacy_decoder = (
        ROOT_DIR / "src" / "ScreenplayAgentPage" / "conversationState.ts"
    )
    legacy_test = (
        ROOT_DIR / "src" / "ScreenplayAgentPage" / "conversationState.test.ts"
    )
    frontend_suffixes = {".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs"}
    alias_sources = {
        path: path.read_text(encoding="utf-8")
        for path in (ROOT_DIR / "src").rglob("*")
        if path.is_file()
        and path.suffix in frontend_suffixes
        and _LEGACY_ROOT_JSON_ALIAS in path.read_text(encoding="utf-8")
    }
    assert set(alias_sources) == {legacy_decoder, legacy_test}

    decoder_source = alias_sources[legacy_decoder]
    decoder_boundary = _source_between(
        decoder_source,
        "type LegacyRootRunCarrier = {",
        "export interface ScreenplayTurnArtifact {",
    )
    assert decoder_boundary.count(_LEGACY_ROOT_JSON_ALIAS) == 2
    assert "protocol v2 serializes only rootRunId" in decoder_boundary
    assert _LEGACY_ROOT_JSON_ALIAS not in decoder_source.replace(
        decoder_boundary,
        "",
    )

    test_source = alias_sources[legacy_test]
    marker = (
        "test('legacy snapshot plannerRunId decodes once into canonical rootRunId'"
    )
    before, after = test_source.split(marker, 1)
    fixture, remainder = after.split("function operation(", 1)
    assert _LEGACY_ROOT_JSON_ALIAS not in before + remainder
    assert (marker + fixture).count(_LEGACY_ROOT_JSON_ALIAS) == 5


def test_storage_roles_tolerate_formatting_but_reject_rogue_reads_and_aliases():
    adapter = (
        BACKEND_DIR / "infrastructure" / "persistence"
        / "sqlite_screenplay_agent_repository.py"
    )
    source = adapter.read_text(encoding="utf-8")
    def scan(suffix: str) -> list[str]:
        return _planner_column_violations(
            {adapter: source + suffix},
            paths=(adapter,),
        )

    formatted = (
        "\n_BOUNDARY_FORMATTED = '''SELECT\n"
        "planner_run_id   AS   root_run_id\n"
        "FROM screenplay_agent_turns'''\n"
    )
    assert scan(formatted) == []

    rogue = (
        "\n_BOUNDARY_ROGUE = "
        "'SELECT planner_run_id FROM screenplay_agent_turns'\n"
    )
    assert any(
        "read must alias root_run_id" in item
        for item in scan(rogue)
    )

    wrong_alias = (
        "\n_BOUNDARY_ALIAS = "
        "'SELECT root_run_id AS planner_run_id FROM screenplay_agent_turns'\n"
    )
    assert any(
        "legacy output alias" in item
        for item in scan(wrong_alias)
    )

    json_alias = (
        "\n_BOUNDARY_JSON = "
        "\"UPDATE screenplay_agent_turns SET intent_json = "
        "'{\\\"planner_run_id\\\": \\\"run-1\\\"}'\"\n"
    )
    assert any(
        "legacy JSON key" in item
        for item in scan(json_alias)
    )


def test_phase_four_generic_conversation_contract_has_no_screenplay_fields():
    paths = (
        BACKEND_DIR / "schemas" / "conversations.py",
        BACKEND_DIR / "routers" / "conversations.py",
        BACKEND_DIR
        / "infrastructure"
        / "persistence"
        / "run_conversation_store.py",
    )
    violations = [
        path.relative_to(ROOT_DIR).as_posix()
        for path in paths
        if "screenplay" in path.read_text(encoding="utf-8").casefold()
    ]
    assert not violations, "Generic conversation owns screenplay state: " + ", ".join(
        violations
    )


def test_phase_four_generic_ai_stream_has_no_screenplay_request_shape():
    types_source = (ROOT_DIR / "src" / "types.ts").read_text(encoding="utf-8")
    stream_contract = types_source.split("aiChatStream:", 1)[1].split(
        "abortAiStream:",
        1,
    )[0]
    forbidden_fields = {
        "agentProfile",
        "screenplayProjectId",
        "screenplayOperationId",
        "sourceBookId",
        "activeDocumentId",
        "activeStage",
        "screenplayTaskIntent",
        "screenplayDraftSceneCount",
        "screenplayDraftScope",
    }
    violations = sorted(
        field for field in forbidden_fields if field in stream_contract
    )
    assert not violations, "Legacy screenplay AI stream fields returned: " + ", ".join(
        violations
    )

    live_runtime_paths = (
        ROOT_DIR / "src" / "services" / "backendApi.ts",
        ROOT_DIR / "src" / "components" / "AiDevInspector" / "store.ts",
    )
    leaked_branches = [
        path.relative_to(ROOT_DIR).as_posix()
        for path in live_runtime_paths
        if ".agentProfile" in path.read_text(encoding="utf-8")
    ]
    assert not leaked_branches, "Legacy screenplay live diagnostics returned: " + ", ".join(
        leaked_branches
    )


def test_screenplay_domain_events_never_return_to_generic_ai_wire():
    removed_mapper = BACKEND_DIR / "application" / "screenplay_sse_mapping.py"
    assert not removed_mapper.exists(), "Legacy screenplay SSE mapper returned"
    paths = (ROOT_DIR / "src" / "types.ts",)
    forbidden = {"proposedScreenplayDocument", "screenplayRevisionReady"}
    violations = [
        f"{path.relative_to(ROOT_DIR).as_posix()}: {token}"
        for path in paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert not violations, "Legacy screenplay AI wire returned: " + ", ".join(
        violations
    )


def test_phase_five_generic_runtime_has_no_product_operation_ownership():
    violations = [
        path.relative_to(ROOT_DIR).as_posix()
        for path in GENERIC_RUNTIME_PERSISTENCE_FILES
        if "operation_id" in path.read_text(encoding="utf-8")
    ]
    assert not violations, "Generic runtime owns screenplay Operation: " + ", ".join(
        violations
    )


def test_purra_has_no_product_or_model_identity_branches():
    forbidden = {"deepseek", "kimi", "glm", "zhipu", "minimax", "mimo"}
    violations = [
        f"{path.relative_to(ROOT_DIR).as_posix()}: {token}"
        for path in sorted(PURRA_DIR.rglob("*.py"))
        for token in forbidden
        if token in path.read_text(encoding="utf-8").casefold()
    ]
    assert not violations, "PurrA branches on concrete model identity:\n" + "\n".join(
        violations
    )


def test_removed_failure_and_reasoning_fallback_paths_stay_removed():
    forbidden = {
        "pinned_disabled_after_truncation",
        "reasoning_disabled_attempt",
        "is_retryable_unit_error",
        "is_retryable_screenplay_run_error",
        ".fail_unit(",
    }
    boundary_test = Path(__file__).resolve()
    paths = (
        *sorted(PURRA_DIR.rglob("*.py")),
        *(path for path in sorted(BACKEND_DIR.rglob("*.py")) if path != boundary_test),
    )
    violations = [
        f"{path.relative_to(ROOT_DIR).as_posix()}: {token}"
        for path in paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert not violations, "Removed recovery path returned:\n" + "\n".join(
        violations
    )


def test_screenplay_structured_call_does_not_own_core_recovery_policy():
    path = BACKEND_DIR / "application" / "screenplay_structured_call.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "purra.recovery"
        for alias in node.names
    }

    assert not imported_names.intersection({
        "EMPTY_RESPONSE_RETRY_GUIDANCE",
        "RecoveryCause",
        "RecoveryLedger",
        "RecoveryPolicy",
    })


def test_legacy_task_budgets_and_truncation_replay_stay_removed():
    forbidden = {
        "OutputBudgetPolicy",
        "ResolvedOutputBudget",
        "resolve_output_budget",
        "safety_factor",
        "reasoning_reserve_tokens",
        "hard_cap_tokens",
        "retry_provider_attempt",
        "_TRUNCATED_TOOL_CALL_RETRY_GUIDANCE",
        "_TRUNCATED_MODEL_OUTPUT_RETRY_GUIDANCE",
    }
    boundary_test = Path(__file__).resolve()
    paths = (
        *sorted(PURRA_DIR.rglob("*.py")),
        *sorted((BACKEND_DIR / "application").rglob("*.py")),
        *sorted((BACKEND_DIR / "infrastructure" / "models").rglob("*.py")),
    )
    violations = [
        f"{path.relative_to(ROOT_DIR).as_posix()}: {token}"
        for path in paths
        if path != boundary_test
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]

    assert not violations, "Legacy output control returned:\n" + "\n".join(
        violations
    )


def test_screenplay_profile_cannot_settle_business_terminal_state():
    source = (
        BACKEND_DIR / "application" / "screenplay_agent_profile.py"
    ).read_text(encoding="utf-8")
    settle_start = source.index("async def _settle_screenplay_execution(")
    settle_end = source.index(
        "async def _settle_screenplay_exception(",
        settle_start,
    )
    settle = source[settle_start:settle_end]

    assert "pause_task(" not in settle
    assert "fail_task(" not in settle
    assert "complete_task(" not in settle
    assert "cancel_task(" not in settle


def test_agent_composition_registers_profiles_without_extension_indirection():
    composition = (
        BACKEND_DIR / "application" / "agent_composition.py"
    ).read_text(encoding="utf-8")
    registry = (
        BACKEND_DIR / "application" / "agent_profile_registry.py"
    ).read_text(encoding="utf-8")

    assert "profile_registration(" not in composition
    assert "_profile_extensions" not in composition
    assert "class AgentProfileRegistration" not in registry
    assert "class AgentProfile(Protocol)" in registry
    assert "agent_role_registry" not in composition
    assert "agent_role_registry" not in registry


def test_screenplay_manifest_never_emits_the_obsolete_coarse_units():
    source = (
        BACKEND_DIR / "application" / "screenplay_manifest_compiler.py"
    ).read_text(encoding="utf-8")
    assert 'kind="generate_episode_draft"' not in source
    assert 'kind="generate_deliverable"' not in source
    assert not (
        BACKEND_DIR / "domains" / "screenplay_agent" / "recipe_compiler.py"
    ).exists()
