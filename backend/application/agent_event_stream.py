"""Cursor replay and committed-output wakeups shared by product transports."""

from __future__ import annotations

import json


async def stream_agent_pages(*, request, read_page, notifications, after=0):
    cursor = after
    first = True
    version = None
    while not await request.is_disconnected():
        # Capture before reading: a commit between the read and wait cannot
        # be lost. The bounded wait also observes cross-process/product writes.
        observed = notifications.revision
        page = await read_page(cursor)
        next_cursor = int(page["nextCursor"])
        if next_cursor < cursor or (page.get("hasMore") and next_cursor == cursor):
            raise ValueError("Agent stream cursor did not advance")
        changed = page.get("projectionVersion") != version
        if first or next_cursor > cursor or changed or page.get("done"):
            yield {"data": json.dumps(page, ensure_ascii=False)}
        first = False
        cursor = next_cursor
        version = page.get("projectionVersion")
        if page.get("done"):
            return
        if not page.get("hasMore"):
            await notifications.wait_for_change(observed)


def projection_version(value) -> str:
    """Content fingerprint, not a second business revision authority."""
    import hashlib
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str,
    ).encode()).hexdigest()
