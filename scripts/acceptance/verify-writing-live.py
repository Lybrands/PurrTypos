#!/usr/bin/env python3
"""Run a content-isolated Writing Agent live acceptance against localhost."""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
import time
import urllib.request
from uuid import uuid4


SEMANTIC_EMBEDDING_PORT = 18322
SEMANTIC_EMBEDDING_BASE_URL = (
    f"http://127.0.0.1:{SEMANTIC_EMBEDDING_PORT}/v1"
)


class SemanticEmbeddingFixture:
    """Local deterministic embeddings for isolated memory-store setup only."""

    def __init__(self) -> None:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib callback name
                if self.path != "/v1/embeddings":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                values = payload.get("input") or []
                if isinstance(values, str):
                    values = [values]
                fixture.calls += 1
                data = []
                for index, value in enumerate(values):
                    digest = hashlib.sha256(str(value).encode()).digest()
                    embedding = [
                        (digest[offset] + 1) / 256
                        for offset in range(4)
                    ]
                    data.append({
                        "object": "embedding",
                        "index": index,
                        "embedding": embedding,
                    })
                response = json.dumps({
                    "object": "list",
                    "data": data,
                    "model": "purrtypos-acceptance-embedding",
                    "usage": {
                        "prompt_tokens": sum(len(str(value)) for value in values),
                        "total_tokens": sum(len(str(value)) for value in values),
                    },
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, _format, *_args):
                return

        self.calls = 0
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", SEMANTIC_EMBEDDING_PORT), Handler
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="writing-semantic-embedding-fixture",
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc_info):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def request_json(base_url: str, method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


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


def stream_run(base_url: str, payload: dict) -> dict:
    request_id = payload["streamId"]
    reservation = request_json(
        base_url,
        "PUT",
        f"/api/ai/chat/requests/{request_id}",
        payload,
    )
    if not reservation.get("success"):
        raise RuntimeError("Writing request reservation failed")

    request = urllib.request.Request(
        base_url + "/api/ai/chat/stream",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    run_id = None
    tool_names: list[str] = []
    final_text: list[str] = []
    terminal = None
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            raw_payload = line[6:].strip()
            if not raw_payload or raw_payload == "[DONE]":
                continue
            event = json.loads(raw_payload)
            receipt = event.get("requestReceipt") or {}
            run_id = receipt.get("runId") or run_id
            result = event.get("runResult") or {}
            run_id = result.get("runId") or run_id
            if result:
                terminal = result
            tool_call = event.get("toolCall") or {}
            name = tool_call.get("name")
            if name and name not in tool_names:
                tool_names.append(name)
            if event.get("content"):
                final_text.append(str(event["content"]))
    return {
        "runId": run_id,
        "streamTools": tool_names,
        "terminal": terminal,
        "visibleText": "".join(final_text),
    }


def inspect_run(database_dir: str, run_id: str, scenario: str) -> dict:
    database_path = Path(database_dir) / "purrtypos.db"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        run = connection.execute(
            "SELECT status, implementation_id, implementation_version, "
            "tool_contract_version, recipe_version, artifact_schema_version, "
            "error, final_response, binding_attributes_json "
            "FROM ai_agent_runs WHERE id = ?",
            [run_id],
        ).fetchone()
        rows = connection.execute(
            "SELECT event_type, payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND event_type IN "
            "('tool.calls_started', 'tool.results') ORDER BY id",
            [run_id],
        ).fetchall()
    if run is None:
        raise RuntimeError("Writing Run was not persisted")
    calls: list[str] = []
    call_arguments: dict[str, list[dict]] = {}
    tool_results: dict[str, dict] = {}
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        if row["event_type"] == "tool.calls_started":
            for item in payload.get("calls", ()):
                name = str(item.get("name"))
                calls.append(name)
                try:
                    arguments = json.loads(item.get("arguments_json") or "{}")
                except json.JSONDecodeError as error:
                    raise RuntimeError(
                        f"persisted arguments are invalid for {name}"
                    ) from error
                call_arguments.setdefault(name, []).append(arguments)
        for item in payload.get("results", ()):
            name = str(item.get("tool_name") or "")
            if name:
                tool_results[name] = json.loads(item["content"])
    if scenario == "continuation-drift":
        if (
            run["status"] != "failed"
            or run["error"] != "continuation_context_unavailable"
        ):
            raise RuntimeError(
                "Writing drift Run did not fail closed: "
                f"{run['status']} / {run['error']}"
            )
    elif run["status"] != "done":
        raise RuntimeError(f"Writing Run did not complete: {run['status']}")
    result = {
        "identity": {
            "implementationId": run["implementation_id"],
            "implementationVersion": run["implementation_version"],
            "toolContractVersion": run["tool_contract_version"],
            "recipeVersion": run["recipe_version"],
            "artifactSchemaVersion": run["artifact_schema_version"],
        },
        "tools": calls,
        "finalResponse": run["final_response"],
    }
    if scenario == "characters":
        character_result = tool_results.get("listBookCharacters")
        if "listBookCharacters" not in calls or character_result is None:
            raise RuntimeError("model did not select listBookCharacters")
        if character_result.get("total") != 3:
            raise RuntimeError("host character total was not 3")
        if character_result.get("countScope") != "current_book_owned_characters":
            raise RuntimeError("host character count scope changed")
        result["characterResult"] = {
            "total": character_result["total"],
            "countScope": character_result["countScope"],
            "returnedItems": len(character_result.get("items", ())),
        }
        return result

    binding = json.loads(run["binding_attributes_json"] or "{}")
    selection = binding.get("contextSelection") or {}
    final_response = str(run["final_response"] or "")
    if scenario == "associated":
        associated = tool_results.get("readAssociatedWritingContext")
        if "readAssociatedWritingContext" not in calls or associated is None:
            raise RuntimeError("model did not select readAssociatedWritingContext")
        encoded = json.dumps(associated, ensure_ascii=False)
        for marker in ("蓝鲸钟", "赤色潮汐"):
            if marker not in encoded or marker not in final_response:
                raise RuntimeError(f"associated context marker missing: {marker}")
        if len(selection.get("associatedChapterIds") or ()) != 1:
            raise RuntimeError("associated chapter selection was not frozen")
        if len(selection.get("associatedOutlineIds") or ()) != 1:
            raise RuntimeError("associated outline selection was not frozen")
        result["associatedResult"] = {
            "chapterCount": len(associated.get("chapters") or ()),
            "outlineCount": len(associated.get("outlines") or ()),
            "selection": selection,
        }
        return result

    if scenario == "technique":
        expected_tools = {
            "listWritingTechniqueCandidates",
            "readWritingTechniqueContext",
        }
        if set(calls) != expected_tools:
            raise RuntimeError(f"unexpected technique tool sequence: {calls}")
        if calls.index("listWritingTechniqueCandidates") > calls.index(
            "readWritingTechniqueContext"
        ):
            raise RuntimeError("model read a technique before listing candidates")
        candidates = tool_results.get("listWritingTechniqueCandidates") or {}
        technique = tool_results.get("readWritingTechniqueContext") or {}
        items = candidates.get("items") or ()
        if candidates.get("mode") != "auto" or len(items) != 2:
            raise RuntimeError("automatic technique candidate snapshot changed")
        target = next(
            (
                item for item in items
                if (item.get("metadata") or {}).get("name") == "潮汐意象"
            ),
            None,
        )
        if target is None:
            raise RuntimeError("target technique candidate is missing")
        automatic_calls = call_arguments.get("readWritingTechniqueContext") or ()
        automatic_refs = automatic_calls[0].get("automaticRefs") or ()
        if automatic_refs != [target["ref"]]:
            raise RuntimeError("model did not freeze the intended automatic ref")
        entries = technique.get("entries") or ()
        if len(entries) != 1 or target["ref"] not in entries[0].get("sources", ()):
            raise RuntimeError("selected technique entry does not match candidate")
        if "琥珀雨" not in entries[0].get("content", "") or "琥珀雨" not in final_response:
            raise RuntimeError("selected technique marker missing")
        frozen_input = (binding.get("writingContextSnapshot") or {}).get(
            "writingTechniqueInput"
        ) or {}
        input_id = selection.get("writingTechniqueInputId")
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT request_digest, snapshot_json FROM "
                "writing_technique_request_inputs WHERE id = ?",
                [input_id],
            ).fetchone()
        if row is None or frozen_input.get("inputId") != input_id:
            raise RuntimeError("technique input was not frozen in Run binding")
        snapshot_digest = "sha256:" + hashlib.sha256(row[1].encode()).hexdigest()
        if (
            frozen_input.get("requestDigest") != row[0]
            or frozen_input.get("snapshotDigest") != snapshot_digest
        ):
            raise RuntimeError("technique input digests do not match persisted snapshot")
        frozen_candidates = (json.loads(row[1]).get("candidates") or ())
        if len(frozen_candidates) != 2:
            raise RuntimeError("persisted automatic candidate set changed")
        result["techniqueResult"] = {
            "mode": candidates["mode"],
            "candidateCount": len(items),
            "selectedRef": target["ref"],
            "automaticRefs": automatic_refs,
            "inputSnapshot": frozen_input,
        }
        return result

    if scenario == "continuation":
        expected_tools = {
            "listContinuationSourceSections",
            "readContinuationSourceSection",
        }
        if set(calls) != expected_tools:
            raise RuntimeError(f"unexpected continuation tool sequence: {calls}")
        if calls.index("listContinuationSourceSections") > calls.index(
            "readContinuationSourceSection"
        ):
            raise RuntimeError("model read continuation source before listing it")
        directory = tool_results.get("listContinuationSourceSections") or {}
        section_result = tool_results.get("readContinuationSourceSection") or {}
        items = directory.get("items") or ()
        if directory.get("total") != 2 or directory.get("chapterCount") != 2:
            raise RuntimeError("continuation directory crossed the frozen fork")
        target = next(
            (item for item in items if item.get("title") == "潮门之前"),
            None,
        )
        if target is None:
            raise RuntimeError("target continuation source section is missing")
        read_calls = call_arguments.get("readContinuationSourceSection") or ()
        if len(read_calls) != 1 or read_calls[0].get("sectionId") != target.get(
            "sectionId"
        ):
            raise RuntimeError("model did not read the exact listed section id")
        section = section_result.get("section") or {}
        encoded = json.dumps(section_result, ensure_ascii=False)
        if section.get("sectionId") != target.get("sectionId"):
            raise RuntimeError("read section does not match directory target")
        if "白潮刻痕" not in encoded or "白潮刻痕" not in final_response:
            raise RuntimeError("continuation source marker is missing")
        if "黑曜余烬" in encoded or "黑曜余烬" in final_response:
            raise RuntimeError("post-fork source leaked into the continuation Run")

        frozen = (binding.get("writingContextSnapshot") or {}).get(
            "continuation"
        ) or {}
        frozen_binding = frozen.get("binding") or {}
        if frozen.get("creationMode") != "continuation":
            raise RuntimeError("continuation creation mode was not frozen")
        required_binding_fields = {
            "id",
            "targetBookId",
            "sourceWorkId",
            "sourceRevisionId",
            "sourceAnalysisId",
            "forkSectionId",
            "forkOrdinal",
            "canonSnapshotId",
            "canonSnapshotDigest",
            "bindingDigest",
        }
        if not required_binding_fields.issubset(frozen_binding):
            raise RuntimeError("continuation binding is incomplete in Run snapshot")
        if int(frozen_binding.get("forkOrdinal", -1)) != 1:
            raise RuntimeError("continuation Run froze the wrong fork ordinal")
        if section.get("sourceRevisionId") != frozen_binding.get(
            "sourceRevisionId"
        ) or section.get("canonSnapshotId") != frozen_binding.get(
            "canonSnapshotId"
        ):
            raise RuntimeError("section read does not match frozen continuation binding")
        with sqlite3.connect(database_path) as connection:
            current = connection.execute(
                "SELECT cb.binding_digest, cb.fork_section_id, "
                "cs.content_digest AS canon_snapshot_digest "
                "FROM continuation_bindings AS cb "
                "JOIN continuation_canon_snapshots AS cs "
                "ON cs.id = cb.canon_snapshot_id "
                "WHERE cb.target_book_id = ?",
                [frozen_binding["targetBookId"]],
            ).fetchone()
            frozen_section_count = connection.execute(
                "SELECT COUNT(*) FROM continuation_source_sections "
                "WHERE book_id = ?",
                [frozen_binding["targetBookId"]],
            ).fetchone()[0]
        if current is None or tuple(current) != (
            frozen_binding["bindingDigest"],
            frozen_binding["forkSectionId"],
            frozen_binding["canonSnapshotDigest"],
        ):
            raise RuntimeError("persisted continuation binding differs from Run snapshot")
        if frozen_section_count != 2:
            raise RuntimeError("frozen continuation source snapshot has wrong size")
        result["continuationResult"] = {
            "directoryCount": len(items),
            "selectedSectionId": target["sectionId"],
            "selectedSectionTitle": target["title"],
            "binding": frozen_binding,
        }
        return result

    if scenario == "continuation-drift":
        expected_tools = {
            "listContinuationSourceSections",
            "readContinuationSourceSection",
        }
        if set(calls) != expected_tools:
            raise RuntimeError(f"unexpected drift tool sequence: {calls}")
        if calls.index("listContinuationSourceSections") > calls.index(
            "readContinuationSourceSection"
        ):
            raise RuntimeError("drift Run read source before listing it")
        directory = tool_results.get("listContinuationSourceSections") or {}
        failed_read = tool_results.get("readContinuationSourceSection") or {}
        if directory.get("total") != 2 or directory.get("chapterCount") != 2:
            raise RuntimeError("drift Run did not receive the frozen directory")
        if (
            failed_read.get("success") is not False
            or failed_read.get("code") != "continuation_context_unavailable"
            or "continuation_context_snapshot_changed"
            not in str(failed_read.get("error") or "")
        ):
            raise RuntimeError("continuation drift was not rejected by the read tool")
        if "白潮刻痕" in final_response or "黑曜余烬" in final_response:
            raise RuntimeError("source content leaked after continuation drift")
        frozen = (binding.get("writingContextSnapshot") or {}).get(
            "continuation"
        ) or {}
        frozen_binding = frozen.get("binding") or {}
        with sqlite3.connect(database_path) as connection:
            current = connection.execute(
                "SELECT binding_digest FROM continuation_bindings "
                "WHERE target_book_id = ?",
                [frozen_binding.get("targetBookId")],
            ).fetchone()
        if (
            current is None
            or not frozen_binding.get("bindingDigest")
            or current[0] == frozen_binding["bindingDigest"]
        ):
            raise RuntimeError("continuation drift injection was not persisted")
        result["continuationDriftResult"] = {
            "directoryCount": len(directory.get("items") or ()),
            "errorCode": failed_read["code"],
            "frozenBindingDigest": frozen_binding["bindingDigest"],
            "currentBindingDigest": current[0],
            "sourceContentLeaked": False,
        }
        return result

    selected = tool_results.get("readSelectedWritingContext")
    if "readSelectedWritingContext" not in calls or selected is None:
        raise RuntimeError("model did not select readSelectedWritingContext")
    long_term = selected.get("longTermMemory") or {}
    if long_term.get("available") is not True:
        raise RuntimeError(
            f"selected long-term memory unavailable: {long_term.get('reason')}"
        )
    encoded = json.dumps(long_term, ensure_ascii=False)
    if "银色航标" not in encoded or "银色航标" not in final_response:
        raise RuntimeError("semantic memory marker missing")
    selected_ids = selection.get("selectedLongTermMemoryIds") or ()
    snapshot = binding.get("writingContextSnapshot") or {}
    refs = (snapshot.get("longTermMemory") or {}).get("refs") or ()
    if len(selected_ids) != 1 or len(refs) != 1:
        raise RuntimeError("semantic memory selection/version was not frozen")
    if str(refs[0].get("id")) != str(selected_ids[0]):
        raise RuntimeError("semantic memory snapshot does not match selection")
    result["semanticMemoryResult"] = {
        "available": long_term["available"],
        "itemCount": len(long_term.get("items") or ()),
        "selection": selection,
        "snapshot": snapshot.get("longTermMemory"),
    }
    return result


def lexical_text(value: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "children": [{
                    "detail": 0,
                    "format": 0,
                    "mode": "normal",
                    "style": "",
                    "text": value,
                    "type": "text",
                    "version": 1,
                }],
                "direction": "ltr",
                "format": "",
                "indent": 0,
                "type": "paragraph",
                "version": 1,
                "textFormat": 0,
                "textStyle": "",
            }],
            "direction": "ltr",
            "format": "",
            "indent": 0,
            "type": "root",
            "version": 1,
        }
    }, ensure_ascii=False)


