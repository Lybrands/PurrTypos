#!/usr/bin/env python3
"""Run scalable Novel Analysis with a real Provider in an isolated database."""

from __future__ import annotations

import argparse
import asyncio
import json
from hashlib import sha256
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from agents.novel_analysis.composition import (
    create_isolated_scalable_novel_analysis_composition,
)
from agents.novel_analysis.domain import NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
from agents.novel_analysis.scalable_executor import ScalableNovelAnalysisUnitExecutor
from application.agent_run_service import AgentRunService
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
)
from database.connection import DatabaseConnection
from purra.api import AgentCoreRunOptions
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    DomainContext,
    MessageRole,
    RunBinding,
    RunStatus,
)


def _load_model(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'ai_model_configs'"
        ).fetchone()
    if row is None:
        raise RuntimeError("model database has no saved model configuration")
    for item in json.loads(row[0]):
        if item.get("providerId") == "zai" and item.get("apiKey"):
            return item
    raise RuntimeError("model database has no configured Zai model")


def _runtime(config: dict[str, object]):
    return SimpleNamespace(
        apiProvider=config.get("apiProvider") or "zai",
        apiKey=str(config["apiKey"]),
        baseURL=config.get("baseUrl") or config.get("baseURL") or None,
        contextWindow="32k",
        options={
            "model": config["name"],
            "model_profile": config.get("presetId") or "zai:glm-5.3-flash",
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
            "temperature": 1,
        },
    )


async def _seed(db: DatabaseConnection) -> tuple[str, str]:
    # Large enough to force multiple slices at a 32K context window while
    # remaining synthetic and cheap enough for a one-off live gate.
    paragraph = (
        "月蚀港每逢潮钟三响便关闭北门。守门人林澈只认银色航标，"
        "药师周遥必须在第三声钟响前送入药箱。"
    )
    text = "\n\n".join(paragraph + f"记录序号{index}。" for index in range(300))
    content_digest = sha256(text.encode()).hexdigest()
    revision_digest = sha256(("revision:" + content_digest).encode()).hexdigest()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('live-work', '可扩展分析隔离验收', 'external_text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('live-revision', 'live-work', 1, ?, 1, ?, ?)",
        [revision_digest, len(text.encode()), len(text)],
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest, byte_count, character_count) "
        "VALUES ('live-section', 'live-revision', 0, '月蚀港', ?, ?, ?, ?)",
        [text, content_digest, len(text.encode()), len(text)],
    )
    return revision_digest, text


async def _run(model_db: Path, data_dir: Path) -> dict[str, object]:
    if data_dir.exists() and any(data_dir.iterdir()):
        raise RuntimeError("acceptance data directory must be empty")
    data_dir.mkdir(parents=True, exist_ok=True)
    db = DatabaseConnection(data_dir)
    await db.init()
    composition = None
    try:
        revision_digest, source_text = await _seed(db)
        config = _load_model(model_db)
        runtime = _runtime(config)
        model = model_request_from_runtime(runtime)
        composition = create_isolated_scalable_novel_analysis_composition(db)
        command_id = "scalable-live-" + uuid4().hex
        request = AgentRunRequest(
            messages=(AgentMessage(
                MessageRole.USER,
                "分析整部合成作品的人物、情节和世界规则，最后给出整书总结。",
            ),),
            model=model,
            context_window=32_000,
            domain_context=DomainContext(
                namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
                payload={
                    "sourceRevisionId": "live-revision",
                    "commandId": command_id,
                },
            ),
        )
        executor = ScalableNovelAnalysisUnitExecutor(
            db, model_name=model.model
        )
        results = []
        async for update in AgentRunService(composition).run(
            request=request,
            api_key=runtime.apiKey,
            options=AgentCoreRunOptions(
                turn_id=command_id,
                default_context_window_tokens=32_000,
                reasoning_mode=reasoning_mode_from_options(model.options),
                binding=RunBinding(
                    namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
                    aggregate_id="live-revision",
                    command_id=command_id,
                ),
            ),
            signal=asyncio.Event(),
            long_task_executor=executor,
        ):
            if isinstance(update, AgentRunResult):
                results.append(update)
        if len(results) != 1 or results[0].status is not RunStatus.DONE:
            raise RuntimeError(f"scalable live Run failed: {results}")
        result = results[0]
        if not str(result.final_response or "").strip():
            raise RuntimeError("scalable live Run returned no final summary")
        task = await db.fetch_one(
            "SELECT id FROM ai_agent_long_tasks WHERE created_by_run_id = ?",
            [result.run_id],
        )
        if task is None:
            raise RuntimeError("scalable live Run created no durable analysis task")
        task_id = task["id"]
        units = await db.fetch_all(
            "SELECT unit_id, status, metadata_json FROM ai_agent_long_task_units "
            "WHERE task_id = ? ORDER BY position", [task_id]
        )
        kinds = [json.loads(item["metadata_json"])["unitKind"] for item in units]
        child_count = (await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE root_run_id = ? "
            "AND parent_run_id = ?", [result.run_id, result.run_id]
        ))["count"]
        tool_names = [
            json.loads(item["payload_json"])["toolName"]
            for item in await db.fetch_all(
            "SELECT payload_json FROM ai_agent_run_events "
            "WHERE event_type = 'tool.call_completed' AND run_id IN "
            "(SELECT id FROM ai_agent_runs WHERE root_run_id = ?) ORDER BY id",
            [result.run_id],
        )]
        lease_count = (await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_provider_call_leases"
        ))["count"]
        manifest = json.loads((await db.fetch_one(
            "SELECT metadata_json FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        ))["metadata_json"])["sliceManifest"]
        if len(manifest["slices"]) < 2 or kinds.count("map") < 2:
            raise RuntimeError("live fixture did not exercise multi-slice analysis")
        expected_tools = {
            "readNovelSourceSlice",
            "readNovelAnalysisReduceInputs",
            "readNovelAnalysisSynthesisInputs",
            "readNovelAnalysisReviewInput",
        }
        if not expected_tools.issubset(tool_names):
            raise RuntimeError(f"Provider did not select required tools: {tool_names}")
        if lease_count:
            raise RuntimeError(f"Provider leases leaked: {lease_count}")
        return {
            "dataDir": str(data_dir),
            "runId": result.run_id,
            "taskId": task_id,
            "sourceCharacters": len(source_text),
            "sliceCount": len(manifest["slices"]),
            "unitKinds": kinds,
            "childRunCount": child_count,
            "toolNames": tool_names,
            "providerLeaseCount": lease_count,
            "finalResponse": result.final_response,
        }
    finally:
        if composition is not None:
            await composition.shutdown()
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-db", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args.model_db, args.data_dir)), ensure_ascii=False))


if __name__ == "__main__":
    main()
