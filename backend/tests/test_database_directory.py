from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from routers import files
from database.connection import DatabaseConnection


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_host", "server_host", "allowed"),
    [
        ("127.0.0.1", "localhost", True),
        ("::1", "[::1]", True),
        ("192.168.1.10", "localhost", False),
        ("127.0.0.1", "remote.example", False),
    ],
)
async def test_open_directory_only_accepts_local_requests(
    tmp_path, monkeypatch, client_host, server_host, allowed
):
    directory = tmp_path / "写作 数据"
    directory.mkdir()
    monkeypatch.setattr(
        files, "get_db",
        lambda: SimpleNamespace(
            get_connected_db_path=AsyncMock(return_value=directory / "purrtypos.db")
        ),
    )
    opener = Mock()
    monkeypatch.setattr(files, "_open_database_directory", opener)
    app = FastAPI()
    app.include_router(files.router, prefix="/api")
    transport = ASGITransport(app=app, client=(client_host, 12345))
    async with AsyncClient(transport=transport, base_url=f"http://{server_host}") as client:
        response = await client.post(
            "/api/database/open-directory", json={"path": "/untrusted/path"}
        )
    assert response.json()["success"] is allowed
    if allowed:
        opener.assert_called_once_with(directory.resolve())
    else:
        opener.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "unavailable", "timeout"])
async def test_open_directory_returns_actionable_errors(tmp_path, monkeypatch, failure):
    directory = tmp_path / "missing" if failure == "missing" else tmp_path
    monkeypatch.setattr(
        files, "get_db",
        lambda: SimpleNamespace(
            get_connected_db_path=AsyncMock(return_value=directory / "purrtypos.db")
        ),
    )
    error = (
        files.subprocess.TimeoutExpired("open", 10)
        if failure == "timeout" else OSError("unavailable")
    )
    opener = Mock(side_effect=error)
    monkeypatch.setattr(files, "_open_database_directory", opener)
    app = FastAPI()
    app.include_router(files.router, prefix="/api")
    transport = ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.post("/api/database/open-directory")
    assert response.json()["success"] is False
    assert "目录" in response.json()["error"]
    if failure == "missing":
        opener.assert_not_called()


@pytest.mark.parametrize("platform,command", [("darwin", "open"), ("linux", "xdg-open")])
def test_open_directory_passes_path_as_single_argument(tmp_path, monkeypatch, platform, command):
    monkeypatch.setattr(files.sys, "platform", platform)
    run = Mock()
    monkeypatch.setattr(files.subprocess, "run", run)
    directory = tmp_path / "写作 $(example) 数据"
    files._open_database_directory(directory)
    run.assert_called_once_with(
        [command, str(directory)], check=True, capture_output=True, timeout=10
    )


def test_open_directory_uses_windows_file_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(files.sys, "platform", "win32")
    startfile = Mock()
    monkeypatch.setattr(files.os, "startfile", startfile, raising=False)
    files._open_database_directory(tmp_path)
    startfile.assert_called_once_with(str(tmp_path))


@pytest.mark.asyncio
async def test_database_location_uses_active_file_after_import(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = DatabaseConnection(Path("source"))
    target = DatabaseConnection(Path("current"))
    try:
        await source.init()
        await target.init()
        await source.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ["dbPath", r"C:\Users\writer\PurrTypos\purrtypos.db"],
        )
        await target.import_from_buffer(await source.export_to_buffer())
        monkeypatch.setattr(files, "get_db", lambda: target)
        opener = Mock()
        monkeypatch.setattr(files, "_open_database_directory", opener)
        app = FastAPI()
        app.include_router(files.router, prefix="/api")
        transport = ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            info = (await client.get("/api/database/info")).json()
            opened = (await client.post("/api/database/open-directory")).json()
        expected = (tmp_path / "current" / "purrtypos.db").resolve()
        assert info["data"]["dbPath"] == str(expected)
        assert opened["success"] is True
        opener.assert_called_once_with(expected.parent)
    finally:
        await source.close()
        await target.close()


@pytest.mark.asyncio
async def test_connected_path_resolves_directory_symlinks(tmp_path):
    directory = tmp_path / "actual"
    directory.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")
    db = DatabaseConnection(alias)
    try:
        await db.init(initialize_schema=False)
        assert await db.get_connected_db_path() == (directory / "purrtypos.db").resolve()
    finally:
        await db.close()
