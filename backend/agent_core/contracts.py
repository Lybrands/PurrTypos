"""Pure data contracts shared by the future Agent Core and its adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator, Literal, Mapping, MutableMapping, TypeAlias

from agent_core.json_values import (
    freeze_json_mapping,
    freeze_json_value,
    thaw_json_mapping,
    thaw_json_value,
)


RunId: TypeAlias = str
SessionId: TypeAlias = str | int


def _frozen_mapping(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """Return a detached recursively immutable JSON mapping."""

    return freeze_json_mapping(value)


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELED = "canceled"


TerminalRunStatus: TypeAlias = Literal[
    RunStatus.DONE,
    RunStatus.BLOCKED,
    RunStatus.FAILED,
    RunStatus.CANCELED,
]


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class StepExecutor(StrEnum):
    MODEL = "model"
    TOOL = "tool"


class StepType(StrEnum):
    READ = "read"
    ANALYZE = "analyze"
    WRITE = "write"
    REVIEW = "review"
    CONFIRM = "confirm"


class ToolExecutionMode(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    CONFIRM = "confirm"


class ToolRiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    UNAVAILABLE = "unavailable"


class MessageRole(StrEnum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageOrigin(StrEnum):
    """Internal provenance that provider-shaped mappings cannot set."""

    CALLER = "caller"
    HOST_CONTEXT = "host_context"
    MODEL = "model"
    HOST_TOOL_RESULT = "host_tool_result"


class ToolChoiceMode(StrEnum):
    NONE = "none"
    AUTO = "auto"
    REQUIRED = "required"


class ReasoningMode(StrEnum):
    DEFAULT = "default"
    DISABLED = "disabled"


class ModelFinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    OTHER = "other"


class ToolBatchOutcome(StrEnum):
    COMPLETED = "completed"
    DECLINED = "declined"
    CANCELED = "canceled"
    REJECTED = "rejected"
    FAILED = "failed"


class RuntimeOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class PlanningKind(StrEnum):
    PLANNED = "planned"
    DIRECT_RESPONSE = "direct_response"


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """Provider-neutral message with lossless extra protocol fields."""

    role: MessageRole
    content: Any = None
    thinking: str | None = None
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
        object.__setattr__(self, "thinking", _optional_text(self.thinking))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "tool_call_id", _optional_text(self.tool_call_id))
        object.__setattr__(self, "origin", MessageOrigin(self.origin))
        if role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("tool message requires tool_call_id")
        object.__setattr__(self, "attributes", _frozen_mapping(self.attributes))
        object.__setattr__(
            self,
            "host_metadata",
            _frozen_mapping(self.host_metadata),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentMessage":
        raw = dict(value)
        # Provenance is assigned only by in-process Core composition. A
        # provider-shaped or HTTP mapping can never claim a host origin.
        raw.pop("origin", None)
        raw.pop("host_metadata", None)
        role = raw.pop("role", "")
        content = raw.pop("content", None)
        thinking = raw.pop("thinking", raw.pop("reasoning_content", None))
        tool_call_id = raw.pop("tool_call_id", None)
        tool_calls = tuple(
            _tool_call_from_mapping(item)
            for item in (raw.pop("tool_calls", ()) or ())
            if isinstance(item, Mapping)
        )
        return cls(
            role=role,  # type: ignore[arg-type]
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            attributes=raw,
        )

    def to_mapping(self) -> dict[str, Any]:
        value = thaw_json_mapping(self.attributes)
        value.update({"role": self.role.value, "content": thaw_json_value(self.content)})
        if self.thinking is not None:
            value["thinking"] = self.thinking
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
        namespace = str(self.namespace or "").strip()
        if not namespace:
            raise ValueError("domain context namespace is required")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class ModelRequest:
    provider: str
    model: str
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip().lower()
        model = str(self.model or "").strip()
        if not provider:
            raise ValueError("model provider is required")
        if not model:
            raise ValueError("model name is required")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "options", _frozen_mapping(self.options))


@dataclass(frozen=True, slots=True)
class ModelInvocation:
    request: ModelRequest
    tools: tuple[ToolSchema, ...] = ()
    tool_choice: ToolChoiceMode = ToolChoiceMode.AUTO
    max_output_tokens: int | None = None
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "tool_choice", ToolChoiceMode(self.tool_choice))
        object.__setattr__(self, "reasoning_mode", ReasoningMode(self.reasoning_mode))
        if self.max_output_tokens is not None:
            maximum = int(self.max_output_tokens)
            if maximum <= 0:
                raise ValueError("max output tokens must be positive")
            object.__setattr__(self, "max_output_tokens", maximum)
        if not self.tools and self.tool_choice is ToolChoiceMode.REQUIRED:
            raise ValueError("required tool choice needs at least one tool")


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    type: str | None = None
    name: str | None = None
    arguments_fragment: str = ""

    def __post_init__(self) -> None:
        index = int(self.index)
        if index < 0:
            raise ValueError("tool call delta index must be non-negative")
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "id", _optional_text(self.id))
        object.__setattr__(self, "type", _optional_text(self.type))
        object.__setattr__(self, "name", _optional_text(self.name))
        object.__setattr__(
            self,
            "arguments_fragment",
            str(self.arguments_fragment or ""),
        )


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    content_delta: str = ""
    thinking_delta: str = ""
    tool_call_deltas: tuple[ToolCallDelta, ...] = ()
    finish_reason: ModelFinishReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_call_deltas", tuple(self.tool_call_deltas))
        if self.finish_reason is not None:
            object.__setattr__(
                self,
                "finish_reason",
                ModelFinishReason(self.finish_reason),
            )


@dataclass(slots=True)
class ModelStream:
    chunks: AsyncIterator[ModelStreamChunk]
    model: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        model = str(self.model or "").strip()
        if not model:
            raise ValueError("model stream requires a model name")
        self.model = model
        self.metadata = _frozen_mapping(self.metadata)


@dataclass(frozen=True, slots=True)
class ModelCompletion:
    message: AgentMessage
    model: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        model = str(self.model or "").strip()
        if not model:
            raise ValueError("model completion requires a model name")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


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
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))
        if self.context_window is not None:
            window = int(self.context_window)
            if window <= 0:
                raise ValueError("context window must be positive")
            object.__setattr__(self, "context_window", window)

    def latest_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                return str(message.content or "")
        return ""


@dataclass(frozen=True, slots=True)
class PlanningConstraints:
    """Request-scoped limits on the tools a planner may select.

    ``context_satisfied_tool_names`` is node-wide: the named tool's result is
    already present in trusted context, so the planner must not call it.
    ``planning_excluded_tool_names`` names tools that are valid runtime
    capabilities but outside this request's explicitly bounded evidence or
    action scope.  Exclusion is not a claim that their results are present.
    ``satisfied_tool_dependency_edges`` is deliberately narrower: only the
    named consumer's dependency is already satisfied, while the dependency
    tool remains available for explicit use and for every other consumer.
    """

    context_satisfied_tool_names: frozenset[str] = frozenset()
    planning_excluded_tool_names: frozenset[str] = frozenset()
    satisfied_tool_dependency_edges: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "context_satisfied_tool_names",
            frozenset(
                str(name).strip()
                for name in self.context_satisfied_tool_names
                if str(name).strip()
            ),
        )
        object.__setattr__(
            self,
            "planning_excluded_tool_names",
            frozenset(
                str(name).strip()
                for name in self.planning_excluded_tool_names
                if str(name).strip()
            ),
        )
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
        code = str(self.violation_code or "").strip() or None
        guidance = str(self.repair_guidance or "").strip() or None
        if (code is None) != (guidance is None):
            raise ValueError(
                "response validation rejection requires both a violation "
                "code and repair guidance"
            )
        object.__setattr__(self, "violation_code", code)
        object.__setattr__(self, "repair_guidance", guidance)
        object.__setattr__(self, "details", _frozen_mapping(self.details))

    @property
    def accepted(self) -> bool:
        return self.violation_code is None


@dataclass(frozen=True, slots=True)
class PlanningCapabilities:
    available_tool_names: frozenset[str] = frozenset()
    model_supports_tools: bool = True
    host_planning_facts: Mapping[str, Any] = field(default_factory=dict)
    tool_guidance: Mapping[str, Any] = field(default_factory=dict)
    constraints: PlanningConstraints = field(default_factory=PlanningConstraints)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_tool_names",
            frozenset(str(name).strip() for name in self.available_tool_names if str(name).strip()),
        )
        object.__setattr__(self, "model_supports_tools", bool(self.model_supports_tools))
        object.__setattr__(
            self,
            "host_planning_facts",
            _frozen_mapping(self.host_planning_facts),
        )
        object.__setattr__(
            self,
            "tool_guidance",
            _frozen_mapping(self.tool_guidance),
        )
        if not isinstance(self.constraints, PlanningConstraints):
            raise TypeError("planning constraints must be PlanningConstraints")


@dataclass(frozen=True, slots=True)
class TaskStep:
    id: str
    title: str
    type: StepType
    executor: StepExecutor
    status: StepStatus = StepStatus.PENDING
    risk_level: ToolRiskLevel | None = None
    suggested_tools: tuple[str, ...] = ()
    description: str | None = None
    result_summary: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        step_id = str(self.id or "").strip()
        title = str(self.title or "").strip()
        if not step_id:
            raise ValueError("task step id is required")
        if not title:
            raise ValueError("task step title is required")
        object.__setattr__(self, "id", step_id)
        object.__setattr__(self, "title", title)
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
            tuple(dict.fromkeys(
                str(name).strip()
                for name in self.suggested_tools
                if str(name).strip()
            )),
        )
        object.__setattr__(self, "description", _optional_text(self.description))
        object.__setattr__(self, "result_summary", _optional_text(self.result_summary))
        object.__setattr__(self, "error", _optional_text(self.error))


@dataclass(frozen=True, slots=True)
class TaskPlan:
    title: str
    steps: tuple[TaskStep, ...]
    goal: str | None = None

    def __post_init__(self) -> None:
        title = str(self.title or "").strip()
        steps = tuple(self.steps)
        if not title:
            raise ValueError("task plan title is required")
        if not steps:
            raise ValueError("task plan requires at least one step")
        step_ids = [step.id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("task plan step ids must be unique")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "goal", _optional_text(self.goal))


@dataclass(frozen=True, slots=True)
class PlannerLimits:
    max_steps: int = 8
    max_output_tokens: int = 1_200
    max_step_id_chars: int = 48
    max_title_chars: int = 48
    max_goal_chars: int = 160
    max_tool_steps: int = 4

    def __post_init__(self) -> None:
        for name in (
            "max_steps",
            "max_step_id_chars",
            "max_title_chars",
            "max_goal_chars",
        ):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        max_tool_steps = int(self.max_tool_steps)
        if max_tool_steps < 0 or max_tool_steps > self.max_steps:
            raise ValueError("max_tool_steps must be between zero and max_steps")
        object.__setattr__(self, "max_tool_steps", max_tool_steps)
        max_output_tokens = int(self.max_output_tokens)
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        object.__setattr__(self, "max_output_tokens", max_output_tokens)


@dataclass(frozen=True, slots=True)
class PlanningResult:
    kind: PlanningKind
    plan: TaskPlan
    reason: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PlanningKind(self.kind))
        object.__setattr__(self, "reason", _optional_text(self.reason))
        object.__setattr__(self, "model", _optional_text(self.model))


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
            value = int(getattr(self, name))
            if value < 0 or (name == "window_tokens" and value <= 0):
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        allocations: dict[str, int] = {}
        for raw_name, raw_tokens in self.context_allocations.items():
            name = str(raw_name or "").strip()
            tokens = int(raw_tokens)
            if not name:
                raise ValueError("context allocation name is required")
            if tokens < 0:
                raise ValueError("context allocation tokens must be non-negative")
            allocations[name] = tokens
        object.__setattr__(self, "context_allocations", _frozen_mapping(allocations))
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
    name: str
    desired_tokens: int

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        desired = int(self.desired_tokens)
        if not name:
            raise ValueError("context budget claim name is required")
        if desired < 0:
            raise ValueError("context budget claim must be non-negative")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "desired_tokens", desired)


@dataclass(frozen=True, slots=True)
class ContextBlock:
    name: str
    content: str
    token_count: int = 0
    untrusted: bool = True
    host_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("context block name is required")
        token_count = int(self.token_count)
        if token_count < 0:
            raise ValueError("context block token count must be non-negative")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(self, "token_count", token_count)
        object.__setattr__(self, "untrusted", bool(self.untrusted))
        object.__setattr__(
            self,
            "host_metadata",
            _frozen_mapping(self.host_metadata),
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
        object.__setattr__(self, "diagnostics", _frozen_mapping(self.diagnostics))


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        call_id = str(self.id or "").strip()
        name = str(self.name or "").strip()
        if not call_id:
            raise ValueError("tool call id is required")
        if not name:
            raise ValueError("tool call name is required")
        object.__setattr__(self, "id", call_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments_json", str(self.arguments_json or ""))


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("tool schema name is required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description or ""))
        object.__setattr__(self, "parameters", _frozen_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    mode: ToolExecutionMode
    title: str
    risk_level: ToolRiskLevel = ToolRiskLevel.READ

    def __post_init__(self) -> None:
        title = str(self.title or "").strip()
        if not title:
            raise ValueError("tool policy title is required")
        object.__setattr__(self, "mode", ToolExecutionMode(self.mode))
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "risk_level", ToolRiskLevel(self.risk_level))

    @property
    def requires_user_approval(self) -> bool:
        return self.mode is ToolExecutionMode.CONFIRM


@dataclass(frozen=True, slots=True)
class DomainEffect:
    """A domain-owned UI effect emitted without depending on SSE."""

    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        effect_type = str(self.type or "").strip()
        if not effect_type:
            raise ValueError("domain effect type is required")
        object.__setattr__(self, "type", effect_type)
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class ToolHandlerResult:
    content: str
    from_cache: bool = False
    effects: tuple[DomainEffect, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(self, "from_cache", bool(self.from_cache))
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "error_code", _optional_text(self.error_code))


@dataclass(frozen=True, slots=True)
class ToolExecutionLimits:
    max_calls_per_batch: int = 8
    max_argument_chars: int = 32_000
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
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
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


@dataclass(frozen=True, slots=True)
class ToolBatchRequest:
    run_id: RunId | None
    calls: tuple[ToolCall, ...]
    allowed_tool_names: frozenset[str]
    state: ExecutionState

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", tuple(self.calls))
        object.__setattr__(
            self,
            "allowed_tool_names",
            frozenset(
                str(name).strip()
                for name in self.allowed_tool_names
                if str(name).strip()
            ),
        )
        if not self.calls:
            raise ValueError("tool batch requires at least one call")


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    content: str
    from_cache: bool = False
    approval_status: ApprovalStatus | None = None
    error: str | None = None
    effects: tuple[DomainEffect, ...] = ()

    def __post_init__(self) -> None:
        call_id = str(self.tool_call_id or "").strip()
        tool_name = str(self.tool_name or "").strip()
        if not call_id:
            raise ValueError("tool result call id is required")
        if not tool_name:
            raise ValueError("tool result name is required")
        object.__setattr__(self, "tool_call_id", call_id)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "content", str(self.content or ""))
        if self.approval_status is not None:
            object.__setattr__(
                self,
                "approval_status",
                ApprovalStatus(self.approval_status),
            )
        object.__setattr__(self, "error", _optional_text(self.error))
        object.__setattr__(self, "effects", tuple(self.effects))


@dataclass(frozen=True, slots=True)
class ToolBatchResult:
    results: tuple[ToolCallResult, ...]
    outcome: ToolBatchOutcome
    error: str | None = None
    cache_hits: tuple[bool, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "outcome", ToolBatchOutcome(self.outcome))
        object.__setattr__(self, "error", _optional_text(self.error))
        object.__setattr__(self, "cache_hits", tuple(bool(item) for item in self.cache_hits))
        if self.cache_hits and len(self.cache_hits) != len(self.results):
            raise ValueError("tool batch cache hits must align with results")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool_call: ToolCall
    title: str
    risk_level: ToolRiskLevel
    summary: str
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        title = str(self.title or "").strip()
        if not title:
            raise ValueError("approval title is required")
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
class RunProvenance:
    """Immutable, non-secret identity of the model request behind a Run."""

    model_provider: str
    model_name: str
    context_window: int
    endpoint_digest: str
    request_profile_digest: str

    def __post_init__(self) -> None:
        for name in ("model_provider", "model_name"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"run provenance {name} is required")
            object.__setattr__(self, name, value)
        context_window = int(self.context_window)
        if context_window <= 0:
            raise ValueError("run provenance context_window must be positive")
        object.__setattr__(self, "context_window", context_window)
        for name in ("endpoint_digest", "request_profile_digest"):
            value = str(getattr(self, name) or "").strip().lower()
            if len(value) != 64 or any(
                char not in "0123456789abcdef"
                for char in value
            ):
                raise ValueError(f"run provenance {name} must be a SHA-256 digest")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class RunCreateParams:
    session_id: SessionId | None
    prompt: str
    mode: str | None
    provenance: RunProvenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt", str(self.prompt or ""))
        object.__setattr__(self, "mode", _optional_text(self.mode))
        if self.provenance is not None and not isinstance(
            self.provenance,
            RunProvenance,
        ):
            raise TypeError("run provenance must be a RunProvenance value")


@dataclass(frozen=True, slots=True)
class TaskStepUpdate:
    step_id: str
    status: StepStatus
    result_summary: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        step_id = str(self.step_id or "").strip()
        if not step_id:
            raise ValueError("task step update id is required")
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "status", StepStatus(self.status))


@dataclass(frozen=True, slots=True)
class TraceRecord:
    stage: str
    outcome: str
    details: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        stage = str(self.stage or "").strip()
        outcome = str(self.outcome or "").strip()
        if not stage or not outcome:
            raise ValueError("trace stage and outcome are required")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "details", _frozen_mapping(self.details))
        if self.duration_ms is not None:
            duration = int(self.duration_ms)
            if duration < 0:
                raise ValueError("trace duration must be non-negative")
            object.__setattr__(self, "duration_ms", duration)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: RunId
    status: RunStatus
    final_response: str = ""
    error: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        status = RunStatus(self.status)
        if not run_id:
            raise ValueError("agent run result requires a run id")
        if status is RunStatus.RUNNING:
            raise ValueError("agent run result must be terminal")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "final_response", str(self.final_response or ""))
        object.__setattr__(self, "error", _optional_text(self.error))
        object.__setattr__(self, "model", _optional_text(self.model))


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    max_model_rounds: int = 6

    def __post_init__(self) -> None:
        if int(self.max_model_rounds) <= 0:
            raise ValueError("max model rounds must be positive")
        object.__setattr__(self, "max_model_rounds", int(self.max_model_rounds))


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


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
