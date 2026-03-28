"""
FastAPI dependency injection — replaces the duplicated getDb() pattern
found in main.js:19-21 and toolExecutor.js:15-17.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

_db_instance: "DatabaseConnection | None" = None


def set_db(db: "DatabaseConnection") -> None:
    global _db_instance
    _db_instance = db


def get_db() -> "DatabaseConnection":
    if _db_instance is None:
        from exceptions import DatabaseNotReadyError
        raise DatabaseNotReadyError()
    return _db_instance
