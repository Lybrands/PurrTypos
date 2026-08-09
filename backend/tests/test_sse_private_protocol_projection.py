from application.sse_mapping import core_event_to_sse_chunk
from agent_core.events import AgentEvent, CoreEventType


def test_task_plan_hides_private_protocol_and_exposes_business_capability():
    chunk = core_event_to_sse_chunk(AgentEvent(
        type=CoreEventType.RUN_TODOS_UPDATED,
        run_id="run-1",
        payload={
            "title": "生成场景表",
            "status": "running",
            "steps": [
                {
                    "id": "generate-protocol-1",
                    "title": "开始场景表分批提案",
                    "type": "write",
                    "executor": "tool",
                    "status": "running",
                    "suggested_tools": ["beginSceneListArtifact"],
                    "protocol_private": True,
                    "planning_capability": "generateSceneList",
                },
                {
                    "id": "generate",
                    "title": "生成完整场景表",
                    "type": "write",
                    "executor": "tool",
                    "status": "pending",
                    "suggested_tools": ["finalizeSceneListProposal"],
                    "planning_capability": "generateSceneList",
                },
            ],
        },
    ))

    assert chunk is not None
    steps = chunk["agentRunTodosUpdated"]["steps"]
    assert len(steps) == 1
    assert steps[0]["id"] == "generate"
    assert steps[0]["suggestedTools"] == ["generateSceneList"]
    assert steps[0]["planningCapability"] == "generateSceneList"
    assert "protocolPrivate" not in steps[0]


def test_private_protocol_step_updates_are_not_public_sse_events():
    chunk = core_event_to_sse_chunk(AgentEvent(
        type=CoreEventType.RUN_TODO_UPDATED,
        run_id="run-1",
        payload={
            "step_id": "generate-protocol-1",
            "step": {
                "id": "generate-protocol-1",
                "title": "追加场景表批次",
                "type": "write",
                "executor": "tool",
                "status": "done",
                "suggested_tools": ["appendSceneListBatch"],
                "protocol_private": True,
                "planning_capability": "generateSceneList",
            },
            "status": "running",
        },
    ))

    assert chunk is None
