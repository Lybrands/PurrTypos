from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from starlette.requests import Request

from application.agent_composition import (
    clear_agent_composition,
    set_agent_composition,
)
from application.project_backup import (
    ProjectBackupError,
    build_project_backup,
    extract_project_backup,
    merge_local_credentials,
)
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from routers.files import import_database


def _component(root: Path, *, marker: bytes = b"memory") -> Path:
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"format": 1, "embeddingDimensions": 4}),
        encoding="utf-8",
    )
    for name in ("history.sqlite3", "journal.sqlite3"):
        connection = sqlite3.connect(root / name)
        try:
            connection.execute("CREATE TABLE marker (value BLOB NOT NULL)")
            connection.execute("INSERT INTO marker (value) VALUES (?)", (marker,))
            connection.commit()
        finally:
            connection.close()
    vectors = root / "vectors"
    vectors.mkdir()
    (vectors / "storage.bin").write_bytes(marker)
    return root


async def _database(directory: Path, *, book_id: str, api_key: str):
    db = DatabaseConnection(directory)
    await db.init()
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        [book_id, book_id],
    )
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [
            "ai_model_configs",
            json.dumps([{"id": "model-1", "name": "chat", "apiKey": api_key}]),
        ],
    )
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [
            "memory_embedding_config",
            json.dumps({
                "apiProvider": "openai",
                "model": "embed",
                "apiKey": api_key,
                "baseUrl": "https://provider.test/v1",
                "dimensions": 4,
            }),
        ],
    )
    return db


def _setting(path: Path, key: str):
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return json.loads(row[0])
    finally:
        connection.close()


def _component_marker(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM marker").fetchone()[0]
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_complete_backup_round_trip_redacts_and_locally_rebinds_credentials(
    tmp_path,
):
    db = await _database(tmp_path / "source", book_id="book-new", api_key="secret")
    try:
        archive = build_project_backup(
            await db.export_to_buffer(),
            _component(tmp_path / "source" / "memory-component-v1"),
        )
    finally:
        await db.close()

    extracted = extract_project_backup(archive, tmp_path / "extracted")
    assert extracted.component_path is not None
    assert (extracted.component_path / "vectors" / "storage.bin").read_bytes() == b"memory"
    assert _setting(extracted.database_path, "ai_model_configs")[0]["apiKey"] == ""
    assert _setting(extracted.database_path, "memory_embedding_config")["apiKey"] == ""

    merge_local_credentials(extracted.database_path, {
        "ai_model_configs": [{"id": "model-1", "apiKey": "device-key"}],
        "memory_embedding_config": {
            "apiProvider": "openai",
            "model": "embed",
            "apiKey": "device-embedding-key",
            "baseUrl": "https://provider.test/v1",
            "dimensions": 4,
        },
    })
    assert _setting(extracted.database_path, "ai_model_configs")[0]["apiKey"] == "device-key"
    assert _setting(extracted.database_path, "memory_embedding_config")["apiKey"] == "device-embedding-key"


def test_backup_rejects_archive_path_traversal(tmp_path):
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("../purrtypos.db", b"malicious")

    with pytest.raises(ProjectBackupError) as caught:
        extract_project_backup(payload.getvalue(), tmp_path / "unsafe")

    assert caught.value.code == "backup_path_invalid"


@pytest.mark.asyncio
async def test_backup_accepts_component_before_its_first_journaled_operation(
    tmp_path,
):
    db = await _database(tmp_path / "fresh-db", book_id="book", api_key="secret")
    component = _component(tmp_path / "fresh-db" / "memory-component-v1")
    (component / "journal.sqlite3").unlink()
    try:
        archive = build_project_backup(await db.export_to_buffer(), component)
    finally:
        await db.close()

    extracted = extract_project_backup(archive, tmp_path / "fresh-extracted")
    assert extracted.component_path is not None
    assert not (extracted.component_path / "journal.sqlite3").exists()
    assert _component_marker(
        extracted.component_path / "history.sqlite3"
    ) == b"memory"


@pytest.mark.asyncio
async def test_backup_rejects_corrupt_component_history(tmp_path):
    db = await _database(tmp_path / "corrupt-db", book_id="book", api_key="secret")
    component = _component(tmp_path / "corrupt-db" / "memory-component-v1")
    (component / "history.sqlite3").write_bytes(b"not-sqlite")
    try:
        with pytest.raises(ProjectBackupError) as caught:
            build_project_backup(await db.export_to_buffer(), component)
    finally:
        await db.close()

    assert caught.value.code == "backup_component_integrity_failed"


@pytest.mark.asyncio
async def test_import_route_replaces_database_and_component_as_one_backup(
    tmp_path,
):
    source = await _database(
        tmp_path / "source-route",
        book_id="book-restored",
        api_key="backup-secret",
    )
    try:
        archive = build_project_backup(
            await source.export_to_buffer(),
            _component(
                tmp_path / "source-route" / "memory-component-v1",
                marker=b"restored-memory",
            ),
        )
    finally:
        await source.close()

    target_dir = tmp_path / "target-route"
    target = await _database(
        target_dir,
        book_id="book-old",
        api_key="device-secret",
    )
    _component(target_dir / "memory-component-v1", marker=b"old-memory")
    composition = SimpleNamespace(memory_resource=None)
    set_db(target)
    set_agent_composition(composition)

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": archive, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/database/import",
            "headers": [],
        },
        receive,
    )
    try:
        result = await import_database(request, fileName="project.purrbackup")
        assert result["success"] is True
        assert result["data"]["restartRequired"] is True
        assert await target.fetch_all("SELECT id FROM books ORDER BY id") == [
            {"id": "book-restored"}
        ]
        assert _component_marker(
            target_dir / "memory-component-v1" / "history.sqlite3"
        ) == b"restored-memory"
        settings = await target.fetch_one(
            "SELECT value FROM settings WHERE key = 'memory_embedding_config'"
        )
        assert json.loads(settings["value"])["apiKey"] == "device-secret"
    finally:
        clear_agent_composition(composition)
        clear_db(target)
        await target.close()
