"""
Book style CRUD — per-book style guide for AI prompt injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


_FIELDS = (
    "pov",
    "tone",
    "pace",
    "banned_rules",
    "reference_chapter_ids",
    "free_notes",
)


async def get_book_style(
    db: DatabaseConnection, book_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM book_style WHERE book_id = ?", [book_id]
    )


async def save_book_style(
    db: DatabaseConnection, book_id: str, data: dict[str, Any]
) -> None:
    """Upsert: any missing field defaults to ''. reference_chapter_ids is a JSON string."""
    payload = {f: str(data.get(f, "") or "") for f in _FIELDS}
    existing = await db.fetch_one(
        "SELECT book_id FROM book_style WHERE book_id = ?", [book_id]
    )
    if existing:
        await db.execute(
            "UPDATE book_style SET pov = ?, tone = ?, pace = ?, banned_rules = ?, "
            "reference_chapter_ids = ?, free_notes = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE book_id = ?",
            [
                payload["pov"],
                payload["tone"],
                payload["pace"],
                payload["banned_rules"],
                payload["reference_chapter_ids"],
                payload["free_notes"],
                book_id,
            ],
        )
    else:
        await db.execute(
            "INSERT INTO book_style "
            "(book_id, pov, tone, pace, banned_rules, reference_chapter_ids, free_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                book_id,
                payload["pov"],
                payload["tone"],
                payload["pace"],
                payload["banned_rules"],
                payload["reference_chapter_ids"],
                payload["free_notes"],
            ],
        )