def require_data(response: dict, operation: str):
    if not response.get("success"):
        raise RuntimeError(f"{operation} failed: {response.get('code') or response.get('error')}")
    return response["data"]


def create_published_technique(
    base_url: str,
    *,
    name: str,
    description: str,
    instructions: str,
) -> dict:
    operation = "writing-technique-live-" + uuid4().hex
    content = (
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"{instructions}\n"
    )
    draft = require_data(
        request_json(
            base_url,
            "POST",
            "/api/writing-techniques/import",
            {"operationId": operation, "files": {"SKILL.md": content}},
        ),
        f"import technique {name}",
    )
    sealed = require_data(
        request_json(
            base_url,
            "POST",
            "/api/writing-techniques/objects/technique/"
            f"{draft['techniqueId']}/drafts/{draft['draftId']}/seal",
            {
                "operationId": operation + ":seal",
                "expectedDraftRevision": draft["draftRevision"],
                "expectedTreeDigest": draft["treeDigest"],
            },
        ),
        f"seal technique {name}",
    )
    ref = sealed["sealedRef"]
    require_data(
        request_json(
            base_url,
            "POST",
            "/api/writing-techniques/objects/technique/"
            f"{ref['id']}/publish",
            {
                "operationId": operation + ":publish",
                "ref": ref,
                "expectedPublishedHead": None,
            },
        ),
        f"publish technique {name}",
    )
    return ref


