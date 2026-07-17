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


def clear_db(db: "DatabaseConnection | None" = None) -> None:
    """Clear the process-scoped database if it still owns ``db``.

    Passing the expected instance prevents an older lifespan from clearing a
    newer replacement during overlapping test or reload shutdown.
    """

    global _db_instance
    if db is None or _db_instance is db:
        _db_instance = None


def is_db_owner(db: "DatabaseConnection") -> bool:
    """Return whether ``db`` is the active process-scoped database."""

    return _db_instance is db


def get_db() -> "DatabaseConnection":
    if _db_instance is None:
        from exceptions import DatabaseNotReadyError
        raise DatabaseNotReadyError()
    return _db_instance
