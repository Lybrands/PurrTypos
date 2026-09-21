#!/usr/bin/env python3
"""Run one content-isolated Novel Analysis replacement live acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
import urllib.request
from uuid import uuid4


REVIEW_REF_PREFIX = "novel-analysis://"
SOURCE_TEXT = (
    "# 月蚀港\n"
    "月蚀之夜，守门人林澈只认银色航标。潮钟敲响三次后，他打开北门，"
    "让载着药箱的周遥进入城内。"
)


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload=None,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} failed with HTTP {error.code}: {detail}"
        ) from error


def require_data(response: dict, operation: str):
    if not response.get("success"):
        raise RuntimeError(f"{operation} failed: {response.get('error')}")
    return response.get("data")


def load_model(database_path: str) -> dict:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'ai_model_configs'"
        ).fetchone()
    if row is None:
        raise RuntimeError("source acceptance database has no model config")
    configs = json.loads(row[0])
    for config in configs:
        if config.get("providerId") == "zai" and config.get("apiKey"):
            return config
    raise RuntimeError("source acceptance database has no configured Zai model")


def runtime_for(model: dict) -> dict:
    return {
        "modelConfigId": str(model["id"]),
        "apiKey": model["apiKey"],
        "baseURL": model.get("baseUrl"),
        "apiProvider": model.get("apiProvider", "zai"),
        "locale": "zh-CN",
        "options": {
            "model": model["name"],
            "model_profile": model.get("presetId", "zai:glm-5.3-flash"),
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
            "temperature": 1,
            "context_window": "256k",
        },
        "contextWindow": "256k",
    }


def import_fixture(base_url: str) -> dict:
    source = {
        "fileName": "novel-analysis-live.md",
        "extension": ".md",
        "content": SOURCE_TEXT,
        "importKind": "file",
        "documentCount": 1,
        "skippedFileCount": 0,
    }
    preview = require_data(
        request_json(base_url, "POST", "/api/novel-sources/import/preview", source),
        "preview source import",
    )
    if preview.get("sectionCount") != 1:
        raise RuntimeError("synthetic source did not parse as exactly one section")
    sections = [{
        "title": item["title"],
        "startCharacter": item["startCharacter"],
        "endCharacter": item["endCharacter"],
    } for item in preview["sections"]]
    return require_data(
        request_json(
            base_url,
            "POST",
            "/api/novel-sources/import/confirm",
            {
                **source,
                "title": "Novel Analysis Replacement 隔离验收来源",
                "expectedContentDigest": preview["contentDigest"],
                "confirmSingleSection": True,
                "rightsConfirmed": True,
                "modelDataBoundaryConfirmed": True,
                "sections": sections,
            },
        ),
        "confirm source import",
    )


def wait_for_terminal(base_url: str, revision_id: str, command_id: str) -> dict:
    deadline = time.monotonic() + 600
    last_state = None
    while time.monotonic() < deadline:
        runs = require_data(
            request_json(
                base_url,
                "GET",
                f"/api/novel-source-revisions/{revision_id}/analysis-runs",
            ),
            "list analysis runs",
        )
        run = next(
            (item for item in runs if item.get("commandId") == command_id),
            None,
        )
        if run is not None:
            last_state = {
                "runStatus": run.get("runStatus"),
                "workflowStatus": run.get("workflowStatus"),
                "completedUnits": run.get("completedUnits"),
                "failedUnits": run.get("failedUnits"),
                "error": run.get("error"),
            }
            if run.get("workflowStatus") in {"completed", "failed", "canceled"}:
                return run
            if run.get("runStatus") in {"done", "failed", "canceled"}:
                return run
        time.sleep(1)
    raise RuntimeError(f"analysis timed out; last state: {last_state}")


def inspect_persistence(
    database_dir: str,
    run: dict,
    *,
    minimum_sdk_requests: int = 5,
) -> dict:
    database_path = Path(database_dir) / "purrtypos.db"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        persisted = connection.execute(
            "SELECT agent_kind, status, implementation_id, implementation_version, "
            "tool_contract_version, recipe_version, artifact_schema_version, error "
            "FROM ai_agent_runs WHERE id = ?",
            [run["runId"]],
        ).fetchone()
        unit_rows = connection.execute(
            "SELECT unit_id, status, attempt, error_code FROM ai_agent_long_task_units "
            "WHERE task_id = ? ORDER BY unit_id",
            [run["taskId"]],
        ).fetchall()
        sdk_request_count = connection.execute(
            "SELECT COUNT(*) FROM ai_model_sdk_requests WHERE run_id = ?",
            [run["runId"]],
        ).fetchone()[0]
        provider_lease_count = connection.execute(
            "SELECT COUNT(*) FROM ai_provider_call_leases"
        ).fetchone()[0]
    if persisted is None:
        raise RuntimeError("Novel Analysis Run was not persisted")
    identity = {
        "agentKind": persisted["agent_kind"],
        "implementationId": persisted["implementation_id"],
        "implementationVersion": persisted["implementation_version"],
        "toolContractVersion": persisted["tool_contract_version"],
        "recipeVersion": persisted["recipe_version"],
        "artifactSchemaVersion": persisted["artifact_schema_version"],
    }
    if identity != {
        "agentKind": "novel_analysis",
        "implementationId": "purra-native",
        "implementationVersion": 1,
        "toolContractVersion": 1,
        "recipeVersion": 1,
        "artifactSchemaVersion": 1,
    }:
        raise RuntimeError(f"replacement Run identity changed: {identity}")
    if persisted["status"] != "done" or persisted["error"]:
        raise RuntimeError(
            f"replacement Run persistence is not successful: "
            f"{persisted['status']} / {persisted['error']}"
        )
    if provider_lease_count:
        raise RuntimeError(f"Provider leases leaked after completion: {provider_lease_count}")
    units = [dict(row) for row in unit_rows]
    if len(units) != 7 or any(item["status"] != "completed" for item in units):
        raise RuntimeError(f"replacement recipe units did not settle: {units}")
    if sdk_request_count < minimum_sdk_requests:
        raise RuntimeError(
            "expected live model calls, got "
            f"{sdk_request_count} SDK requests (minimum {minimum_sdk_requests})"
        )
    return {
        "identity": identity,
        "units": units,
        "modelSdkRequestCount": sdk_request_count,
        "providerLeaseCount": provider_lease_count,
    }


def validate_artifact(artifact: dict, revision_id: str) -> dict:
    if artifact.get("artifactContract") != "purrtypos.novel_analysis.review.v1":
        raise RuntimeError("review Artifact contract changed")
    if artifact.get("sourceRevisionId") != revision_id:
        raise RuntimeError("review Artifact escaped the synthetic source revision")
    if artifact.get("reviewStatus") != "pending":
        raise RuntimeError("analysis did not stop at explicit review boundary")
    overview = artifact.get("storyOverview") or {}
    summary = str(overview.get("summaryMarkdown") or "")
    evidence = overview.get("evidence") or []
    if not summary or not evidence:
        raise RuntimeError("story overview or its source evidence is missing")
    facts = artifact.get("facts") or []
    if not facts or any(not item.get("evidence") for item in facts):
        raise RuntimeError("analysis facts are missing canonical source evidence")
    evidence_items = [
        item
        for fact in facts
        for item in fact.get("evidence", [])
    ] + list(evidence)
    excerpts = "\n".join(str(item.get("excerpt") or "") for item in evidence_items)
    if not any(marker in excerpts for marker in (
        "银色航标", "潮钟", "药箱", "蓝灯", "钟声", "药匣",
    )):
        raise RuntimeError("projected evidence does not resolve the synthetic source")
    section_ids = set(artifact.get("sectionIds") or [])
    if len(section_ids) != 1 or any(
        item.get("sectionId") not in section_ids for item in evidence_items
    ):
        raise RuntimeError("projected evidence escaped the one-section source scope")
    return {
        "artifactId": artifact["artifactId"],
        "reviewStatus": artifact["reviewStatus"],
        "storyOverviewCharacters": len(summary),
        "storyOverviewEvidenceCount": len(evidence),
        "factCount": len(facts),
        "craftCardCount": len(artifact.get("craftCards") or []),
        "sourceSectionCount": len(section_ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-db", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18321")
    parser.add_argument("--resume-task")
    parser.add_argument("--revision-id")
    parser.add_argument("--existing-command-id")
    parser.add_argument("--replace-run-id")
    parser.add_argument("--conversation-id")
    args = parser.parse_args()

    model = load_model(args.model_db)
    request_json(
        args.base_url,
        "PUT",
        "/api/settings",
        {"data": {"ai_model_configs": [model]}},
    )
    if args.replace_run_id:
        if not args.revision_id or not args.conversation_id:
            raise RuntimeError(
                "--revision-id and --conversation-id are required with --replace-run-id"
            )
        revision_id = args.revision_id
        conversation = {"id": args.conversation_id}
        command_id = "novel-analysis-edit-live-" + uuid4().hex
        accepted = require_data(
            request_json(
                args.base_url,
                "POST",
                f"/api/novel-source-revisions/{revision_id}/analysis-follow-ups",
                {
                    "conversationId": args.conversation_id,
                    "replaceRunId": args.replace_run_id,
                    "runtime": runtime_for(model),
                    "prompt": (
                        "重新分析这篇合成短篇：重点说明人物行动与港口规则之间的因果，"
                        "并为每项结论保留来源证据。"
                    ),
                },
                headers={"Idempotency-Key": command_id},
            ),
            "replace Novel Analysis turn",
        )
    elif args.resume_task:
        if not args.revision_id:
            raise RuntimeError("--revision-id is required with --resume-task")
        revision_id = args.revision_id
        conversation = {"id": None}
        command_id = (
            args.existing_command_id
            or "novel-analysis-resume-live-" + uuid4().hex
        )
        if args.existing_command_id:
            accepted = {"commandStatus": "accepted"}
        else:
            accepted = require_data(
                request_json(
                    args.base_url,
                    "POST",
                    f"/api/novel-analysis-tasks/{args.resume_task}/resume",
                    {"runtime": runtime_for(model)},
                    headers={"Idempotency-Key": command_id},
                ),
                "resume Novel Analysis replacement",
            )
    else:
        revision = import_fixture(args.base_url)
        revision_id = str(revision["id"])
        conversation = require_data(
            request_json(
                args.base_url,
                "POST",
                f"/api/novel-source-revisions/{revision_id}/conversations",
            ),
            "create analysis conversation",
        )
        command_id = "novel-analysis-live-" + uuid4().hex
        accepted = require_data(
            request_json(
                args.base_url,
                "POST",
                f"/api/novel-source-revisions/{revision_id}/analyses",
                {
                    "conversationId": conversation["id"],
                    "runtime": runtime_for(model),
                    "prompt": (
                        "分析这篇合成短篇，提取人物、事件和因果，生成有证据的故事概览；"
                        "所有结论必须通过来源段落工具核实，不能猜测。"
                    ),
                },
                headers={"Idempotency-Key": command_id},
            ),
            "start Novel Analysis replacement",
        )
    if (
        accepted.get("status") != "accepted"
        and accepted.get("commandStatus") != "accepted"
    ):
        raise RuntimeError(f"analysis was not accepted: {accepted}")
    run = wait_for_terminal(args.base_url, revision_id, command_id)
    if (
        run.get("workflowStatus") != "completed"
        or run.get("completedUnits") != 7
        or run.get("failedUnits") != 0
    ):
        raise RuntimeError(f"Novel Analysis workflow did not complete: {run}")
    artifact_ref = str(run.get("artifactRef") or "")
    if not artifact_ref.startswith(REVIEW_REF_PREFIX):
        raise RuntimeError(f"replacement review Artifact is missing: {artifact_ref}")
    artifact = require_data(
        request_json(
            args.base_url,
            "GET",
            "/api/novel-analysis-artifacts/" + artifact_ref.removeprefix(REVIEW_REF_PREFIX),
        ),
        "load replacement review Artifact",
    )
    persistence = inspect_persistence(
        args.data_dir,
        run,
        minimum_sdk_requests=1 if args.resume_task else 5,
    )
    projected = validate_artifact(artifact, revision_id)
    edit_projection = {}
    if args.replace_run_id:
        database_path = Path(args.data_dir) / "purrtypos.db"
        with sqlite3.connect(database_path) as connection:
            superseded_ids = [
                str(row[0]) for row in connection.execute(
                    "SELECT run_id FROM novel_analysis_superseded_runs "
                    "WHERE target_run_id = ? ORDER BY run_id",
                    [args.replace_run_id],
                )
            ]
        visible_ids = [
            str(item["runId"])
            for item in require_data(
                request_json(
                    args.base_url,
                    "GET",
                    f"/api/novel-source-revisions/{revision_id}/analysis-runs",
                ),
                "list edited analysis runs",
            )
        ]
        if args.replace_run_id not in superseded_ids:
            raise RuntimeError("edited target Run was not superseded")
        if args.replace_run_id in visible_ids or run["runId"] not in visible_ids:
            raise RuntimeError(
                f"edited branch projection is inconsistent: visible={visible_ids}"
            )
        edit_projection = {
            "replaceRunId": args.replace_run_id,
            "supersededRunIds": superseded_ids,
            "visibleRunIds": visible_ids,
        }
    print(json.dumps({
        "sourceRevisionId": revision_id,
        "conversationId": conversation["id"],
        "resumedTaskId": args.resume_task,
        "commandId": command_id,
        "runId": run["runId"],
        "taskId": run["taskId"],
        "workflowStatus": run["workflowStatus"],
        **projected,
        **persistence,
        **edit_projection,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
