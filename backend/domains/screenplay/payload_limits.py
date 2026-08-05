"""Versioned semantic payload limits for screenplay model tools.

These values describe useful domain content, not provider context allocation or
raw JSON transport capacity. Keeping them in one immutable policy prevents the
model schema, host handler, and acceptance validators from drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SceneDraftPayloadLimits:
    scene_text_chars: int = 24_000
    execution_result_chars: int = 2_000
    unresolved_notes: int = 10
    unresolved_note_chars: int = 500
    draft_notes_chars: int = 4_000


SCENE_DRAFT_PAYLOAD_LIMITS = SceneDraftPayloadLimits()
