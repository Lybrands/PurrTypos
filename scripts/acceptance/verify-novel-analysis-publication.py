#!/usr/bin/env python3
"""Publish and verify one isolated replacement Novel Analysis review."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
import urllib.request
from uuid import uuid4


REVIEW_REF_PREFIX = "novel-analysis://"
ATTEMPT_ARTIFACT_NAMESPACE = "purrtypos.novel_analysis.v1"
DEFAULT_EDIT_MARKER = "人工审核确认：药箱用途仍待后续情节揭示。"


def request_json(base_url: str, method: str, path: str, payload=None, *, headers=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            decoded = json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} failed with HTTP {error.code}: {detail}"
        ) from error
    if not decoded.get("success"):
        raise RuntimeError(f"{method} {path} failed: {decoded.get('error')}")
    return decoded.get("data")


def review_payload(artifact: dict) -> dict:
    return {
        "facts": artifact["facts"],
        "craftCards": artifact["craftCards"],
        "storyOverview": artifact.get("storyOverview"),
        "techniqueResult": artifact.get("techniqueResult"),
        "analysisTechniqueResult": artifact.get("analysisTechniqueResult"),
    }


def add_review_marker(artifact: dict, marker: str) -> dict:
    edited = copy.deepcopy(artifact)
    fact = next(
        (item for item in edited.get("facts", []) if isinstance(item.get("value"), str)),
        None,
    )
    if fact is None:
        raise RuntimeError("review Artifact has no editable string fact")
    if marker not in fact["value"]:
        fact["value"] = f"{fact['value']}\n\n{marker}"
    return edited


def validate_publication(
    *,
    base_url: str,
    database_dir: str,
    revision_id: str,
    run_id: str,
    source_artifact_id: str,
    marker: str,
) -> dict:
    published_items = request_json(
        base_url,
        "GET",
        f"/api/novel-source-revisions/{revision_id}/analyses",
    )
    if len(published_items) != 1:
        raise RuntimeError(f"expected one current published analysis: {published_items}")
    published = published_items[0]
    published_again = request_json(
        base_url,
        "GET",
        f"/api/novel-source-analyses/{published['id']}",
    )
    if published_again != published:
        raise RuntimeError("published analysis detail differs from list projection")
    if published.get("schemaVersion") != 4 or published.get("versionNo") != 1:
        raise RuntimeError("replacement publication schema/version changed")
    if not any(marker in str(item.get("value") or "") for item in published["facts"]):
        raise RuntimeError("reviewed edit did not reach formal published facts")
    summary = published.get("summary") or {}
    reviewed_artifact_id = str(summary.get("artifactId") or "")
    if (
        summary.get("sourceArtifactRef") != REVIEW_REF_PREFIX + source_artifact_id
        or not reviewed_artifact_id
        or not summary.get("storyOverview")
        or not summary.get("analysisTechniqueResult")
    ):
        raise RuntimeError("published summary lost replacement lineage or review fields")
    runs = request_json(
        base_url,
        "GET",
        f"/api/novel-source-revisions/{revision_id}/analysis-runs",
    )
    run = next((item for item in runs if item.get("runId") == run_id), None)
    if run is None:
        raise RuntimeError("published source Run is missing from history")
    if (
        run.get("publishedAnalysisId") != published["id"]
        or run.get("artifactRef") != REVIEW_REF_PREFIX + reviewed_artifact_id
        or run.get("workflowStatus") != "completed"
    ):
        raise RuntimeError("Run publication projection is inconsistent")

    database_path = Path(database_dir) / "purrtypos.db"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        source_artifact = connection.execute(
            "SELECT namespace, status FROM ai_agent_artifacts WHERE id = ?",
            [source_artifact_id],
        ).fetchone()
        reviewed_artifact = connection.execute(
            "SELECT namespace, status, created_by_run_id FROM ai_agent_artifacts "
            "WHERE id = ?",
            [reviewed_artifact_id],
        ).fetchone()
        analysis_count = connection.execute(
            "SELECT COUNT(*) FROM novel_source_analyses WHERE source_revision_id = ?",
            [revision_id],
        ).fetchone()[0]
        fact_count = connection.execute(
            "SELECT COUNT(*) FROM novel_source_analysis_facts WHERE analysis_id = ?",
            [published["id"]],
        ).fetchone()[0]
        card_count = connection.execute(
            "SELECT COUNT(*) FROM novel_source_craft_cards WHERE analysis_id = ?",
            [published["id"]],
        ).fetchone()[0]
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM novel_source_analysis_evidence WHERE analysis_id = ?",
            [published["id"]],
        ).fetchone()[0]
    if source_artifact is None or tuple(source_artifact) != (
        ATTEMPT_ARTIFACT_NAMESPACE,
        "finalized",
    ):
        raise RuntimeError("source attempt Artifact was mutated or retired")
    if reviewed_artifact is None or tuple(reviewed_artifact) != (
        "purrtypos.novel_analysis.review.v1",
        "finalized",
        run_id,
    ):
        raise RuntimeError("reviewed Artifact ownership is inconsistent")
    if analysis_count != 1 or fact_count != len(published["facts"]):
        raise RuntimeError("formal analysis or fact projection count is inconsistent")
    if card_count != len(published["craftCards"]) or evidence_count < fact_count + card_count:
        raise RuntimeError("craft/evidence projection count is inconsistent")
    return {
        "analysisId": published["id"],
        "reviewedArtifactId": reviewed_artifact_id,
        "versionNo": published["versionNo"],
        "factCount": fact_count,
        "craftCardCount": card_count,
        "evidenceCount": evidence_count,
        "runProjection": {
            "runId": run_id,
            "artifactRef": run["artifactRef"],
            "publishedAnalysisId": run["publishedAnalysisId"],
            "workflowStatus": run["workflowStatus"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--revision-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-artifact-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18321")
    parser.add_argument("--edit-marker", default=DEFAULT_EDIT_MARKER)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if not args.verify_only:
        source = request_json(
            args.base_url,
            "GET",
            f"/api/novel-analysis-artifacts/{args.source_artifact_id}",
        )
        edited = add_review_marker(source, args.edit_marker)
        reviewed = request_json(
            args.base_url,
            "POST",
            f"/api/novel-analysis-artifacts/{args.source_artifact_id}/review",
            review_payload(edited),
            headers={"Idempotency-Key": "novel-analysis-review-live-" + uuid4().hex},
        )
        if reviewed.get("reviewStatus") != "reviewed":
            raise RuntimeError("reviewed Artifact did not reach reviewed state")
        reloaded_source = request_json(
            args.base_url,
            "GET",
            f"/api/novel-analysis-artifacts/{args.source_artifact_id}",
        )
        if any(
            args.edit_marker in str(item.get("value") or "")
            for item in reloaded_source.get("facts", [])
        ):
            raise RuntimeError("review mutated the source attempt Artifact")
        first_publish = request_json(
            args.base_url,
            "POST",
            f"/api/novel-analysis-artifacts/{reviewed['artifactId']}/publish",
            {},
        )
        replayed_publish = request_json(
            args.base_url,
            "POST",
            f"/api/novel-analysis-artifacts/{reviewed['artifactId']}/publish",
            {},
        )
        if replayed_publish.get("id") != first_publish.get("id"):
            raise RuntimeError("publication replay created a second analysis")

    result = validate_publication(
        base_url=args.base_url,
        database_dir=args.data_dir,
        revision_id=args.revision_id,
        run_id=args.run_id,
        source_artifact_id=args.source_artifact_id,
        marker=args.edit_marker,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
