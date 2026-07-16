"""Domain-neutral failures raised by Agent Core boundaries."""

from __future__ import annotations


class AgentCoreError(Exception):
    """Base class for errors with host-controlled public handling."""


class ContractViolationError(AgentCoreError):
    """A registered capability violates a Core contract."""


class ResponseJudgeContractError(ContractViolationError):
    """A semantic judge response violates its declared verdict contract."""


class ContextOverflowError(AgentCoreError):
    """The complete request cannot fit inside the configured budget."""


class InvalidPlannerOutputError(AgentCoreError):
    """A planner response could not be normalized into a safe typed plan."""


class RepairablePlannerOutputError(InvalidPlannerOutputError):
    """A structurally valid plan may be corrected by one model re-plan."""


class ModelGatewayError(AgentCoreError):
    """A model adapter failed after provider-specific handling."""

    def __init__(
        self,
        message: str = "model gateway failed",
        *,
        code: str = "model_gateway_error",
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = str(code or "model_gateway_error")
        self.retryable = bool(retryable)


class UnsupportedModelFeatureError(ModelGatewayError):
    """The selected provider/model rejected a requested capability."""

    def __init__(self, message: str = "unsupported model feature"):
        super().__init__(
            message,
            code="unsupported_model_feature",
            retryable=True,
        )


class ToolExecutionError(AgentCoreError):
    """A tool call failed inside the host-controlled execution boundary."""
