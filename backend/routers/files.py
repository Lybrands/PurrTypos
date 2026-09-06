from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import tempfile
import time

from fastapi import APIRouter, Query, Request, Response

from application.project_backup import (
    BACKUP_EXTENSION,
    ProjectBackupError,
    build_project_backup,
    database_stats,
    extract_project_backup,
    merge_local_credentials,
)
from dependencies import get_db

router = APIRouter(tags=["files"])


@router.get("/database/info")
async def get_database_info():
    db = get_db()
    db_path = str(db.get_db_path())

    books = await db.fetch_all("SELECT id FROM books")
    book_count = len(books)

    outline_chapter_count = 0
    outlines = await db.fetch_all("SELECT id FROM outlines")
    for ol in outlines:
        chapters = await db.fetch_all(
            "SELECT id FROM outline_chapters WHERE outline_id = ?", [ol["id"]]
        )
        outline_chapter_count += len(chapters)

    article_count = 0
    for b in books:
        writing = await db.fetch_one(
            "SELECT id FROM outlines WHERE book_id = ? AND type = 'writing' LIMIT 1",
            [b["id"]],
        )
        if not writing:
            continue
        chapters = await db.fetch_all(
            "SELECT id FROM outline_chapters WHERE outline_id = ?", [writing["id"]]
        )
        for ch in chapters:
            article = await db.fetch_one(
                "SELECT content FROM articles WHERE chapter_id = ?", [ch["id"]]
            )
            if article and article.get("content"):
                article_count += 1

    return {
        "success": True,
        "data": {
            "dbPath": db_path,
            "books": book_count,
            "outlineChapters": outline_chapter_count,
            "articles": article_count,
        },
    }


@router.post("/database/export")
async def export_database():
    db = get_db()
    resource = _memory_resource()
    try:
        async with _memory_storage(resource, db.get_db_path().parent) as root:
            database = await db.export_to_buffer()
            if not database:
                return {"success": False, "error": "database_unavailable"}
            buf = build_project_backup(database, root)
    except ProjectBackupError as error:
        return {"success": False, "error": error.code}
    db_path = str(db.get_db_path())
    filename = f"purrtypos-backup{BACKUP_EXTENSION}"
    return Response(
        content=buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-PurrTypos-Db-Path": db_path,
            "X-PurrTypos-Backup-Size": str(len(buf)),
            "X-PurrTypos-Backup-Format": "purrtypos.full-backup/v1",
        },
    )


async def _database_stats() -> dict[str, int]:
    db = get_db()
    books = await db.fetch_one("SELECT COUNT(*) AS count FROM books")
    outline_chapters = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM outline_chapters"
    )
    articles = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM articles WHERE content IS NOT NULL AND content != ''"
    )
    return {
        "books": int((books or {}).get("count") or 0),
        "outlineChapters": int((outline_chapters or {}).get("count") or 0),
        "articles": int((articles or {}).get("count") or 0),
    }


@router.post("/database/import")
async def import_database(
    request: Request,
    fileName: str = Query(default=f"backup{BACKUP_EXTENSION}"),
):
    if not fileName.lower().endswith(BACKUP_EXTENSION):
        return {"success": False, "error": "backup_extension_invalid"}
    payload = await request.body()
    if len(payload) > 512 * 1024 * 1024:
        return {"success": False, "error": "backup_archive_size_exceeded"}
    db = get_db()
    before_stats = await _database_stats()
    local_settings = await _local_credential_settings()
    data_dir = db.get_db_path().parent
    resource = _memory_resource()
    try:
        with tempfile.TemporaryDirectory(
            prefix=".purrtypos-restore-",
            dir=data_dir,
        ) as raw:
            extracted = extract_project_backup(payload, Path(raw) / "contents")
            merge_local_credentials(extracted.database_path, local_settings)
            after_stats = database_stats(extracted.database_path)
            async with _memory_storage(resource, data_dir):
                await _replace_project_data(
                    db,
                    database_path=extracted.database_path,
                    component_path=extracted.component_path,
                    data_dir=data_dir,
                )
        if resource is not None:
            resource.require_restart()
    except ProjectBackupError as error:
        return {"success": False, "error": error.code}
    except (OSError, ValueError, RuntimeError):
        return {"success": False, "error": "backup_restore_failed"}
    return {
        "success": True,
        "data": {
            "beforeStats": before_stats,
            "afterStats": after_stats,
            "restartRequired": True,
        },
    }


def _memory_resource():
    try:
        from application.agent_composition import get_agent_composition

        return get_agent_composition().memory_resource
    except RuntimeError:
        return None


@asynccontextmanager
async def _memory_storage(resource, data_dir: Path):
    if resource is None:
        yield data_dir / "memory-component-v1"
        return
    async with resource.storage_maintenance() as root:
        yield root


async def _local_credential_settings() -> dict:
    db = get_db()
    rows = await db.fetch_all(
        "SELECT key, value FROM settings "
        "WHERE key IN ('ai_model_configs', 'memory_embedding_config')"
    )
    result = {}
    for row in rows:
        try:
            result[str(row["key"])] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue
    return result


async def _replace_project_data(
    db,
    *,
    database_path: Path,
    component_path: Path | None,
    data_dir: Path,
) -> None:
    component_root = data_dir / "memory-component-v1"
    previous_root = data_dir / (
        f"memory-component-v1.before-import-{time.time_ns()}"
    )
    moved_previous = False
    installed_component = False
    try:
        if component_root.exists():
            component_root.replace(previous_root)
            moved_previous = True
        if component_path is not None:
            shutil.copytree(component_path, component_root)
            installed_component = True
        await db.import_from_buffer(database_path.read_bytes())
    except BaseException:
        if installed_component and component_root.exists():
            shutil.rmtree(component_root)
        if moved_previous and previous_root.exists():
            previous_root.replace(component_root)
        raise