def create_continuation_fixture(base_url: str, database_dir: str) -> dict:
    content = (
        "# 雾港初鸣\n本节的隔离暗号是雾灯铜铃。\n\n"
        "# 潮门之前\n本节的续写来源暗号是白潮刻痕。\n\n"
        "# 分叉之后\n这里位于分叉点之后，禁读暗号是黑曜余烬。"
    )
    source_payload = {
        "fileName": "continuation-live.md",
        "extension": ".md",
        "content": content,
    }
    preview = require_data(
        request_json(
            base_url,
            "POST",
            "/api/novel-sources/import/preview",
            source_payload,
        ),
        "preview continuation source",
    )
    revision = require_data(
        request_json(
            base_url,
            "POST",
            "/api/novel-sources/import/confirm",
            {
                **source_payload,
                "title": "Writing Continuation 隔离原作",
                "expectedContentDigest": preview["contentDigest"],
                "confirmSingleSection": False,
                "rightsConfirmed": True,
                "modelDataBoundaryConfirmed": True,
            },
        ),
        "confirm continuation source",
    )
    sections = revision.get("sections") or ()
    if [item.get("title") for item in sections] != [
        "雾港初鸣",
        "潮门之前",
        "分叉之后",
    ]:
        raise RuntimeError("continuation source parser did not produce three chapters")
    analysis_id = "analysis_" + uuid4().hex
    database_path = Path(database_dir) / "purrtypos.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO novel_source_analyses "
            "(id, source_revision_id, version_no, coverage_end_ordinal, "
            "schema_version, content_digest, summary_json) "
            "VALUES (?, ?, 1, 1, 1, ?, '{}')",
            [analysis_id, revision["id"], "acceptance-analysis-" + uuid4().hex],
        )
        connection.commit()
    fork = sections[1]
    canon_preview = require_data(
        request_json(
            base_url,
            "POST",
            "/api/continuations/canon-preview",
            {
                "sourceRevisionId": revision["id"],
                "sourceAnalysisId": analysis_id,
                "forkSectionId": fork["id"],
            },
        ),
        "preview continuation canon",
    )
    created = require_data(
        request_json(
            base_url,
            "POST",
            "/api/continuations",
            {
                "title": "Writing Replacement 隔离续写书",
                "sourceRevisionId": revision["id"],
                "sourceAnalysisId": analysis_id,
                "forkSectionId": fork["id"],
                "expectedSnapshotDigest": canon_preview["snapshotDigest"],
                "operationId": "writing-continuation-live-" + uuid4().hex,
                "useSourceTechniques": False,
            },
        ),
        "create continuation fixture",
    )
    return {
        "book": created["book"],
        "sourceRevisionId": revision["id"],
        "sourceAnalysisId": analysis_id,
        "forkSectionId": fork["id"],
    }


