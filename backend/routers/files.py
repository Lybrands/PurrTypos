from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import asyncio
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from fastapi import APIRouter, Query, Request, Response

from application.project_backup import (
    BACKUP_EXTENSION,
    BACKUP_FORMAT,
    ProjectBackupError,
    build_project_backup,
    database_stats,
    extract_project_backup,
    merge_local_credentials,
)
from dependencies import get_db

router = APIRouter(tags=["files"])


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _open_database_directory(directory: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(directory))
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run(
            [command, str(directory)],
            check=True,
            capture_output=True,
            timeout=10,
        )


@router.post("/database/open-directory")
async def open_database_directory(request: Request):
    if (
        not request.client
        or not _is_loopback(request.client.host)
        or not _is_loopback(request.url.hostname or "")
    ):
        return {"success": False, "error": "仅支持在后端所在电脑上打开数据库目录"}
    try:
        directory = (await get_db().get_connected_db_path()).parent
        if not directory.is_dir():
            return {"success": False, "error": "数据库目录不存在"}
        await asyncio.to_thread(_open_database_directory, directory)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {"success": False, "error": "无法打开数据库目录，请确认本机文件管理器可用"}
    return {"success": True}


@router.get("/database/info")
async def get_database_info():
    db = get_db()
    db_path = str(await db.get_connected_db_path())

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
            buf = await db.export_with_resources(lambda database: build_project_backup(database, root, db.get_db_path().parent / "writing-library", db.get_db_path().parent / "creation-materials"))
            if not buf:
                return {"success": False, "error": "database_unavailable"}
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
            "X-PurrTypos-Backup-Format": BACKUP_FORMAT,
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
                    technique_path=extracted.technique_path,
                    material_path=extracted.material_path,
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
    technique_path: Path | None = None,
    material_path: Path | None = None,
) -> None:
    from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore
    store = TechniqueFileStore(data_dir / "writing-library")

    @contextmanager
    def install():
        with store.barrier():
            moved = []
            installed = []
            try:
                for name, incoming in [("memory-component-v1", component_path), ("writing-library", technique_path), ("creation-materials", material_path)]:
                    active = data_dir / name
                    previous = data_dir / f"{name}.before-import-{time.time_ns()}"
                    if active.exists():
                        active.replace(previous)
                        moved.append((active, previous))
                    if incoming is not None:
                        incoming.replace(active)
                        installed.append(active)
                yield
            except BaseException:
                for active in reversed(installed):
                    if active.exists():
                        shutil.rmtree(active)
                for active, previous in reversed(moved):
                    previous.replace(active)
                raise

    await db.import_from_buffer(database_path.read_bytes(), resource_install=install)
