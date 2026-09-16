"""Durable attempt-scoped Candidate submission for Screenplay replacement."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agents.screenplay.access_contract import WRITE_SCREENPLAY_CANDIDATE_PART
from agents.screenplay.candidate_artifact import ScreenplayCandidateArtifactStore
from agents.screenplay.contracts import (
    ScreenplayPartCompletion,
    ScreenplayPartOperationScope,
    screenplay_part_definition,
)
from agents.screenplay.read_tools import SCREENPLAY_OPERATION_SCOPE_STATE_KEY
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolEffectState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration


def build_screenplay_replacement_submission_registration(db) -> ToolRegistration:
    artifacts = ScreenplayCandidateArtifactStore(db)

    async def submit(state, arguments, signal=None):
        raise_if_stopped(signal)
        try:
            scope = ScreenplayPartOperationScope.from_mapping(
                state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY)
            )
            if (
                screenplay_part_definition(scope.part_kind).completion
                is not ScreenplayPartCompletion.CANDIDATE_TOOL
            ):
                raise ValueError(
                    "Screenplay Part completion is not Candidate-tool owned"
                )
            if not state.run_id:
                raise ValueError("Screenplay Operation owner Run is unavailable")
            candidate = arguments.get("candidate")
            if not isinstance(candidate, Mapping):
                raise ValueError("Screenplay Candidate must be an object")
            receipt = await artifacts.commit(
                scope=scope,
                run_id=state.run_id,
                payload={
                    "schemaVersion": 1,
                    "partKind": scope.part_kind.value,
                    "partKey": scope.part_key,
                    "targetRole": scope.target_role,
                    "payload": dict(candidate),
                },
            )
            raise_if_stopped(signal)
            return ToolHandlerResult(
                content=json.dumps(
                    {
                        "artifactRef": receipt.resource_ref,
                        "replayed": receipt.replayed,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                effect_state=ToolEffectState.COMMITTED,
            )
        except (TypeError, ValueError) as error:
            return ToolHandlerResult(
                content=json.dumps(
                    {
                        "success": False,
                        "code": "tool_input_invalid",
                        "error": str(error),
                    },
                    ensure_ascii=False,
                ),
                error_code="tool_input_invalid",
                effect_state=ToolEffectState.NOT_STARTED,
            )

    return ToolRegistration(
        schema=ToolSchema(
            name=WRITE_SCREENPLAY_CANDIDATE_PART,
            description=(
                "校验并持久化提交当前 Part attempt 的唯一候选；Part 身份由主机绑定，"
                "此操作不会发布或修改已接受剧本版本。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "candidate": {
                        "type": "object",
                        "description": "当前 Part 的完整候选内容。",
                    },
                },
                "required": ["candidate"],
                "additionalProperties": False,
            },
            display_names={
                "zh-CN": "提交剧本候选部件",
                "en": "Submit screenplay candidate part",
            },
        ),
        handler=submit,
        policy=ToolPolicy(
            ToolExecutionMode.PROPOSE,
            "提交剧本候选部件",
            ToolRiskLevel.WRITE,
        ),
        concurrency_safe=False,
        host_managed_durability=True,
        cancellation_linearizable=True,
        max_argument_chars=300_000,
        data_contract=ToolDataContract(
            model_owned_paths=("candidate",),
            host_bound_paths=(SCREENPLAY_OPERATION_SCOPE_STATE_KEY,),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {
                "zh-CN": "提交剧本候选部件",
                "en": "Submit screenplay candidate part",
            },
            **_candidate_title_arguments(arguments),
        },
    )


__all__ = ["build_screenplay_replacement_submission_registration"]
