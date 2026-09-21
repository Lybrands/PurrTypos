"""Persist bounded, content-free Novel Analysis reliability baselines."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from infrastructure.persistence.stability_query import (
    get_novel_analysis_reliability_snapshot,
)


logger = logging.getLogger(__name__)

# This is an analytics retention cadence, not a request timeout or retry delay:
# at most four immutable global samples are written per UTC calendar day.
_BASELINE_BUCKET_MS = 6 * 60 * 60 * 1_000
_BASELINE_WINDOW_LIMIT = 100


class NovelAnalysisReliabilityBaselineService:
    """Capture one immutable global snapshot per bounded time bucket."""

    def __init__(self, db) -> None:
        self._db = db

    async def capture_due(self, *, timestamp_ms: int | None = None) -> bool:
        now_ms = int(time.time() * 1_000) if timestamp_ms is None else int(
            timestamp_ms
        )
        bucket_started_at_ms = now_ms - (now_ms % _BASELINE_BUCKET_MS)
        existing = await self._db.fetch_one(
            "SELECT 1 AS present FROM ai_novel_analysis_reliability_snapshots "
            "WHERE bucket_started_at_ms = ? AND window_limit = ?",
            [bucket_started_at_ms, _BASELINE_WINDOW_LIMIT],
        )
        if existing is not None:
            return False
        metrics = await get_novel_analysis_reliability_snapshot(
            self._db,
            limit=_BASELINE_WINDOW_LIMIT,
        )
        if int(metrics["sample"]["taskCount"]) == 0:
            return False
        async with self._db.transaction():
            await self._db.execute(
                "INSERT OR IGNORE INTO ai_novel_analysis_reliability_snapshots "
                "(bucket_started_at_ms, window_limit, metrics_json) VALUES (?, ?, ?)",
                [
                    bucket_started_at_ms,
                    _BASELINE_WINDOW_LIMIT,
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                ],
            )
        return True


async def monitor_novel_analysis_reliability_baseline(
    baseline: NovelAnalysisReliabilityBaselineService,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Capture buckets opportunistically without participating in execution."""

    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            captured = await baseline.capture_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to capture Novel Analysis reliability baseline")
        else:
            if captured:
                logger.info("Captured Novel Analysis reliability baseline")
        now_ms = int(time.time() * 1_000)
        next_bucket_ms = (
            (now_ms // _BASELINE_BUCKET_MS) + 1
        ) * _BASELINE_BUCKET_MS
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=max(0.001, (next_bucket_ms - now_ms) / 1_000),
            )
        except TimeoutError:
            pass


__all__ = [
    "NovelAnalysisReliabilityBaselineService",
    "monitor_novel_analysis_reliability_baseline",
]
