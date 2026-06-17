from __future__ import annotations

import pytest

from services.task_step_executor import (
    TaskStepValidationError,
    validate_task_plan_steps,
    validate_task_step_executor,
)


class TestTaskStepExecutorValidation:
    def test_expert_executor_is_rejected(self):
        with pytest.raises(TaskStepValidationError, match="executor must be one of"):
            validate_task_step_executor({
                "id": "review-result",
                "title": "生成审校结论",
                "type": "review",
                "status": "pending",
                "executor": "expert",
                "expertRole": "review",
            })

    def test_non_expert_steps_do_not_require_expert_role(self):
        hook = validate_task_step_executor({
            "id": "analyze-goal",
            "title": "分析目标",
            "type": "analyze",
            "status": "pending",
            "executor": "model",
        })

        assert hook.executor == "model"
        assert hook.allowed_tools == set()

    def test_tool_steps_expose_suggested_tools(self):
        hooks = validate_task_plan_steps([
            {
                "id": "read-context",
                "title": "读取上下文",
                "type": "read",
                "status": "pending",
                "executor": "tool",
                "suggestedTools": ["getChapterContent"],
            }
        ])

        hook = next(hook for hook in hooks if hook.step.id == "read-context")
        assert hook.executor == "tool"
        assert hook.allowed_tools == {"getChapterContent"}
