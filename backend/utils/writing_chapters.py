"""
Writing chapter helpers — port of electron/writingChaptersForAgent.js.
"""

from __future__ import annotations


def get_writable_chapters_for_agent(writing_chapters: list[dict]) -> list[dict]:
    """Filter out volume/group nodes, keeping only leaf chapters."""
    wc = writing_chapters or []
    if not wc:
        return []
    parent_ids: set[str] = set()
    for c in wc:
        pid = c.get("parent_id")
        if pid is not None and str(pid).strip():
            parent_ids.add(str(pid))
    return [c for c in wc if str(c.get("id", "")) not in parent_ids]