def inject_continuation_drift_after_directory(
    database_dir: str,
    book_id: str,
    outcome: dict,
) -> None:
    database_path = Path(database_dir) / "purrtypos.db"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with sqlite3.connect(database_path, timeout=1) as connection:
                observed = connection.execute(
                    "SELECT 1 FROM ai_agent_run_events AS e "
                    "JOIN ai_agent_runs AS r ON r.id = e.run_id "
                    "WHERE e.event_type = 'tool.results' "
                    "AND e.payload_json LIKE '%listContinuationSourceSections%' "
                    "AND json_extract(r.binding_attributes_json, '$.bookId') = ? "
                    "LIMIT 1",
                    [book_id],
                ).fetchone()
                if observed is not None:
                    replacement = "sha256:acceptance-drift-" + uuid4().hex
                    connection.execute(
                        "UPDATE continuation_bindings SET binding_digest = ? "
                        "WHERE target_book_id = ?",
                        [replacement, book_id],
                    )
                    connection.commit()
                    outcome.update({
                        "injected": True,
                        "currentBindingDigest": replacement,
                    })
                    return
        except sqlite3.OperationalError:
            pass
        time.sleep(0.02)
    outcome["error"] = "timed out before continuation directory result"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-db", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18321")
    parser.add_argument(
        "--scenario",
        choices=(
            "characters",
            "associated",
            "semantic",
            "technique",
            "continuation",
            "continuation-drift",
        ),
        default="characters",
    )
    parser.add_argument(
        "--prepare-semantic-memory",
        action="store_true",
        help="Persist the isolated embedding fixture config; restart before running semantic.",
    )
    args = parser.parse_args()

    if args.prepare_semantic_memory:
        request_json(
            args.base_url,
            "PUT",
            "/api/settings",
            {"data": {"memory_embedding_config": {
                "apiProvider": "openai",
                "model": "purrtypos-acceptance-embedding",
                "apiKey": "isolated-acceptance-only",
                "baseUrl": SEMANTIC_EMBEDDING_BASE_URL,
                "dimensions": 4,
            }}},
        )
        print(json.dumps({
            "semanticMemoryPrepared": True,
            "restartRequired": True,
            "embeddingBaseUrl": SEMANTIC_EMBEDDING_BASE_URL,
        }))
        return

    model = load_model(args.model_db)
    request_json(
        args.base_url,
        "PUT",
        "/api/settings",
        {"data": {"ai_model_configs": [model]}},
    )
    continuation_fixture = None
    if args.scenario in {"continuation", "continuation-drift"}:
        continuation_fixture = create_continuation_fixture(
            args.base_url, args.data_dir
        )
        book = continuation_fixture["book"]
    else:
        book = request_json(
            args.base_url,
            "POST",
            "/api/books",
            {"title": "Writing Replacement 隔离验收书", "enableVolume": False},
        )["data"]
    book_id = str(book["id"])
    associated_chapter_ids: list[str] = []
    associated_outline_ids: list[str] = []
    chapter_id = None
    writing_technique_input_id = None
    embedding_fixture = None
    if args.scenario == "characters":
        for name in ("林澈", "周遥", "顾岚"):
            created = request_json(
                args.base_url,
                "POST",
                f"/api/books/{book_id}/characters",
                {"data": {"name": name, "tags": "隔离验收", "profile_md": "验收用虚构人物。"}},
            )
            if not created.get("success"):
                raise RuntimeError(f"failed to create fixture character {name}")
        session_payload = {"bookId": book_id, "scope": "setting"}
        prompt = "本书共有多少个人物？请使用宿主工具核实，只回答总数和统计口径。"
    elif args.scenario == "associated":
        writing_outline = request_json(
            args.base_url, "GET", f"/api/outlines/writing/{book_id}"
        )["data"]
        chapter = request_json(
            args.base_url,
            "POST",
            f"/api/chapters/{writing_outline['id']}/add",
            {"title": "雾港来信", "isVolume": False},
        )["data"]
        chapter_id = str(chapter["id"])
        chapter_outline = request_json(
            args.base_url,
            "GET",
            f"/api/outlines/for-chapter/{chapter_id}",
        )["data"]
        request_json(
            args.base_url,
            "PUT",
            f"/api/articles/{chapter_id}",
            {"content": lexical_text("关联章节的验收暗号是蓝鲸钟。"), "source": "acceptance_fixture"},
        )
        request_json(
            args.base_url,
            "PUT",
            f"/api/outlines/{chapter_outline['id']}",
            {"markdown_content": "关联大纲的验收暗号是赤色潮汐。"},
        )
        associated_chapter_ids = [chapter_id]
        associated_outline_ids = [str(chapter_outline["id"])]
        session_payload = {"bookId": book_id, "chapterId": chapter_id}
        prompt = (
            "根据我选择的关联章节和关联大纲，分别说出其中的两个验收暗号。"
            "必须调用 readAssociatedWritingContext 核实，不要改写任何内容。"
        )
    elif args.scenario == "semantic":
        embedding_fixture = SemanticEmbeddingFixture()
        embedding_fixture.__enter__()
        try:
            created_memory = request_json(
                args.base_url,
                "POST",
                "/api/memories",
                {
                    "bookId": book_id,
                    "kind": "world",
                    "text": "月蚀港的守门人只认银色航标。",
                    "operationKey": "writing-semantic-live-" + uuid4().hex,
                    "summary": "隔离验收长期记忆",
                    "keywords": "月蚀港,航标",
                    "state": "active",
                },
            )
        except BaseException:
            embedding_fixture.__exit__(None, None, None)
            raise
        if not created_memory.get("success"):
            embedding_fixture.__exit__(None, None, None)
            raise RuntimeError(
                f"failed to create semantic fixture: {created_memory.get('error')}"
            )
        selected_long_term_memory_ids = [str(created_memory["data"]["id"])]
        session_payload = {"bookId": book_id, "scope": "setting"}
        prompt = (
            "我选择的长期记忆中，月蚀港的守门人只认什么？"
            "必须调用 readSelectedWritingContext 核实，只回答记忆中的事实。"
        )
    elif args.scenario == "technique":
        session_payload = {"bookId": book_id, "scope": "setting"}
        prompt = (
            "请从本轮自动候选中选择最适合描写海港天气的写作技法，"
            "并说出该技法正文中的验收暗号。必须先调用 "
            "listWritingTechniqueCandidates，再用候选的完整 ref 调用 "
            "readWritingTechniqueContext 核实。"
        )
    elif args.scenario == "continuation":
        session_payload = {"bookId": book_id, "scope": "setting"}
        prompt = (
            "请先调用 listContinuationSourceSections 查看分叉点以前的来源目录，"
            "确认目录中的章节总数；再从目录中找到标题为“潮门之前”的条目，"
            "必须使用该条目返回的准确 sectionId 调用 "
            "readContinuationSourceSection。最后只回答目录章节总数和该章中的"
            "续写来源暗号，不要读取或猜测分叉点之后的内容。"
        )
    else:
        session_payload = {"bookId": book_id, "scope": "setting"}
        prompt = (
            "请先调用 listContinuationSourceSections 查看续写来源目录，再用目录中"
            "标题为“潮门之前”的准确 sectionId 调用 "
            "readContinuationSourceSection。如果第二个工具返回错误，立即停止，"
            "只报告工具错误 code，不要猜测或复述任何来源正文。"
        )
    session = request_json(
        args.base_url, "POST", "/api/sessions", session_payload
    )["data"]
    if args.scenario == "technique":
        refs = [
            create_published_technique(
                args.base_url,
                name="对白节拍",
                description="用于调整人物对话中的停顿和应答节奏",
                instructions="先压缩对白，再安排停顿。对白技法验收暗号是灰羽。",
            ),
            create_published_technique(
                args.base_url,
                name="潮汐意象",
                description="用于描写海港天气、潮汐与风暴意象",
                instructions="让天气参与叙事。海港天气技法验收暗号是琥珀雨。",
            ),
        ]
        for ref in refs:
            require_data(
                request_json(
                    args.base_url,
                    "POST",
                    f"/api/writing-techniques/books/{book_id}/grants",
                    ref,
                ),
                "grant technique",
            )
        reserved = require_data(
            request_json(
                args.base_url,
                "POST",
                "/api/writing-techniques/request-inputs",
                {
                    "operationId": "writing-technique-input-" + uuid4().hex,
                    "bookId": book_id,
                    "sessionId": str(session["id"]),
                    "mode": "auto",
                    "manual": [],
                },
            ),
            "reserve automatic technique input",
        )
        writing_technique_input_id = reserved["inputId"]

    request_id = "writing-live-" + uuid4().hex
    payload = {
        "streamId": request_id,
        "requestReceiptVersion": 1,
        "messages": [{
            "role": "user",
            "content": prompt,
        }],
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
        "sessionId": int(session["id"]),
        "enableAgentTools": True,
        "bookId": book_id,
        "chapterId": chapter_id,
        "associatedChapterIds": associated_chapter_ids,
        "associatedOutlineIds": associated_outline_ids,
        "selectedLongTermMemoryIds": (
            selected_long_term_memory_ids
            if args.scenario == "semantic"
            else []
        ),
        "writingTechniqueInputId": writing_technique_input_id,
        "chatAgentMode": "agent",
        "planningMode": "reactive",
        "expectedConversationIds": [],
        "expectedRunIds": [],
    }
    drift_outcome = None
    drift_thread = None
    if args.scenario == "continuation-drift":
        drift_outcome = {}
        drift_thread = threading.Thread(
            target=inject_continuation_drift_after_directory,
            args=(args.data_dir, book_id, drift_outcome),
            name="writing-continuation-drift-fixture",
            daemon=True,
        )
        drift_thread.start()
    try:
        result = stream_run(args.base_url, payload)
        if not result.get("runId"):
            raise RuntimeError("Writing stream did not expose a Run id")
        persisted = inspect_run(args.data_dir, result["runId"], args.scenario)
        if drift_thread is not None:
            drift_thread.join(timeout=5)
            if not drift_outcome.get("injected"):
                raise RuntimeError(
                    "continuation drift fixture failed: "
                    f"{drift_outcome.get('error') or 'unknown error'}"
                )
        if embedding_fixture is not None and embedding_fixture.calls < 1:
            raise RuntimeError("semantic fixture memory was not embedded")
        print(json.dumps({
            "bookId": book_id,
            "sessionId": session["id"],
            **result,
            **persisted,
            **({
                "embeddingFixtureCalls": embedding_fixture.calls,
            } if embedding_fixture is not None else {}),
            **({
                "continuationFixture": continuation_fixture,
            } if continuation_fixture is not None else {}),
            **({
                "driftFixture": drift_outcome,
            } if drift_outcome is not None else {}),
        }, ensure_ascii=False))
    finally:
        if embedding_fixture is not None:
            embedding_fixture.__exit__(None, None, None)


if __name__ == "__main__":
    main()
