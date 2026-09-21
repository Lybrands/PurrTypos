#!/usr/bin/env python3
"""Verify live pause/resume and terminal cancel semantics for Novel Analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
import urllib.request
from uuid import uuid4


CHECKPOINT_NAME = "novel-analysis-control-checkpoint.json"
SOURCE_TEXT = (
    "# 雾灯岛\n"
    "雾潮升起时，守塔人沈岚只点亮东侧蓝灯。钟声第二次响起后，"
    "信使顾舟把密封药匣交给她，并提醒西侧栈桥已经断裂。"
)


class RequestError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


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
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RequestError(method, path, error.code, detail) from error


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
    for config in json.loads(row[0]):
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


def import_fixture(base_url: str, *, suffix: str) -> dict:
    source = {
        "fileName": f"novel-analysis-control-{suffix}.md",
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
    sections = [{
        "title": item["title"],
        "startCharacter": item["startCharacter"],
        "endCharacter": item["endCharacter"],
    } for item in preview["sections"]]
    if len(sections) != 1:
        raise RuntimeError("synthetic source did not parse as exactly one section")
    return require_data(
        request_json(
            base_url,
            "POST",
            "/api/novel-sources/import/confirm",
            {
                **source,
                "title": f"Novel Analysis 控制验收 {suffix}",
                "expectedContentDigest": preview["contentDigest"],
                "confirmSingleSection": True,
                "rightsConfirmed": True,
                "modelDataBoundaryConfirmed": True,
                "sections": sections,
            },
        ),
        "confirm source import",
    )


def list_runs(base_url: str, revision_id: str) -> list[dict]:
    return require_data(
        request_json(
            base_url,
            "GET",
            f"/api/novel-source-revisions/{revision_id}/analysis-runs",
        ),
        "list analysis runs",
    )


def find_task_run(runs: list[dict], task_id: str) -> dict | None:
    return next(
        (
            item for item in runs
            if item.get("taskId") == task_id and item.get("taskRevision") is not None
        ),
        None,
    )


def start_analysis(base_url: str, runtime: dict, *, suffix: str) -> dict:
    revision = import_fixture(base_url, suffix=suffix)
    revision_id = str(revision["id"])
    conversation = require_data(
        request_json(
            base_url,
            "POST",
            f"/api/novel-source-revisions/{revision_id}/conversations",
        ),
        "create analysis conversation",
    )
    command_id = f"novel-analysis-control-{suffix}-" + uuid4().hex
    accepted = require_data(
        request_json(
            base_url,
            "POST",
            f"/api/novel-source-revisions/{revision_id}/analyses",
            {
                "conversationId": conversation["id"],
                "runtime": runtime,
                "prompt": (
                    "分析这篇合成短篇，提取人物、事件、规则和因果，生成带来源证据的"
                    "故事概览；所有结论必须通过来源段落工具核实。"
                ),
            },
            headers={"Idempotency-Key": command_id},
        ),
        "start Novel Analysis replacement",
    )
    if accepted.get("status") != "accepted":
        raise RuntimeError(f"analysis was not accepted: {accepted}")
    return {
        "revisionId": revision_id,
        "conversationId": conversation["id"],
        "commandId": command_id,
    }


def wait_until_interruptible(base_url: str, revision_id: str, command_id: str) -> dict:
    deadline = time.monotonic() + 600
    last_state = None
    while time.monotonic() < deadline:
        run = next(
            (item for item in list_runs(base_url, revision_id)
             if item.get("commandId") == command_id),
            None,
        )
        if run is not None:
            last_state = {
                "runStatus": run.get("runStatus"),
                "workflowStatus": run.get("workflowStatus"),
                "completedUnits": run.get("completedUnits"),
                "taskRevision": run.get("taskRevision"),
            }
            if (
                run.get("workflowStatus") == "running"
                and int(run.get("completedUnits") or 0) >= 1
                and run.get("taskRevision") is not None
            ):
                return run
            if run.get("workflowStatus") in {"completed", "failed", "canceled"}:
                raise RuntimeError(f"analysis settled before control request: {last_state}")
        time.sleep(0.25)
    raise RuntimeError(f"analysis never became interruptible: {last_state}")


def pause_with_revision_retry(base_url: str, revision_id: str, run: dict) -> dict:
    deadline = time.monotonic() + 20
    current = run
    while time.monotonic() < deadline:
        try:
            return require_data(
                request_json(
                    base_url,
                    "POST",
                    f"/api/novel-analysis-tasks/{current['taskId']}/pause",
                    {"expectedTaskRevision": current["taskRevision"]},
                ),
                "pause Novel Analysis task",
            )
        except RequestError as error:
            if error.status != 409:
                raise
        latest = find_task_run(list_runs(base_url, revision_id), current["taskId"])
        if latest is None or latest.get("workflowStatus") != "running":
            raise RuntimeError(f"pause lost its active task: {latest}")
        current = latest
    raise RuntimeError("pause could not win the optimistic revision race")


def wait_for_workflow(
    base_url: str,
    revision_id: str,
    task_id: str,
    expected: str,
    *,
    timeout: int = 600,
) -> dict:
    deadline = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < deadline:
        runs = list_runs(base_url, revision_id)
        run = find_task_run(runs, task_id)
        if run is not None:
            last_state = {
                "runId": run.get("runId"),
                "runStatus": run.get("runStatus"),
                "workflowStatus": run.get("workflowStatus"),
                "completedUnits": run.get("completedUnits"),
                "failedUnits": run.get("failedUnits"),
                "taskRevision": run.get("taskRevision"),
                "error": run.get("error"),
            }
            run_terminal = run.get("runStatus") in {"done", "failed", "canceled"}
            if run.get("workflowStatus") == expected and run_terminal:
                return run
            if run.get("workflowStatus") in {"failed", "canceled", "completed"}:
                if run.get("workflowStatus") != expected:
                    raise RuntimeError(f"workflow settled unexpectedly: {last_state}")
        time.sleep(0.5)
    raise RuntimeError(f"workflow did not become {expected}: {last_state}")


def persisted_state(database_dir: str, task_id: str) -> dict:
    database_path = Path(database_dir) / "purrtypos.db"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        task = connection.execute(
            "SELECT status, revision, completed_units, failed_units, state_reason_code "
            "FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        ).fetchone()
        units = connection.execute(
            "SELECT unit_id, status, attempt, output_ref, artifact_digest, run_id, "
            "error_code FROM ai_agent_long_task_units WHERE task_id = ? "
            "ORDER BY position, unit_id",
            [task_id],
        ).fetchall()
        bindings = connection.execute(
            "SELECT run_id, relation FROM ai_agent_long_task_runs WHERE task_id = ? "
            "ORDER BY create_time, run_id",
            [task_id],
        ).fetchall()
        leases = connection.execute(
            "SELECT COUNT(*) FROM ai_provider_call_leases"
        ).fetchone()[0]
    if task is None:
        raise RuntimeError("control task is missing from persistence")
    return {
        "task": dict(task),
        "units": [dict(row) for row in units],
        "bindings": [dict(row) for row in bindings],
        "providerLeaseCount": leases,
    }


def wait_for_no_leases(database_dir: str, task_id: str) -> dict:
    deadline = time.monotonic() + 30
    state = persisted_state(database_dir, task_id)
    while state["providerLeaseCount"] and time.monotonic() < deadline:
        time.sleep(0.25)
        state = persisted_state(database_dir, task_id)
    if state["providerLeaseCount"]:
        raise RuntimeError(
            f"Provider leases leaked after workflow settled: {state['providerLeaseCount']}"
        )
    return state


def completed_unit_identity(state: dict) -> dict[str, dict]:
    return {
        str(unit["unit_id"]): {
            "attempt": unit["attempt"],
            "outputRef": unit["output_ref"],
            "artifactDigest": unit["artifact_digest"],
            "runId": unit["run_id"],
        }
        for unit in state["units"]
        if unit["status"] == "completed"
    }


def checkpoint_path(database_dir: str) -> Path:
    return Path(database_dir) / CHECKPOINT_NAME


def configure_model(base_url: str, model: dict) -> None:
    request_json(
        base_url,
        "PUT",
        "/api/settings",
        {"data": {"ai_model_configs": [model]}},
    )


def run_pause(args, runtime: dict) -> None:
    started = start_analysis(args.base_url, runtime, suffix="pause")
    active = wait_until_interruptible(
        args.base_url, started["revisionId"], started["commandId"]
    )
    receipt = pause_with_revision_retry(args.base_url, started["revisionId"], active)
    paused = wait_for_workflow(
        args.base_url, started["revisionId"], active["taskId"], "paused"
    )
    state = wait_for_no_leases(args.data_dir, active["taskId"])
    preserved = completed_unit_identity(state)
    if not preserved:
        raise RuntimeError("pause did not preserve any completed Unit")
    checkpoint = {
        **started,
        "taskId": active["taskId"],
        "originalRunId": active["runId"],
        "pauseReceipt": receipt,
        "completedUnitIdentity": preserved,
    }
    path = checkpoint_path(args.data_dir)
    path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "phase": "paused",
        "checkpointPath": str(path),
        "runStatus": paused["runStatus"],
        "workflowStatus": paused["workflowStatus"],
        "workflowResumable": paused["workflowResumable"],
        "completedUnits": paused["completedUnits"],
        "preservedUnitIds": sorted(preserved),
        **checkpoint,
        "providerLeaseCount": state["providerLeaseCount"],
    }, ensure_ascii=False, indent=2))


def run_resume(args, runtime: dict) -> None:
    path = checkpoint_path(args.data_dir)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    command_id = "novel-analysis-control-resume-" + uuid4().hex
    accepted = require_data(
        request_json(
            args.base_url,
            "POST",
            f"/api/novel-analysis-tasks/{checkpoint['taskId']}/resume",
            {"runtime": runtime},
            headers={"Idempotency-Key": command_id},
        ),
        "resume Novel Analysis task",
    )
    if accepted.get("commandStatus") != "accepted":
        raise RuntimeError(f"resume was not accepted: {accepted}")
    completed = wait_for_workflow(
        args.base_url,
        checkpoint["revisionId"],
        checkpoint["taskId"],
        "completed",
    )
    state = wait_for_no_leases(args.data_dir, checkpoint["taskId"])
    if state["task"]["completed_units"] != 7 or state["task"]["failed_units"] != 0:
        raise RuntimeError(f"resumed task did not complete all Units: {state['task']}")
    after = completed_unit_identity(state)
    for unit_id, before in checkpoint["completedUnitIdentity"].items():
        if after.get(unit_id) != before:
            raise RuntimeError(
                f"completed Unit was rerun or replaced during resume: {unit_id}: "
                f"before={before}, after={after.get(unit_id)}"
            )
    bindings = state["bindings"]
    relations = [item["relation"] for item in bindings]
    if relations != ["created", "continuation"]:
        raise RuntimeError(f"unexpected task Run bindings: {bindings}")
    if bindings[0]["run_id"] != checkpoint["originalRunId"]:
        raise RuntimeError("resume changed the original task owner Run")
    if completed["runId"] == checkpoint["originalRunId"]:
        raise RuntimeError("resume did not create a continuation Root Run")
    print(json.dumps({
        "phase": "resumed",
        "revisionId": checkpoint["revisionId"],
        "conversationId": checkpoint["conversationId"],
        "taskId": checkpoint["taskId"],
        "originalRunId": checkpoint["originalRunId"],
        "continuationRunId": completed["runId"],
        "resumeCommandId": command_id,
        "workflowStatus": completed["workflowStatus"],
        "completedUnits": completed["completedUnits"],
        "reusedUnitIds": sorted(checkpoint["completedUnitIdentity"]),
        "bindings": bindings,
        "providerLeaseCount": state["providerLeaseCount"],
    }, ensure_ascii=False, indent=2))


def run_cancel(args, runtime: dict) -> None:
    started = start_analysis(args.base_url, runtime, suffix="cancel")
    active = wait_until_interruptible(
        args.base_url, started["revisionId"], started["commandId"]
    )
    receipt = require_data(
        request_json(
            args.base_url,
            "POST",
            f"/api/novel-analysis-tasks/{active['taskId']}/cancel",
        ),
        "cancel Novel Analysis task",
    )
    canceled = wait_for_workflow(
        args.base_url, started["revisionId"], active["taskId"], "canceled"
    )
    state = wait_for_no_leases(args.data_dir, active["taskId"])
    if canceled.get("workflowResumable"):
        raise RuntimeError("canceled workflow was incorrectly marked resumable")
    try:
        request_json(
            args.base_url,
            "POST",
            f"/api/novel-analysis-tasks/{active['taskId']}/resume",
            {"runtime": runtime},
            headers={"Idempotency-Key": "cancel-resume-rejected-" + uuid4().hex},
        )
    except RequestError as error:
        if error.status != 409:
            raise
        resume_rejection = {"status": error.status, "detail": error.detail}
    else:
        raise RuntimeError("canceled Novel Analysis task unexpectedly accepted resume")
    print(json.dumps({
        "phase": "canceled",
        **started,
        "taskId": active["taskId"],
        "runId": active["runId"],
        "cancelReceipt": receipt,
        "runStatus": canceled["runStatus"],
        "workflowStatus": canceled["workflowStatus"],
        "workflowResumable": canceled["workflowResumable"],
        "completedUnits": canceled["completedUnits"],
        "resumeRejection": resume_rejection,
        "providerLeaseCount": state["providerLeaseCount"],
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("pause", "resume", "cancel"))
    parser.add_argument("--model-db", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18321")
    args = parser.parse_args()

    model = load_model(args.model_db)
    configure_model(args.base_url, model)
    runtime = runtime_for(model)
    {"pause": run_pause, "resume": run_resume, "cancel": run_cancel}[args.phase](
        args, runtime
    )


if __name__ == "__main__":
    main()
