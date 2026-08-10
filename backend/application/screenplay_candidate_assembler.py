"""Deterministically assemble validated screenplay Parts for publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from application.screenplay_agent_context import ScreenplayAgentContextQuery
from domains.screenplay_agent.contracts import ReviewEpisodeResult
from domains.screenplay_agent.operation import ScreenplayOperationRecord
from purra.json_values import thaw_json_mapping


_PROPOSAL_KIND = {
    "sourceAnalysis": "source_analysis",
    "creativeBrief": "creative_brief",
    "structure": "episode_outline",
    "sceneList": "scene_list",
    "screenplayDraft": "scene_draft",
    "review": "review",
}


@dataclass(frozen=True, slots=True)
class ScreenplayCandidatePublication:
    proposal_kind: str
    title: str
    content_json: Mapping[str, Any]
    content_text: str
    base_revision_id: str | None
    finalizing_run_id: str | None
    source_run_ids: tuple[str, ...]


class ScreenplayCandidateAssembler:
    def __init__(self, db) -> None:
        self._context = ScreenplayAgentContextQuery(db)

    async def assemble(
        self,
        operation: ScreenplayOperationRecord,
        candidate_parts: Sequence[Mapping[str, Any]],
    ) -> ScreenplayCandidatePublication:
        parts = tuple(dict(value) for value in candidate_parts)
        if not parts:
            raise ValueError("screenplay finalization requires candidate Parts")
        role = operation.target_role
        requirements = thaw_json_mapping(operation.requirements_json)
        base_revision_id = (
            str(requirements.get("baseRevisionId") or "").strip() or None
        )
        if role == "screenplayDraft":
            title, content, text = await self._draft_candidate(
                operation.project_id,
                parts,
                base_revision_id=base_revision_id,
            )
        elif role == "review":
            required = tuple(sorted(
                int((part.get("contentJson") or {}).get("reviewedEpisode") or 0)
                for part in parts
            ))
            title, content, text = aggregate_review_validations(
                parts,
                required_episode_numbers=required,
            )
        else:
            if len(parts) != 1:
                raise ValueError("document finalization requires one validation Part")
            title = str(parts[0].get("title") or "").strip()
            content = dict(parts[0].get("contentJson") or {})
            text = str(parts[0].get("contentText") or "").strip()
            if not title or not content or not text:
                raise ValueError("document validation Part is incomplete")
        return ScreenplayCandidatePublication(
            proposal_kind=_PROPOSAL_KIND[role],
            title=title,
            content_json=content,
            content_text=text,
            base_revision_id=base_revision_id,
            finalizing_run_id=str(parts[-1].get("runId") or "").strip() or None,
            source_run_ids=_source_run_ids(parts),
        )

    async def _draft_candidate(
        self,
        project_id: str,
        generated: Sequence[Mapping[str, Any]],
        *,
        base_revision_id: str | None,
    ) -> tuple[str, dict[str, Any], str]:
        drafts = [dict(output["episodeDraft"]) for output in generated]
        scene_list_ids = {
            str(output.get("sceneListId") or "") for output in generated
        }
        if len(scene_list_ids) != 1 or "" in scene_list_ids:
            raise RuntimeError("generated episodes do not share one scene list")
        available = await self._context.available_episode_numbers(
            project_id,
            draft_revision_id=base_revision_id,
        )
        complete_numbers = set(available["draft"]).union(
            int(draft["episodeNumber"]) for draft in drafts
        )
        content = {
            "schemaVersion": 1,
            "documentKind": "scene_draft",
            "sceneListId": next(iter(scene_list_ids)),
            "completedSceneIds": [
                scene_id for draft in drafts for scene_id in draft["sceneIds"]
            ],
            "isComplete": set(available["sceneList"]).issubset(complete_numbers),
            "episodeDrafts": drafts,
        }
        numbers = [int(draft["episodeNumber"]) for draft in drafts]
        title = (
            f"第 {min(numbers)}–{max(numbers)} 集剧本"
            if len(numbers) > 1
            else f"第 {numbers[0]} 集剧本"
        )
        return title, content, "\n\n".join(
            str(draft["contentText"]) for draft in drafts
        )


def _source_run_ids(values: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        run_id
        for value in values
        for run_id in (
            str(value.get("runId") or "").strip(),
            *(
                str(item or "").strip()
                for item in value.get("sourceRunIds") or ()
            ),
        )
        if run_id
    ))


def aggregate_review_validations(
    generated: Sequence[Mapping[str, Any]],
    *,
    required_episode_numbers: Sequence[int],
) -> tuple[str, dict[str, Any], str]:
    ordered = sorted(
        (dict(item) for item in generated),
        key=lambda item: int(
            (item.get("contentJson") or {}).get("reviewedEpisode") or 0
        ),
    )
    results = []
    for item in ordered:
        raw = item.get("contentJson")
        if not isinstance(raw, Mapping):
            raise ValueError("review aggregation requires ReviewEpisodeResult")
        receipt = item.get("validationReceipt")
        receipts = tuple(str(value) for value in raw.get("partReceipts") or ())
        if not receipts and isinstance(receipt, Mapping):
            receipts = (str(receipt.get("contentDigest") or ""),)
        results.append(ReviewEpisodeResult.from_mapping(
            raw,
            part_receipts=receipts,
        ))
    reviewed = [item.episode_number for item in results]
    required = [int(value) for value in required_episode_numbers]
    if reviewed != required:
        raise ValueError(
            "review aggregation requires all required episode validations"
        )
    draft_ids = {item.reviewed_revision_id for item in results}
    if not reviewed or len(draft_ids) != 1 or "" in draft_ids:
        raise ValueError(
            "review aggregation requires one immutable Draft Revision"
        )
    episode_reviews = [item.to_mapping() for item in results]
    issues = [dict(issue) for item in results for issue in item.issues]
    content = {
        "schemaVersion": 1,
        "inputContractVersion": 2,
        "documentKind": "review",
        "verdict": _aggregate_review_verdict(item.verdict for item in results),
        "issues": issues,
        "issueCount": len(issues),
        "criticalIssueCount": sum(
            str(issue.get("severity") or "") == "critical" for issue in issues
        ),
        "reviewedDraftId": next(iter(draft_ids)),
        "reviewedEpisodes": reviewed,
        "completedEpisodes": reviewed,
        "episodeReviews": episode_reviews,
    }
    return (
        "剧本审阅报告",
        content,
        "\n\n".join(str(item["contentText"]) for item in ordered),
    )


def _aggregate_review_verdict(values) -> str:
    ranks = {"ready": 0, "revise": 1, "major_rework": 2}
    normalized = tuple(str(value) for value in values)
    if not normalized or any(value not in ranks for value in normalized):
        raise ValueError("review fragment verdict is invalid")
    return max(normalized, key=ranks.__getitem__)


__all__ = [
    "ScreenplayCandidateAssembler",
    "ScreenplayCandidatePublication",
    "aggregate_review_validations",
]
