"""Read-only novel knowledge admission and source contracts."""
from __future__ import annotations

import hashlib
import json


class KnowledgeError(RuntimeError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def digest(value) -> str:
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(value).hexdigest()


def admission(meta: dict, scope: dict, chapters: list[str]) -> str | None:
    """Return an exclusion reason; chapter endpoints are stable IDs, end exclusive."""
    if meta.get('status') != 'confirmed':
        return 'not_confirmed'
    if meta.get('temporal_scope') not in ('global', 'chapter_range'):
        return 'temporal_scope_missing'
    current = scope.get('chapterId')
    if current and current not in chapters:
        return 'chapter_missing'
    start, end = meta.get('valid_from_chapter'), meta.get('valid_to_chapter')
    if meta.get('temporal_scope') == 'chapter_range':
        if start not in chapters or (end and end not in chapters):
            return 'chapter_range_invalid'
        if end and chapters.index(start) >= chapters.index(end):
            return 'chapter_range_invalid'
        if scope.get('purpose') != 'discussion':
            if not current:
                return 'chapter_required'
            if chapters.index(current) < chapters.index(start):
                return 'future'
            if end and chapters.index(current) >= chapters.index(end):
                return 'expired'
    if scope.get('purpose') != 'discussion':
        # With no POV selected, only explicitly public information is safe.
        known = meta.get('known_to', [])
        if '*' not in known and scope.get('characterId') not in known:
            return 'character_knowledge_unknown'
        knowledge_start = meta.get('knowledge_from_chapter')
        if knowledge_start:
            if knowledge_start not in chapters or current not in chapters:
                return 'knowledge_chapter_invalid'
            if chapters.index(current) < chapters.index(knowledge_start):
                return 'not_yet_known'
    return None
