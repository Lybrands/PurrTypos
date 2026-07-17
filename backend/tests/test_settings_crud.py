from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud.settings import get_settings, set_settings

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def test_get_settings_returns_documented_defaults(temp_db: DatabaseConnection):
    settings = await get_settings(temp_db)

    assert settings == {
        "sync_outline_chapter": False,
        "ai_model_configs": [],
    }


async def test_set_settings_round_trips_supported_value_types(temp_db: DatabaseConnection):
    configs = [{"provider": "openai", "model": "test-model"}]
    await set_settings(temp_db, {
        "sync_outline_chapter": True,
        "ai_model_configs": configs,
        "ignored": "not persisted",
    })

    settings = await get_settings(temp_db)
    assert settings["sync_outline_chapter"] is True
    assert settings["ai_model_configs"] == configs


async def test_get_settings_normalizes_invalid_stored_values(temp_db: DatabaseConnection):
    await temp_db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["ai_model_configs", "not-json"],
    )
    settings = await get_settings(temp_db)
    assert settings["ai_model_configs"] == []
