"""Process-owned recovery loop for expired Agent Run execution leases."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from infrastructure.persistence.run_execution_store import recover_orphaned_runs


logger = logging.getLogger(__name__)


async def monitor_orphaned_runs(
    db,
    *,
    poll_interval_seconds: float = 5.0,
    stop_event: asyncio.Event | None = None,
    reconcile_linked_state: (
        Callable[[], Awaitable[Sequence[str]]] | None
    ) = None,
    reconcile_terminal_holes: (
        Callable[[], Awaitable[Sequence[str]]] | None
    ) = None,
) -> None:
    """Terminalize expired Runs and reconcile their durable owner state."""

    interval = float(poll_interval_seconds)
    if interval <= 0:
        raise ValueError("orphan monitor interval must be positive")
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
        if stop.is_set():
            return
        try:
            recovered = await recover_orphaned_runs(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to inspect orphaned Agent Runs")
            continue
        if recovered:
            logger.warning(
                "Terminalized %s orphaned Agent Run(s): %s",
                len(recovered),
                ", ".join(recovered),
            )
        if reconcile_terminal_holes is not None:
            try:
                projected = tuple(await reconcile_terminal_holes())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to materialize terminal Writing Runs")
            else:
                if projected:
                    logger.warning(
                        "Materialized %s terminal Writing Run(s): %s",
                        len(projected),
                        ", ".join(projected),
                    )
        if reconcile_linked_state is None:
            continue
        try:
            reconciled = tuple(await reconcile_linked_state())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to reconcile terminal Agent Run owners")
            continue
        if reconciled:
            logger.warning(
                "Reconciled %s terminal Agent Run owner(s): %s",
                len(reconciled),
                ", ".join(reconciled),
            )
