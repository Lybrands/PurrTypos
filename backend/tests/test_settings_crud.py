from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud.settings import DEFAULT_SYSTEM_PROMPT, get_settings, set_settings

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
        "ai_system_prompt": DEFAULT_SYSTEM_PROMPT,
        "ai_model_configs": [],
        "ai_agent_mode": "legacy",
    }
    assert "“先分析、后执行、可追溯”" in DEFAULT_SYSTEM_PROMPT


async def test_set_settings_round_trips_supported_value_types(temp_db: DatabaseConnection):
    configs = [{"provider": "openai", "model": "test-model"}]
    await set_settings(temp_db, {
        "sync_outline_chapter": True,
        "ai_system_prompt": "custom prompt",
        "ai_model_configs": configs,
        "ai_agent_mode": "subagent",
        "ignored": "not persisted",
    })

    settings = await get_settings(temp_db)
    assert settings["sync_outline_chapter"] is True
    assert settings["ai_system_prompt"] == "custom prompt"
    assert settings["ai_model_configs"] == configs
    assert settings["ai_agent_mode"] == "subagent"


async def test_get_settings_normalizes_invalid_stored_values(temp_db: DatabaseConnection):
    await temp_db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["ai_model_configs", "not-json"],
    )
    await temp_db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["ai_agent_mode", "unsupported"],
    )

    settings = await get_settings(temp_db)
    assert settings["ai_model_configs"] == []
    assert settings["ai_agent_mode"] == "legacy"
