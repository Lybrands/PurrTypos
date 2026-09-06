"""Require durable public progress before an internal stage executes tools."""

from dataclasses import replace

from purra.contracts import ResponseValidationResult, ToolEffectState, ToolHandlerResult
from purra.tools import InMemoryToolCatalog


PUBLIC_PROGRESS_REQUIRED = "public_progress_required"
PUBLIC_PROGRESS_REPAIR = (
    "本阶段尚未产生公开执行说明，业务工具未执行。"
    "先在正文开头用【公开说明】和【说明结束】包围一句当前阶段的目标与下一步动作，"
    "随后立即调用本阶段的工具。说明只包含可公开的执行信息；最终产物仍按原契约提交。"
)


class RequiredPublicProgress:
    def __init__(self, repository):
        self._repository = repository
        self._confirmed = False

    def wrap_tools(self, catalog):
        def wrap(registration):
            async def missing_progress(state):
                if not state.run_id or not await self._repository.has_public_progress(state.run_id):
                    return ToolHandlerResult(
                        content=f"{PUBLIC_PROGRESS_REQUIRED}: {PUBLIC_PROGRESS_REPAIR}",
                        error_code="tool_input_invalid",
                        effect_state=ToolEffectState.NOT_STARTED,
                    )
                self._confirmed = True
                return None

            async def handler(state, arguments, signal):
                failure = await missing_progress(state)
                if failure is not None:
                    return failure
                return await registration.handler(state, arguments, signal)

            async def call_handler(state, arguments, tool_call, signal):
                failure = await missing_progress(state)
                if failure is not None:
                    return failure
                return await registration.call_handler(state, arguments, tool_call, signal)

            return replace(
                registration,
                handler=handler,
                call_handler=call_handler if registration.call_handler is not None else None,
            )

        return InMemoryToolCatalog(
            tuple(wrap(item) for item in catalog.registrations()),
            enablement=catalog.enabled_names,
        )

    def validate(self, *, content, messages):
        # Historical message text is not evidence of publication in this stage.
        if self._confirmed:
            return ResponseValidationResult()
        return ResponseValidationResult(
            violation_code=PUBLIC_PROGRESS_REQUIRED,
            repair_guidance=PUBLIC_PROGRESS_REPAIR,
        )
