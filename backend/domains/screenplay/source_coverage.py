"""Deterministic long-form coverage planning for screenplay adaptations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from domains.screenplay.source_scope import (
    scoped_chapters,
    source_scope_summary,
)
from utils.book_structure import load_chapter_texts


MAX_COVERAGE_BATCHES = 6
TARGET_CHAPTERS_PER_BATCH = 20
TARGET_SOURCE_CHARACTERS_PER_BATCH = 60_000
RETURNED_CHARACTERS_PER_BATCH = 14_000


async def build_source_coverage_plan(
    db,
    project: Mapping[str, Any],
) -> dict[str, Any]:
    chapters = await scoped_chapters(db, project)
    book_id = str(project.get("source_book_id") or "")
    book = await db.fetch_one(
        "SELECT id, title FROM books WHERE id = ?",
        [book_id],
    )
    metadata = await _article_metadata(
        db,
        [str(chapter["id"]) for chapter in chapters],
    )
    planned = [{
        "id": str(chapter["id"]),
        "title": str(chapter.get("title") or ""),
        "index": int(chapter.get("index") or index),
        "volumeId": str(chapter.get("volume_id") or "") or None,
        "volumeTitle": str(chapter.get("volume_title") or "") or None,
        "sourceCharacters": int(
            metadata.get(str(chapter["id"]), {}).get("characters") or 0
        ),
        "sourceUpdatedAt": str(
            metadata.get(str(chapter["id"]), {}).get("updatedAt") or ""
        ),
    } for index, chapter in enumerate(chapters, start=1)]
    batches = _partition_chapters(planned)
    signature = {
        "projectId": str(project.get("id") or ""),
        "sourceScope": project.get("source_scope"),
        "chapters": [
            [
                chapter["id"],
                chapter["sourceCharacters"],
                chapter["sourceUpdatedAt"],
            ]
            for chapter in planned
        ],
        "batches": [
            [chapter["id"] for chapter in batch]
            for batch in batches
        ],
    }
    plan_id = hashlib.sha256(json.dumps(
        signature,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()[:24]
    return {
        "schemaVersion": 1,
        "planId": plan_id,
        "book": {
            "id": book_id,
            "title": str((book or {}).get("title") or ""),
        },
        "sourceScope": source_scope_summary(project.get("source_scope")),
        "selectedChapterCount": len(planned),
        "estimatedSourceCharacters": sum(
            chapter["sourceCharacters"] for chapter in planned
        ),
        "batchCount": len(batches),
        "maxParallelBatchCalls": MAX_COVERAGE_BATCHES,
        "batches": [{
            "batchNumber": index,
            "chapterCount": len(batch),
            "chapterIds": [chapter["id"] for chapter in batch],
            "firstChapter": _chapter_label(batch[0]) if batch else None,
            "lastChapter": _chapter_label(batch[-1]) if batch else None,
            "estimatedSourceCharacters": sum(
                chapter["sourceCharacters"] for chapter in batch
            ),
        } for index, batch in enumerate(batches, start=1)],
    }


async def read_source_coverage_batch(
    db,
    project: Mapping[str, Any],
    *,
    plan_id: str,
    batch_number: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    plan = await build_source_coverage_plan(db, project)
    if str(plan_id or "").strip() != plan["planId"]:
        raise ValueError(
            "Coverage plan is stale or does not belong to this project. "
            "Call getSourceCoveragePlan again."
        )
    batch = next(
        (
            item for item in plan["batches"]
            if item["batchNumber"] == int(batch_number)
        ),
        None,
    )
    if batch is None:
        raise ValueError("batchNumber does not belong to the coverage plan.")
    chapter_ids = [str(item) for item in batch["chapterIds"]]
    scoped = {
        str(chapter["id"]): chapter
        for chapter in await scoped_chapters(db, project)
    }
    texts = await load_chapter_texts(db, chapter_ids)
    per_chapter_budget = max(
        80,
        min(
            8_000,
            RETURNED_CHARACTERS_PER_BATCH // max(1, len(chapter_ids)),
        ),
    )
    chapter_payloads: list[dict[str, Any]] = []
    refs: list[dict[str, str]] = []
    for chapter_id in chapter_ids:
        chapter = scoped.get(chapter_id, {})
        full_text = str(texts.get(chapter_id) or "")
        sample, fully_read = _fit_chapter_text(
            full_text,
            per_chapter_budget,
        )
        chapter_payloads.append({
            "sourceType": "chapter",
            "sourceId": chapter_id,
            "index": int(chapter.get("index") or 0),
            "title": str(chapter.get("title") or "")[:120],
            "volumeTitle": (
                str(chapter.get("volume_title") or "")[:80] or None
            ),
            "coverage": "full" if fully_read else "sampled",
            "sourceCharacters": len(full_text),
            "returnedCharacters": len(sample),
            "text": sample,
        })
        refs.append({
            "sourceType": "chapter",
            "sourceId": chapter_id,
            "revisionText": full_text,
            "excerpt": sample,
        })
    fully_read_count = sum(
        item["coverage"] == "full" for item in chapter_payloads
    )
    return {
        "planId": plan["planId"],
        "batchNumber": int(batch_number),
        "batchCount": plan["batchCount"],
        "chapterCount": len(chapter_payloads),
        "fullyReadChapterCount": fully_read_count,
        "sampledChapterCount": len(chapter_payloads) - fully_read_count,
        "chapters": chapter_payloads,
    }, refs


async def _article_metadata(
    db,
    chapter_ids: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(chapter_ids), 500):
        batch = chapter_ids[offset:offset + 500]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        rows = await db.fetch_all(
            "SELECT chapter_id, LENGTH(COALESCE(content, '')) AS characters, "
            "update_time FROM articles "
            f"WHERE chapter_id IN ({placeholders})",
            batch,
        )
        for row in rows:
            result[str(row["chapter_id"])] = {
                "characters": int(row.get("characters") or 0),
                "updatedAt": str(row.get("update_time") or ""),
            }
    return result


def _partition_chapters(
    chapters: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    if not chapters:
        return []
    total_characters = sum(
        max(1, int(chapter.get("sourceCharacters") or 0))
        for chapter in chapters
    )
    batch_count = min(
        MAX_COVERAGE_BATCHES,
        len(chapters),
        max(
            1,
            math.ceil(len(chapters) / TARGET_CHAPTERS_PER_BATCH),
            math.ceil(
                total_characters / TARGET_SOURCE_CHARACTERS_PER_BATCH
            ),
        ),
    )
    batches: list[list[dict[str, Any]]] = []
    cursor = 0
    for batch_index in range(batch_count):
        remaining = chapters[cursor:]
        batches_left = batch_count - batch_index
        if batches_left == 1:
            batches.append(remaining)
            break
        remaining_weight = sum(
            max(1, int(chapter.get("sourceCharacters") or 0))
            for chapter in remaining
        )
        target_weight = remaining_weight / batches_left
        maximum_take = len(remaining) - (batches_left - 1)
        take = 0
        current_weight = 0
        while take < maximum_take:
            current_weight += max(
                1,
                int(remaining[take].get("sourceCharacters") or 0),
            )
            take += 1
            if current_weight >= target_weight:
                break
        batches.append(remaining[:take])
        cursor += take
    return batches


def _fit_chapter_text(text: str, budget: int) -> tuple[str, bool]:
    if len(text) <= budget:
        return text, True
    marker = "\n…（本章中段已按长篇覆盖预算省略）…\n"
    if budget <= len(marker) + 40:
        return text[:budget], False
    available = budget - len(marker)
    head = max(1, round(available * 0.62))
    tail = max(1, available - head)
    return f"{text[:head]}{marker}{text[-tail:]}", False


def _chapter_label(chapter: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": chapter["id"],
        "index": chapter["index"],
        "title": chapter["title"],
        "volumeTitle": chapter.get("volumeTitle"),
    }
