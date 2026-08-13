"""Public provider-neutral data contracts owned by PurrA."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator, Literal, Mapping, MutableMapping, TypeAlias

from purra.json_values import (
    freeze_json_mapping,
    freeze_json_value,
    thaw_json_mapping,
    thaw_json_value,
)
from purra.model_protocol.capabilities import (
    ModelCapabilitySnapshot,
    ModelOutputCapabilities,
    ModelProtocolCapabilities,
    generic_capability_snapshot,
)
from purra.model_protocol.output_limits import InvocationOutputLimit
from purra.contracts.enums import (
    ApprovalDecision,
    ApprovalStatus,
    DelegationStatus,
    MessageOrigin,
    MessageRole,
    ModelFinishReason,
    PlanningKind,
    ReasoningMode,
    RunId,
    RunStatus,
    RuntimeOutcome,
    SessionId,
    StepExecutor,
    StepStatus,
    StepType,
    TerminalRunStatus,
    ToolBatchOutcome,
    ToolChoiceMode,
    ToolEffectState,
    ToolExecutionMode,
    ToolPlanningDisposition,
    ToolRiskLevel,
    ToolStepDisposition,
)
from purra.contracts.host import (
    ExecutionRecipe,
    ExecutionRecipeStep,
    RunBinding,
)
from purra.normalization import (
    non_negative_int,
    optional_non_negative_int,
    optional_positive_int,
    optional_text as _optional_text,
    positive_int,
    required_text,
    text_frozenset,
    unique_text_tuple,
)
from purra.contracts.tool_paths import tool_data_path as _tool_data_path


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """Provider-neutral message with lossless extra protocol fields."""

    role: MessageRole
    content: Any = None
    reasoning: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    origin: MessageOrigin = MessageOrigin.CALLER
    attributes: Mapping[str, Any] = field(default_factory=dict)
    host_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            role = MessageRole(str(self.role or "").strip())
        except ValueError:
            raise ValueError("unsupported message role")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "content", freeze_json_value(self.content))
        object.__setattr__(self, "reasoning", _optional_text(self.reasoning))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "tool_call_id", _optional_text(self.tool_call_id))
        object.__setattr__(self, "origin", MessageOrigin(self.origin))
        if role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("tool message requires tool_call_id")
        object.__setattr__(self, "attributes", freeze_json_mapping(self.attributes))
        object.__setattr__(
            self,
            "host_metadata",
            freeze_json_mapping(self.host_metadata),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentMessage":
        raw = dict(value)
        # Provenance is assigned only by in-process Core composition. A
        # untrusted external mapping can never claim a host origin.
        raw.pop("origin", None)
        raw.pop("host_metadata", None)
        role = raw.pop("role", "")
        content = raw.pop("content", None)
        reasoning = raw.pop("reasoning", raw.pop("reasoning_content", None))
        tool_call_id = raw.pop("tool_call_id", None)
        tool_calls = tuple(
            _tool_call_from_mapping(item)
            for item in (raw.pop("tool_calls", ()) or ())
            if isinstance(item, Mapping)
        )
        return cls(
            role=role,  # type: ignore[arg-type]
            content=content,
            reasoning=reasoning,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            attributes=raw,
        )

    def to_mapping(self) -> dict[str, Any]:
        value = thaw_json_mapping(self.attributes)
        value.update({"role": self.role.value, "content": thaw_json_value(self.content)})
        if self.reasoning is not None:
            value["reasoning"] = self.reasoning
        if self.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments_json": call.arguments_json,
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            value["tool_call_id"] = self.tool_call_id
        return value


@dataclass(frozen=True, slots=True)
class DomainContext:
    """Opaque immutable request data interpreted only by a domain adapter."""

    namespace: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", required_text(
            self.namespace, "domain context namespace"
        ))
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class ModelRequest:
    provider: str
    model: str
    capability_snapshot: ModelCapabilitySnapshot = field(
        default_factory=generic_capability_snapshot
    )
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", required_text(
            self.provider, "model provider"
        ).lower())
        object.__setattr__(self, "model", required_text(
            self.model, "model name"
        ))
        if not isinstance(self.capability_snapshot, ModelCapabilitySnapshot):
            raise TypeError(
                "model capability snapshot must be ModelCapabilitySnapshot"
            )
        object.__setattr__(self, "options", freeze_json_mapping(self.options))

    @property
    def profile_id(self) -> str:
        return self.capability_snapshot.profile_id

    @property
    def output_capabilities(self) -> ModelOutputCapabilities:
        """Compatibility view until the output-budget migration completes."""

        return self.capability_snapshot.output

    @property
    def protocol_capabilities(self) -> ModelProtocolCapabilities:
        return self.capability_snapshot.protocol


@dataclass(frozen=True, slots=True)
class ModelInvocation:
    request: ModelRequest
    tools: tuple[ToolSchema, ...] = ()
    tool_choice: ToolChoiceMode = ToolChoiceMode.AUTO
    output_limit: InvocationOutputLimit | None = None
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "tool_choice", ToolChoiceMode(self.tool_choice))
        object.__setattr__(self, "reasoning_mode", ReasoningMode(self.reasoning_mode))
        if self.output_limit is not None and not isinstance(
            self.output_limit,
            InvocationOutputLimit,
        ):
            raise TypeError(
                "model output limit must be InvocationOutputLimit"
            )
        if not self.tools and self.tool_choice is ToolChoiceMode.REQUIRED:
            raise ValueError("required tool choice needs at least one tool")

    @property
    def max_output_tokens(self) -> int | None:
        return self.output_limit.max_tokens if self.output_limit is not None else None


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    type: str | None = None
    name: str | None = None
    arguments_fragment: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "index", non_negative_int(
            self.index, "tool call delta index"
        ))
        object.__setattr__(self, "id", _optional_text(self.id))
        object.__setattr__(self, "type", _optional_text(self.type))
        object.__setattr__(self, "name", _optional_text(self.name))
        object.__setattr__(
            self,
            "arguments_fragment",
            str(self.arguments_fragment or ""),
        )


@dataclass(frozen=True, slots=True)
class ModelTokenUsage:
    """Provider-reported token usage for one completed model request.

    ``input_tokens`` is the complete provider input, including cached input
    tokens when the provider reports those as separate counters.
    """

    input_tokens: int
    output_tokens: int = 0
    total_tokens: int | None = None
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_output_tokens",
        ):
            object.__setattr__(self, name, non_negative_int(
                getattr(self, name), name
            ))
        if self.total_tokens is None:
            object.__setattr__(
                self,
                "total_tokens",
                self.input_tokens + self.output_tokens,
            )
        else:
            object.__setattr__(self, "total_tokens", non_negative_int(
                self.total_tokens, "total_tokens"
            ))


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    content_delta: str = ""
    reasoning_delta: str = ""
    tool_call_deltas: tuple[ToolCallDelta, ...] = ()
    finish_reason: ModelFinishReason | None = None
    usage: ModelTokenUsage | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_call_deltas", tuple(self.tool_call_deltas))
        if self.finish_reason is not None:
            object.__setattr__(
                self,
                "finish_reason",
                ModelFinishReason(self.finish_reason),
            )
        if self.usage is not None and not isinstance(
            self.usage,
            ModelTokenUsage,
        ):
            raise TypeError("model stream usage must be ModelTokenUsage")


@dataclass(slots=True)
class ModelStream:
    chunks: AsyncIterator[ModelStreamChunk]
    model: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.model = required_text(self.model, "model stream model name")
        self.metadata = freeze_json_mapping(self.metadata)


@dataclass(frozen=True, slots=True)
class ModelCompletion:
    message: AgentMessage
    model: str
    finish_reason: ModelFinishReason | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    usage: ModelTokenUsage | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", required_text(
            self.model, "model completion model name"
        ))
        if self.finish_reason is not None:
            object.__setattr__(
                self,
                "finish_reason",
                ModelFinishReason(self.finish_reason),
            )
        if self.usage is not None and not isinstance(
            self.usage,
            ModelTokenUsage,
        ):
            raise TypeError("model completion usage must be ModelTokenUsage")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    messages: tuple[AgentMessage, ...]
    model: ModelRequest
    domain_context: DomainContext
    session_id: SessionId | None = None
    mode: str | None = None
    context_window: int | None = None
    tools_enabled: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not all(isinstance(message, AgentMessage) for message in messages):
            raise TypeError("agent run messages must be AgentMessage values")
        if not isinstance(self.model, ModelRequest):
            raise TypeError("agent run model must be ModelRequest")
        if not isinstance(self.domain_context, DomainContext):
            raise TypeError("agent run domain context must be DomainContext")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "mode", _optional_text(self.mode))
        object.__setattr__(self, "tools_enabled", bool(self.tools_enabled))
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
        object.__setattr__(self, "context_window", optional_positive_int(
            self.context_window, "context window"
        ))

    def latest_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                return str(message.content or "")
        return ""


@dataclass(frozen=True, slots=True)
class AgentAssignmentCoverage:
    """Require one Agent role to partition an authenticated value sequence.

    The contract is deliberately domain-neutral: Core never interprets the
    values or the assignment field.  A domain adapter may declare that Agent
    steps of one role must cover an opaque sequence exactly once, in order.
    The Planner still authors the steps, grouping and dependency graph.
    """

    agent_role: str
    assignment_field: str
    required_values: tuple[str, ...]
    root_only: bool = False

    def __post_init__(self) -> None:
        role = required_text(
            self.agent_role, "Agent assignment coverage role"
        )
        field_name = required_text(
            self.assignment_field, "Agent assignment coverage field"
        )
        values = tuple(
            str(value or "").strip()
            for value in self.required_values
        )
        if not values or any(not value for value in values):
            raise ValueError(
                "Agent assignment coverage values must be non-empty"
            )
        if len(values) != len(set(values)):
            raise ValueError(
                "Agent assignment coverage values must be unique"
            )
        if not isinstance(self.root_only, bool):
            raise TypeError("Agent assignment coverage root_only must be boolean")
        object.__setattr__(self, "agent_role", role)
        object.__setattr__(self, "assignment_field", field_name)
        object.__setattr__(self, "required_values", values)

    def to_planning_payload(self) -> dict[str, object]:
        return {
            "agentRole": self.agent_role,
            "assignmentField": self.assignment_field,
            "requiredValues": list(self.required_values),
            "rootOnly": self.root_only,
            "coverage": "exactly_once_in_order",
        }


@dataclass(frozen=True, slots=True)
class PlanningConstraints:
    """Request-scoped limits on tools and Agent roles a planner may select.

    ``context_satisfied_tool_names`` is node-wide: the named tool's result is
    already present in trusted context, so the planner must not call it.
    ``planning_excluded_tool_names`` names tools that are valid runtime
    capabilities but outside this request's explicitly bounded evidence or
    action scope.  Exclusion is not a claim that their results are present.
    ``satisfied_tool_dependency_edges`` is deliberately narrower: only the
    named consumer's dependency is already satisfied, while the dependency
    tool remains available for explicit use and for every other consumer.
    ``required_any_tool_names`` is a generic completion guard: at least one of
    the named tools must be selected (or already completed during replanning).
    ``execution_satisfied_tool_names`` is host-only lowering state.  It names
    private runtime tools whose durable work is already present, without
    advertising those protocol details to the Planner.
    Agent-role exclusions and requirements provide the equivalent generic
    boundary for Planner-authored Agent steps; they never prescribe a domain
    execution topology. ``minimum_root_agent_count`` can require a
    bounded amount of genuinely independent work without naming or creating
    any of those Agent steps. ``planning_excluded_executors`` removes an
    execution mechanism that the request's admitted runtime cannot support.
    ``allow_model_only_fallback`` controls whether invalid Planner output may
    degrade to a side-effect-free response for this request.
    """

    context_satisfied_tool_names: frozenset[str] = frozenset()
    planning_excluded_tool_names: frozenset[str] = frozenset()
    satisfied_tool_dependency_edges: frozenset[tuple[str, str]] = frozenset()
    required_any_tool_names: frozenset[str] = frozenset()
    execution_satisfied_tool_names: frozenset[str] = frozenset()
    planning_excluded_agent_roles: frozenset[str] = frozenset()
    required_any_agent_roles: frozenset[str] = frozenset()
    minimum_root_agent_count: int = 0
    agent_assignment_coverages: tuple[AgentAssignmentCoverage, ...] = ()
    planning_excluded_executors: frozenset[StepExecutor] = frozenset()
    allow_model_only_fallback: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "context_satisfied_tool_names",
            "planning_excluded_tool_names",
            "required_any_tool_names",
            "execution_satisfied_tool_names",
            "planning_excluded_agent_roles",
            "required_any_agent_roles",
        ):
            object.__setattr__(
                self,
                field_name,
                text_frozenset(getattr(self, field_name)),
            )
        minimum_frontier = self.minimum_root_agent_count
        if isinstance(minimum_frontier, bool) or not isinstance(
            minimum_frontier,
            int,
        ):
            raise TypeError(
                "minimum_root_agent_count must be an integer"
            )
        if minimum_frontier < 0:
            raise ValueError(
                "minimum_root_agent_count must be non-negative"
            )
        object.__setattr__(
            self,
            "minimum_root_agent_count",
            minimum_frontier,
        )
        coverages = tuple(self.agent_assignment_coverages)
        if any(
            not isinstance(coverage, AgentAssignmentCoverage)
            for coverage in coverages
        ):
            raise TypeError(
                "agent assignment coverages must be AgentAssignmentCoverage"
            )
        coverage_keys = [
            (coverage.agent_role, coverage.assignment_field)
            for coverage in coverages
        ]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise ValueError(
                "agent assignment coverages must be unique per role and field"
            )
        object.__setattr__(
            self,
            "agent_assignment_coverages",
            coverages,
        )
        try:
            excluded_executors = frozenset(
                StepExecutor(value)
                for value in self.planning_excluded_executors
            )
        except ValueError as error:
            raise ValueError(
                "planning_excluded_executors contains an unsupported executor"
            ) from error
        object.__setattr__(
            self,
            "planning_excluded_executors",
            excluded_executors,
        )
        if not isinstance(self.allow_model_only_fallback, bool):
            raise TypeError("allow_model_only_fallback must be a boolean")
        edges: set[tuple[str, str]] = set()
        for edge in self.satisfied_tool_dependency_edges:
            if not isinstance(edge, (tuple, list)) or len(edge) != 2:
                raise TypeError(
                    "satisfied tool dependency edges must be tool/dependency pairs"
                )
            tool_name = str(edge[0]).strip()
            dependency_name = str(edge[1]).strip()
            if not tool_name or not dependency_name:
                raise ValueError(
                    "satisfied tool dependency edge names must be non-empty"
                )
            edges.add((tool_name, dependency_name))
        object.__setattr__(
            self,
            "satisfied_tool_dependency_edges",
            frozenset(edges),
        )


@dataclass(frozen=True, slots=True)
class ResponseConstraints:
    """Host-owned structural limits for a model's final response."""

    exact_top_level_item_count: int | None = None

    def __post_init__(self) -> None:
        value = self.exact_top_level_item_count
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("exact response item count must be an integer")
        if not 1 <= value <= 100:
            raise ValueError("exact response item count must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class ResponseValidationResult:
    """Business-agnostic result returned by an injected response validator.

    An empty result accepts the response.  A rejected result carries a stable
    machine-readable code plus trusted repair guidance supplied by the host
    adapter; Core only orchestrates withholding and one bounded retry.
    """

    violation_code: str | None = None
    repair_guidance: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        code = _optional_text(self.violation_code)
        guidance = _optional_text(self.repair_guidance)
        if (code is None) != (guidance is None):
            raise ValueError(
                "response validation rejection requires both a violation "
                "code and repair guidance"
            )
        object.__setattr__(self, "violation_code", code)
        object.__setattr__(self, "repair_guidance", guidance)
        object.__setattr__(self, "details", freeze_json_mapping(self.details))

    @property
    def accepted(self) -> bool:
        return self.violation_code is None


@dataclass(frozen=True, slots=True)
class PlanningCapabilities:
    available_tool_names: frozenset[str] = frozenset()
    available_agent_roles: frozenset[str] = frozenset()
    model_supports_tools: bool = True
    host_planning_facts: Mapping[str, Any] = field(default_factory=dict)
    tool_guidance: Mapping[str, Any] = field(default_factory=dict)
    agent_role_guidance: Mapping[str, Any] = field(default_factory=dict)
    max_parallel_agents: int = 1
    constraints: PlanningConstraints = field(default_factory=PlanningConstraints)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_tool_names",
            text_frozenset(self.available_tool_names),
        )
        object.__setattr__(
            self,
            "available_agent_roles",
            text_frozenset(self.available_agent_roles),
        )
        object.__setattr__(self, "model_supports_tools", bool(self.model_supports_tools))
        object.__setattr__(
            self,
            "host_planning_facts",
            freeze_json_mapping(self.host_planning_facts),
        )
        object.__setattr__(
            self,
            "tool_guidance",
            freeze_json_mapping(self.tool_guidance),
        )
        object.__setattr__(
            self,
            "agent_role_guidance",
            freeze_json_mapping(self.agent_role_guidance),
        )
        object.__setattr__(
            self,
            "max_parallel_agents",
            positive_int(self.max_parallel_agents, "max_parallel_agents"),
        )
        if not isinstance(self.constraints, PlanningConstraints):
            raise TypeError("planning constraints must be PlanningConstraints")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Normalized user intent carried across every phase of one Agent Run.

    The planner may propose this semantic summary, but it never grants tools,
    authorizes data access, or declares low-level evidence dependencies.  Those
    remain host-owned contracts.
    """

    goal: str
    target: Mapping[str, Any] = field(default_factory=dict)
    operation: str | None = None
    instruction: str | None = None
    constraints: tuple[str, ...] = ()
    preserve: tuple[str, ...] = ()
    deliverable: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "goal",
            required_text(self.goal, "task spec goal"),
        )
        object.__setattr__(self, "target", freeze_json_mapping(self.target))
        object.__setattr__(self, "operation", _optional_text(self.operation))
        object.__setattr__(self, "instruction", _optional_text(self.instruction))
        object.__setattr__(
            self,
            "constraints",
            unique_text_tuple(self.constraints),
        )
        object.__setattr__(
            self,
            "preserve",
            unique_text_tuple(self.preserve),
        )
        object.__setattr__(self, "deliverable", _optional_text(self.deliverable))

    def to_mapping(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "goal": self.goal,
                "target": thaw_json_mapping(self.target),
                "operation": self.operation,
                "instruction": self.instruction,
                "constraints": list(self.constraints),
                "preserve": list(self.preserve),
                "deliverable": self.deliverable,
            }.items()
            if value not in (None, "", [], {})
        }


@dataclass(frozen=True, slots=True)
class TaskStep:
    id: str
    title: str
    type: StepType
    executor: StepExecutor
    status: StepStatus = StepStatus.PENDING
    risk_level: ToolRiskLevel | None = None
    suggested_tools: tuple[str, ...] = ()
    agent_role: str | None = None
    assignment: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    description: str | None = None
    result_summary: str | None = None
    error: str | None = None
    protocol_private: bool = False
    planning_capability: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", required_text(self.id, "task step id"))
        object.__setattr__(
            self,
            "title",
            required_text(self.title, "task step title"),
        )
        object.__setattr__(self, "type", StepType(self.type))
        object.__setattr__(self, "executor", StepExecutor(self.executor))
        object.__setattr__(self, "status", StepStatus(self.status))
        object.__setattr__(
            self,
            "risk_level",
            ToolRiskLevel(self.risk_level) if self.risk_level is not None else None,
        )
        object.__setattr__(
            self,
            "suggested_tools",
            unique_text_tuple(self.suggested_tools),
        )
        object.__setattr__(self, "agent_role", _optional_text(self.agent_role))
        object.__setattr__(self, "assignment", freeze_json_mapping(self.assignment))
        object.__setattr__(
            self,
            "depends_on",
            unique_text_tuple(self.depends_on),
        )
        if self.executor is StepExecutor.AGENT:
            if self.agent_role is None:
                raise ValueError("agent task step requires agent_role")
            if self.suggested_tools:
                raise ValueError("agent task step cannot grant tools")
        elif self.agent_role is not None or self.assignment:
            raise ValueError(
                "only agent task steps may declare agent_role or assignment"
            )
        object.__setattr__(self, "description", _optional_text(self.description))
        object.__setattr__(self, "result_summary", _optional_text(self.result_summary))
        object.__setattr__(self, "error", _optional_text(self.error))
        if not isinstance(self.protocol_private, bool):
            raise TypeError("task step protocol_private must be a boolean")
        if self.protocol_private and self.executor is not StepExecutor.TOOL:
            raise ValueError("only tool steps may be protocol-private")
        object.__setattr__(
            self,
            "planning_capability",
            _optional_text(self.planning_capability),
        )
        if (
            self.planning_capability is not None
            and self.executor is not StepExecutor.TOOL
        ):
            raise ValueError(
                "only tool steps may reference a planning capability"
            )


@dataclass(frozen=True, slots=True)
class TaskPlan:
    title: str
    steps: tuple[TaskStep, ...]
    goal: str | None = None
    task_spec: TaskSpec | None = None

    def __post_init__(self) -> None:
        title = required_text(self.title, "task plan title")
        steps = tuple(self.steps)
        if not steps:
            raise ValueError("task plan requires at least one step")
        step_ids = [step.id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("task plan step ids must be unique")
        known_ids: set[str] = set()
        for step in steps:
            unknown = set(step.depends_on) - known_ids
            if unknown:
                raise ValueError(
                    "task plan dependencies must reference earlier steps: "
                    + ", ".join(sorted(unknown))
                )
            if step.id in step.depends_on:
                raise ValueError("task plan step cannot depend on itself")
            known_ids.add(step.id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "goal", _optional_text(self.goal))
        if self.task_spec is not None and not isinstance(self.task_spec, TaskSpec):
            raise TypeError("task plan task_spec must be TaskSpec")


@dataclass(frozen=True, slots=True)
class PlannerLimits:
    max_steps: int = 8
    max_step_id_chars: int = 48
    max_title_chars: int = 48
    max_goal_chars: int = 160
    max_tool_steps: int = 4
    max_repair_attempts: int = 1

    def __post_init__(self) -> None:
        for name in (
            "max_steps",
            "max_step_id_chars",
            "max_title_chars",
            "max_goal_chars",
        ):
            object.__setattr__(self, name, positive_int(
                getattr(self, name), name
            ))
        max_tool_steps = int(self.max_tool_steps)
        if max_tool_steps < 0 or max_tool_steps > self.max_steps:
            raise ValueError("max_tool_steps must be between zero and max_steps")
        object.__setattr__(self, "max_tool_steps", max_tool_steps)
        max_repair_attempts = int(self.max_repair_attempts)
        if max_repair_attempts < 0 or max_repair_attempts > 3:
            raise ValueError(
                "max_repair_attempts must be between zero and three"
            )
        object.__setattr__(
            self,
            "max_repair_attempts",
            max_repair_attempts,
        )


@dataclass(frozen=True, slots=True)
class PlanningResult:
    kind: PlanningKind
    plan: TaskPlan
    reason: str | None = None
    model: str | None = None
    model_call_count: int = 0
    model_call_parameters: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PlanningKind(self.kind))
        object.__setattr__(self, "reason", _optional_text(self.reason))
        object.__setattr__(self, "model", _optional_text(self.model))
        call_count = non_negative_int(
            self.model_call_count, "planning model call count"
        )
        parameters = tuple(
            freeze_json_mapping(item)
            for item in self.model_call_parameters
        )
        if parameters:
            call_count = len(parameters)
        object.__setattr__(self, "model_call_count", call_count)
        object.__setattr__(self, "model_call_parameters", parameters)


@dataclass(frozen=True, slots=True)
class PlanningTurn:
    """Runtime evidence supplied when the planner revises future work.

    Completed steps are immutable history. ``messages`` includes the trusted
    tool-result continuation accumulated by the runtime, allowing the planner
    to choose the next transition from observed results instead of committing
    the whole run before execution starts.
    """

    revision: int
    round_number: int
    remaining_model_rounds: int
    messages: tuple[AgentMessage, ...]
    completed_steps: tuple[TaskStep, ...] = ()
    last_tool_outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision", positive_int(
            self.revision, "planning turn revision"
        ))
        object.__setattr__(self, "round_number", positive_int(
            self.round_number, "planning turn round number"
        ))
        object.__setattr__(self, "remaining_model_rounds", non_negative_int(
            self.remaining_model_rounds, "remaining model rounds"
        ))
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "completed_steps", tuple(self.completed_steps))
        object.__setattr__(
            self,
            "last_tool_outcome",
            ToolBatchOutcome(self.last_tool_outcome),
        )


@dataclass(frozen=True, slots=True)
class ContextBudget:
    window_tokens: int
    output_reserve_tokens: int
    safety_reserve_tokens: int
    runtime_reserve_tokens: int
    tool_schema_tokens: int = 0
    provider_input_tokens: int = 0
    minimum_message_tokens: int = 0
    context_allocations: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        numeric_fields = (
            "window_tokens",
            "output_reserve_tokens",
            "safety_reserve_tokens",
            "runtime_reserve_tokens",
            "tool_schema_tokens",
            "provider_input_tokens",
            "minimum_message_tokens",
        )
        for name in numeric_fields:
            normalizer = positive_int if name == "window_tokens" else non_negative_int
            object.__setattr__(self, name, normalizer(getattr(self, name), name))
        allocations: dict[str, int] = {}
        for raw_name, raw_tokens in self.context_allocations.items():
            name = required_text(raw_name, "context allocation name")
            tokens = non_negative_int(raw_tokens, "context allocation tokens")
            allocations[name] = tokens
        object.__setattr__(
            self,
            "context_allocations",
            freeze_json_mapping(allocations),
        )
        fixed_total = (
            self.output_reserve_tokens
            + self.safety_reserve_tokens
            + self.runtime_reserve_tokens
            + self.tool_schema_tokens
            + self.provider_input_tokens
        )
        if fixed_total > self.window_tokens:
            raise ValueError("context budget exceeds the model window")
        if sum(allocations.values()) + self.minimum_message_tokens > self.provider_input_tokens:
            raise ValueError("context allocations exceed provider input budget")

    @property
    def round_input_tokens(self) -> int:
        return self.provider_input_tokens + self.runtime_reserve_tokens

    @property
    def context_pool_tokens(self) -> int:
        return max(0, self.provider_input_tokens - self.minimum_message_tokens)

    def allocation_for(self, name: str) -> int:
        return int(self.context_allocations.get(str(name), 0))


@dataclass(frozen=True, slots=True)
class ContextBudgetClaim:
    """One domain-neutral context demand submitted to Core's allocator.

    ``minimum_tokens`` is the hard floor needed to keep the context usable,
    ``desired_tokens`` is the complete useful demand, and ``maximum_tokens``
    prevents a source from consuming space beyond that demand.  Priorities are
    compared only after every minimum has been funded.

    The first two fields intentionally preserve the former positional API.
    """

    name: str
    desired_tokens: int
    minimum_tokens: int = 0
    maximum_tokens: int | None = None
    priority: int = 0

    def __post_init__(self) -> None:
        name = required_text(self.name, "context budget claim name")
        desired = non_negative_int(
            self.desired_tokens, "context budget claim desired tokens"
        )
        minimum = non_negative_int(
            self.minimum_tokens, "context budget claim minimum tokens"
        )
        maximum = (
            desired
            if self.maximum_tokens is None
            else non_negative_int(
                self.maximum_tokens,
                "context budget claim maximum tokens",
            )
        )
        priority = int(self.priority)
        if minimum > desired:
            raise ValueError("context budget claim minimum exceeds desired")
        if desired > maximum:
            raise ValueError("context budget claim desired exceeds maximum")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "desired_tokens", desired)
        object.__setattr__(self, "minimum_tokens", minimum)
        object.__setattr__(self, "maximum_tokens", maximum)
        object.__setattr__(self, "priority", priority)


@dataclass(frozen=True, slots=True)
class ContextBlock:
    name: str
    content: str
    token_count: int = 0
    untrusted: bool = True
    host_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", required_text(
            self.name, "context block name"
        ))
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(self, "token_count", non_negative_int(
            self.token_count, "context block token count"
        ))
        object.__setattr__(self, "untrusted", bool(self.untrusted))
        object.__setattr__(
            self,
            "host_metadata",
            freeze_json_mapping(self.host_metadata),
        )


@dataclass(frozen=True, slots=True)
class ContextBundle:
    blocks: tuple[ContextBlock, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        blocks = tuple(self.blocks)
        names = [block.name for block in blocks]
        if len(names) != len(set(names)):
            raise ValueError("context block names must be unique")
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(
            self,
            "diagnostics",
            freeze_json_mapping(self.diagnostics),
        )


@dataclass(frozen=True, slots=True)
class TaskContextRequest:
    """Host-compiled requirements for post-planning context retrieval.

    ``task_spec`` carries semantic intent proposed by the planner. Every
    dependency and evidence field is compiled from host-owned tool contracts;
    the planner cannot grant itself context by emitting these values.
    """

    task_spec: TaskSpec
    planned_tool_names: tuple[str, ...] = ()
    available_tool_names: tuple[str, ...] = ()
    required_context_blocks: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    include_response_context: bool = False
    run_id: RunId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_spec, TaskSpec):
            raise TypeError("task context request requires a TaskSpec")
        for name in (
            "planned_tool_names",
            "available_tool_names",
            "required_context_blocks",
            "evidence_kinds",
        ):
            object.__setattr__(
                self,
                name,
                unique_text_tuple(getattr(self, name)),
            )
        object.__setattr__(
            self,
            "include_response_context",
            bool(self.include_response_context),
        )
        if self.run_id is not None:
            run_id = str(self.run_id or "").strip()
            if not run_id:
                raise ValueError("task context run_id must be non-empty")
            object.__setattr__(self, "run_id", run_id)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", required_text(self.id, "tool call id"))
        object.__setattr__(self, "name", required_text(
            self.name, "tool call name"
        ))
        object.__setattr__(self, "arguments_json", str(self.arguments_json or ""))


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: Mapping[str, Any]
    display_names: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = required_text(self.name, "tool schema name")
        display_names: dict[str, str] = {}
        for raw_locale, raw_display_name in self.display_names.items():
            locale = normalize_locale_tag(raw_locale)
            display_name = str(raw_display_name or "").strip()
            if not display_name:
                raise ValueError(
                    f"tool display name for {locale!r} must not be empty"
                )
            if locale in display_names:
                raise ValueError(
                    f"duplicate normalized tool display locale: {locale}"
                )
            display_names[locale] = display_name
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description or ""))
        object.__setattr__(
            self,
            "parameters",
            freeze_json_mapping(self.parameters),
        )
        object.__setattr__(
            self,
            "display_names",
            freeze_json_mapping(display_names),
        )


def normalize_locale_tag(value: Any) -> str:
    parts = [
        part
        for part in str(value or "").strip().replace("_", "-").split("-")
        if part
    ]
    if not parts or any(not part.isalnum() for part in parts):
        raise ValueError(f"invalid locale tag: {value!r}")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) in {2, 3}:
            normalized.append(part.upper())
        elif len(part) == 4:
            normalized.append(part.title())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


class ToolPayloadMode(StrEnum):
    """How model-generated data is committed by a registered tool."""

    INLINE = "inline"
    DELTA = "delta"
    BATCH = "batch"
    RESOURCE_REFERENCE = "resource_reference"


@dataclass(frozen=True, slots=True)
class ToolDataContract:
    """Declare authority boundaries without exposing host state to the model.

    Paths use a compact dotted form. ``items[].id`` addresses a property of an
    array item. Model-owned paths must be present in the model-visible JSON
    Schema. Host-bound and host-derived paths must be absent from it; adapters
    bind or calculate those values after model input validation.

    An empty contract preserves compatibility for tools that have not yet been
    audited. Domains can adopt the contract incrementally while Core startup
    validation prevents audited tools from regressing their authority boundary.
    """

    model_owned_paths: tuple[str, ...] = ()
    host_bound_paths: tuple[str, ...] = ()
    host_derived_paths: tuple[str, ...] = ()
    payload_mode: ToolPayloadMode = ToolPayloadMode.INLINE

    def __post_init__(self) -> None:
        groups: dict[str, tuple[str, ...]] = {}
        for name in (
            "model_owned_paths",
            "host_bound_paths",
            "host_derived_paths",
        ):
            values = tuple(dict.fromkeys(
                _tool_data_path(value)
                for value in getattr(self, name)
            ))
            object.__setattr__(self, name, values)
            groups[name] = values
        object.__setattr__(
            self,
            "payload_mode",
            ToolPayloadMode(self.payload_mode),
        )
        seen: dict[str, str] = {}
        for group, paths in groups.items():
            for path in paths:
                previous = seen.setdefault(path, group)
                if previous != group:
                    raise ValueError(
                        f"tool data path {path!r} has conflicting owners"
                    )


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    mode: ToolExecutionMode
    title: str
    risk_level: ToolRiskLevel = ToolRiskLevel.READ

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", ToolExecutionMode(self.mode))
        object.__setattr__(self, "title", required_text(
            self.title, "tool policy title"
        ))
        object.__setattr__(self, "risk_level", ToolRiskLevel(self.risk_level))

    @property
    def requires_user_approval(self) -> bool:
        return self.mode is ToolExecutionMode.CONFIRM


class ToolResultProjection(StrEnum):
    """How a completed result may be represented in later model rounds."""

    FULL = "full"
    RECEIPT = "receipt"


@dataclass(frozen=True, slots=True)
class ToolContextContract:
    """Host-owned dependency and evidence lifecycle for one tool.

    ``FULL`` is deliberately the default projection.  A tool result may only
    be compacted to a receipt after its domain contract opts in, preventing a
    context optimization from silently removing evidence.
    """

    prerequisite_tools: tuple[str, ...] = ()
    mandatory_context_keys: tuple[str, ...] = ()
    required_context_blocks: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    result_projection: ToolResultProjection = ToolResultProjection.FULL
    final_projection: ToolResultProjection = ToolResultProjection.FULL

    def __post_init__(self) -> None:
        for field_name in (
            "prerequisite_tools",
            "mandatory_context_keys",
            "required_context_blocks",
            "evidence_kinds",
            "produces",
        ):
            object.__setattr__(
                self,
                field_name,
                unique_text_tuple(getattr(self, field_name)),
            )
        object.__setattr__(
            self,
            "result_projection",
            ToolResultProjection(self.result_projection),
        )
        object.__setattr__(
            self,
            "final_projection",
            ToolResultProjection(self.final_projection),
        )


@dataclass(frozen=True, slots=True)
class DomainEffect:
    """A domain-owned effect emitted without depending on a host transport."""

    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", required_text(
            self.type, "domain effect type"
        ))
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class ToolHandlerResult:
    content: str
    from_cache: bool = False
    effects: tuple[DomainEffect, ...] = ()
    error_code: str | None = None
    step_disposition: ToolStepDisposition = ToolStepDisposition.COMPLETE
    planning_disposition: ToolPlanningDisposition = (
        ToolPlanningDisposition.KEEP_PLAN
    )
    effect_state: ToolEffectState = ToolEffectState.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(self, "from_cache", bool(self.from_cache))
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "error_code", _optional_text(self.error_code))
        object.__setattr__(
            self,
            "step_disposition",
            ToolStepDisposition(self.step_disposition),
        )
        object.__setattr__(
            self,
            "planning_disposition",
            ToolPlanningDisposition(self.planning_disposition),
        )
        object.__setattr__(
            self,
            "effect_state",
            ToolEffectState(self.effect_state),
        )


@dataclass(frozen=True, slots=True)
class ToolExecutionLimits:
    max_calls_per_batch: int = 8
    # Raw JSON is bounded before parsing only as a configurable memory-safety
    # envelope. Model-visible semantic limits belong to each tool's schema and
    # are enforced after JSON decoding, so escaping and whitespace cannot
    # consume an unrelated 32K workflow budget.
    max_argument_chars: int = 1_000_000
    max_result_chars: int = 64_000
    approval_timeout_seconds: float = 300.0
    approval_summary_chars: int = 420

    def __post_init__(self) -> None:
        for name in (
            "max_calls_per_batch",
            "max_argument_chars",
            "max_result_chars",
            "approval_summary_chars",
        ):
            object.__setattr__(self, name, positive_int(
                getattr(self, name), name
            ))
        timeout = float(self.approval_timeout_seconds)
        if timeout <= 0:
            raise ValueError("approval_timeout_seconds must be positive")
        object.__setattr__(self, "approval_timeout_seconds", timeout)


@dataclass(slots=True)
class ExecutionState:
    """Run-scoped mutable state owned by the active domain adapter.

    Core authorization such as the current tool allow-list must never be stored
    here because a domain handler can mutate this mapping.
    """

    domain: MutableMapping[str, Any] = field(default_factory=dict)
    # Bound by AgentCore after Run creation. It is contextual identity for
    # run-aware host tools, never a source of authorization.
    run_id: RunId | None = None


@dataclass(frozen=True, slots=True)
class ToolBatchRequest:
    run_id: RunId | None
    calls: tuple[ToolCall, ...]
    allowed_tool_names: frozenset[str]
    state: ExecutionState
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", tuple(self.calls))
        object.__setattr__(
            self,
            "allowed_tool_names",
            text_frozenset(self.allowed_tool_names),
        )
        if not self.calls:
            raise ValueError("tool batch requires at least one call")
        object.__setattr__(
            self,
            "invocation_id",
            _optional_text(self.invocation_id),
        )


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    content: str
    from_cache: bool = False
    approval_status: ApprovalStatus | None = None
    error: str | None = None
    effects: tuple[DomainEffect, ...] = ()
    step_disposition: ToolStepDisposition = ToolStepDisposition.COMPLETE
    planning_disposition: ToolPlanningDisposition = (
        ToolPlanningDisposition.KEEP_PLAN
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_call_id", required_text(
            self.tool_call_id, "tool result call id"
        ))
        object.__setattr__(self, "tool_name", required_text(
            self.tool_name, "tool result name"
        ))
        object.__setattr__(self, "content", str(self.content or ""))
        if self.approval_status is not None:
            object.__setattr__(
                self,
                "approval_status",
                ApprovalStatus(self.approval_status),
            )
        object.__setattr__(self, "error", _optional_text(self.error))
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(
            self,
            "step_disposition",
            ToolStepDisposition(self.step_disposition),
        )
        object.__setattr__(
            self,
            "planning_disposition",
            ToolPlanningDisposition(self.planning_disposition),
        )


@dataclass(frozen=True, slots=True)
class ToolBatchResult:
    results: tuple[ToolCallResult, ...]
    outcome: ToolBatchOutcome
    error: str | None = None
    cache_hits: tuple[bool, ...] = ()
    effect_state: ToolEffectState = ToolEffectState.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "outcome", ToolBatchOutcome(self.outcome))
        object.__setattr__(self, "error", _optional_text(self.error))
        object.__setattr__(self, "cache_hits", tuple(bool(item) for item in self.cache_hits))
        object.__setattr__(
            self,
            "effect_state",
            ToolEffectState(self.effect_state),
        )
        if self.cache_hits and len(self.cache_hits) != len(self.results):
            raise ValueError("tool batch cache hits must align with results")

    @property
    def replan_requested(self) -> bool:
        return any(
            result.planning_disposition is ToolPlanningDisposition.REPLAN
            for result in self.results
        )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool_call: ToolCall
    title: str
    risk_level: ToolRiskLevel
    summary: str
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        title = required_text(self.title, "approval title")
        timeout = float(self.timeout_seconds)
        if timeout <= 0:
            raise ValueError("approval timeout must be positive")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "risk_level", ToolRiskLevel(self.risk_level))
        object.__setattr__(self, "summary", str(self.summary or ""))
        object.__setattr__(self, "timeout_seconds", timeout)


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    approval_id: str | None
    status: ApprovalStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "approval_id", _optional_text(self.approval_id))
        object.__setattr__(self, "status", ApprovalStatus(self.status))
        if self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED} and not self.approval_id:
            raise ValueError("resolved approval requires an approval id")

    @property
    def approved(self) -> bool:
        return self.status is ApprovalStatus.APPROVED


@dataclass(frozen=True, slots=True)
class RunExecutionIntent:
    """Immutable user and host intent shared by every attempt of one Run."""

    requested_reasoning_mode: Literal["enabled", "disabled"]
    output_contract: str
    tool_protocol_contract: str
    recovery_policy_id: str
    capability_snapshot_digest: str

    def __post_init__(self) -> None:
        mode = str(self.requested_reasoning_mode or "").strip().lower()
        if mode not in {"enabled", "disabled"}:
            raise ValueError("requested reasoning mode must be enabled or disabled")
        object.__setattr__(self, "requested_reasoning_mode", mode)
        for name in (
            "output_contract",
            "tool_protocol_contract",
            "recovery_policy_id",
        ):
            object.__setattr__(self, name, required_text(
                getattr(self, name), f"run execution intent {name}"
            ))
        digest = str(self.capability_snapshot_digest or "").strip().lower()
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError(
                "run execution intent capability snapshot must be a SHA-256 digest"
            )
        object.__setattr__(self, "capability_snapshot_digest", digest)


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """Immutable, non-secret identity of the model request behind a Run."""

    model_provider: str
    model_name: str
    context_window: int
    endpoint_digest: str
    request_profile_digest: str
    capability_snapshot: Mapping[str, Any] = field(default_factory=dict)
    execution_intent: RunExecutionIntent | None = None

    def __post_init__(self) -> None:
        for name in ("model_provider", "model_name"):
            object.__setattr__(self, name, required_text(
                getattr(self, name), f"run provenance {name}"
            ))
        object.__setattr__(self, "context_window", positive_int(
            self.context_window, "run provenance context_window"
        ))
        for name in ("endpoint_digest", "request_profile_digest"):
            value = str(getattr(self, name) or "").strip().lower()
            if len(value) != 64 or any(
                char not in "0123456789abcdef"
                for char in value
            ):
                raise ValueError(f"run provenance {name} must be a SHA-256 digest")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "capability_snapshot",
            freeze_json_mapping(self.capability_snapshot),
        )
        if self.execution_intent is not None and not isinstance(
            self.execution_intent,
            RunExecutionIntent,
        ):
            raise TypeError(
                "run provenance execution_intent must be a RunExecutionIntent"
            )
        if self.capability_snapshot and self.execution_intent is not None:
            snapshot_digest = str(
                self.capability_snapshot.get("digest") or ""
            ).strip().lower()
            if snapshot_digest != self.execution_intent.capability_snapshot_digest:
                raise ValueError(
                    "run provenance capability snapshot digest does not match intent"
                )


@dataclass(frozen=True, slots=True)
class RunLineage:
    """Immutable parent identity for one child Agent Run.

    ``delegation_id`` is present only when a model-created delegation claim
    owns the child. Host-orchestrated children, such as durable task units,
    retain parent/root lineage without pretending that a delegation lease
    exists.
    """

    parent_run_id: RunId
    root_run_id: RunId
    delegation_id: str | None
    agent_role: str
    depth: int

    def __post_init__(self) -> None:
        for name in (
            "parent_run_id",
            "root_run_id",
            "agent_role",
        ):
            object.__setattr__(self, name, required_text(
                getattr(self, name), f"run lineage {name}"
            ))
        object.__setattr__(self, "delegation_id", _optional_text(
            self.delegation_id
        ))
        object.__setattr__(self, "depth", positive_int(
            self.depth, "child run depth"
        ))


@dataclass(frozen=True, slots=True)
class RunExecutionLease:
    run_id: RunId
    status: RunStatus
    owner_id: str | None = None
    expires_at_ms: int | None = None
    heartbeat_at_ms: int | None = None
    attempt: int = 0
    cancellation_requested_at_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", required_text(
            self.run_id, "execution lease run id"
        ))
        object.__setattr__(self, "status", RunStatus(self.status))
        object.__setattr__(self, "owner_id", _optional_text(self.owner_id))
        object.__setattr__(self, "attempt", max(0, int(self.attempt)))


@dataclass(frozen=True, slots=True)
class AgentDelegation:
    id: str
    parent_run_id: RunId
    root_run_id: RunId
    agent_role: str
    objective: str
    input_payload: Mapping[str, Any] = field(default_factory=dict)
    status: DelegationStatus = DelegationStatus.QUEUED
    child_run_id: RunId | None = None
    required: bool = True
    priority: int = 0
    result_summary: str | None = None
    error: str | None = None
    claim_attempt: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "parent_run_id", "root_run_id", "agent_role", "objective"):
            object.__setattr__(self, name, required_text(
                getattr(self, name), f"delegation {name}"
            ))
        object.__setattr__(self, "status", DelegationStatus(self.status))
        object.__setattr__(
            self,
            "input_payload",
            freeze_json_mapping(self.input_payload),
        )
        object.__setattr__(self, "child_run_id", _optional_text(self.child_run_id))
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "claim_attempt", max(0, int(self.claim_attempt)))
        object.__setattr__(self, "result_summary", _optional_text(self.result_summary))
        object.__setattr__(self, "error", _optional_text(self.error))


@dataclass(frozen=True, slots=True)
class DelegationClaim:
    delegation: AgentDelegation
    lineage: RunLineage

    def __post_init__(self) -> None:
        if self.delegation.id != self.lineage.delegation_id:
            raise ValueError("delegation claim lineage does not match")


@dataclass(frozen=True, slots=True)
class DelegationAggregation:
    state: Literal["pending", "ready", "blocked"]
    counts: Mapping[str, int]
    required_failures: tuple[str, ...] = ()
    results: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"pending", "ready", "blocked"}:
            raise ValueError("invalid delegation aggregate state")
        object.__setattr__(self, "counts", freeze_json_mapping(self.counts))
        object.__setattr__(self, "required_failures", tuple(self.required_failures))
        object.__setattr__(
            self,
            "results",
            tuple(freeze_json_mapping(item) for item in self.results),
        )


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    """Storage-neutral durable read checkpoint for one Run."""

    run: Mapping[str, Any]
    steps: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    delegations: tuple[AgentDelegation, ...] = ()
    next_cursor: int = 0
    has_more: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run", freeze_json_mapping(self.run))
        object.__setattr__(
            self,
            "steps",
            tuple(freeze_json_mapping(item) for item in self.steps),
        )
        object.__setattr__(
            self,
            "events",
            tuple(freeze_json_mapping(item) for item in self.events),
        )
        object.__setattr__(self, "delegations", tuple(self.delegations))
        object.__setattr__(self, "next_cursor", max(0, int(self.next_cursor)))
        object.__setattr__(self, "has_more", bool(self.has_more))


@dataclass(frozen=True, slots=True)
class RunCreateParams:
    session_id: SessionId | None
    prompt: str
    mode: str | None
    provenance: RunProvenance | None = None
    lineage: RunLineage | None = None
    binding: RunBinding | None = None
    turn_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt", str(self.prompt or ""))
        object.__setattr__(self, "mode", _optional_text(self.mode))
        object.__setattr__(self, "turn_id", _optional_text(self.turn_id))
        if self.provenance is not None and not isinstance(
            self.provenance,
            RunProvenance,
        ):
            raise TypeError("run provenance must be a RunProvenance value")
        if self.lineage is not None and not isinstance(self.lineage, RunLineage):
            raise TypeError("run lineage must be a RunLineage value")
        if self.binding is not None and not isinstance(self.binding, RunBinding):
            raise TypeError("run binding must be a RunBinding value")


@dataclass(frozen=True, slots=True)
class TaskStepUpdate:
    step_id: str
    status: StepStatus
    result_summary: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", required_text(
            self.step_id, "task step update id"
        ))
        object.__setattr__(self, "status", StepStatus(self.status))


@dataclass(frozen=True, slots=True)
class TraceRecord:
    stage: str
    outcome: str
    details: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", required_text(
            self.stage, "trace stage"
        ))
        object.__setattr__(self, "outcome", required_text(
            self.outcome, "trace outcome"
        ))
        object.__setattr__(self, "details", freeze_json_mapping(self.details))
        object.__setattr__(self, "duration_ms", optional_non_negative_int(
            self.duration_ms, "trace duration"
        ))


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: RunId
    status: RunStatus
    final_response: str = ""
    error: str | None = None
    model: str | None = None
    validated_result: str | None = None

    def __post_init__(self) -> None:
        run_id = required_text(self.run_id, "agent run result run id")
        status = RunStatus(self.status)
        if status is RunStatus.RUNNING:
            raise ValueError("agent run result must be terminal")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "final_response", str(self.final_response or ""))
        object.__setattr__(self, "error", _optional_text(self.error))
        object.__setattr__(self, "model", _optional_text(self.model))
        object.__setattr__(
            self,
            "validated_result",
            (
                None
                if self.validated_result is None
                else str(self.validated_result)
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Bound stalled execution separately from monotonic partial progress.

    ``max_model_rounds`` is the base budget for planned transitions,
    corrections, and the final response. A ``PROGRESSED`` tool result has a
    stronger contract: it committed valid partial work while keeping the same
    plan step active. Such rounds may unlock the separately bounded progress
    allowance without turning malformed or stalled loops into unbounded runs.
    """

    max_model_rounds: int = 6
    max_progress_rounds: int = 32

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_model_rounds", positive_int(
            self.max_model_rounds, "max model rounds"
        ))
        object.__setattr__(
            self,
            "max_progress_rounds",
            non_negative_int(self.max_progress_rounds, "max progress rounds"),
        )


@dataclass(frozen=True, slots=True)
class AgentRuntimeResult:
    run_id: RunId | None
    outcome: RuntimeOutcome
    final_response: str
    model: str
    round_count: int
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", RuntimeOutcome(self.outcome))
        object.__setattr__(self, "final_response", str(self.final_response or ""))
        object.__setattr__(self, "model", str(self.model or ""))
        object.__setattr__(self, "round_count", max(0, int(self.round_count)))
        object.__setattr__(self, "error_code", _optional_text(self.error_code))


def _tool_call_from_mapping(value: Mapping[str, Any]) -> ToolCall:
    function = value.get("function") if isinstance(value.get("function"), Mapping) else {}
    return ToolCall(
        id=str(value.get("id") or ""),
        name=str(value.get("name") or function.get("name") or ""),
        arguments_json=str(
            value.get("arguments_json")
            or function.get("arguments")
            or ""
        ),
    )
