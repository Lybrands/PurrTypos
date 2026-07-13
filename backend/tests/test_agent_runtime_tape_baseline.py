from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from tests.support.agent_runtime_tape import replay_legacy_runtime_tape


TAPE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "agent_core"
    / "legacy_runtime_tape_v1.json"
)
TAPE = json.loads(TAPE_PATH.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def tape_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


def test_legacy_runtime_tape_inventory_cannot_silently_shrink():
    assert TAPE["schemaVersion"] == 1
    assert [case["id"] for case in TAPE["cases"]] == [
        "direct-answer",
        "read-then-answer",
        "chapter-edit-proposal",
        "destructive-rejection",
        "sequential-tools",
        "disconnect-cancel",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", TAPE["cases"], ids=lambda case: case["id"])
async def test_legacy_runtime_matches_recorded_tape(case, tape_db, monkeypatch):
    actual = await replay_legacy_runtime_tape(case, db=tape_db, monkeypatch=monkeypatch)
    assert actual == case["expected"], (
        f"Tape {case['id']} changed:\n"
        + json.dumps(actual, ensure_ascii=False, indent=2)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", TAPE["cases"], ids=lambda case: f"core-{case['id']}")
async def test_core_runtime_matches_recorded_legacy_tape(case, tape_db, monkeypatch):
    actual = await replay_legacy_runtime_tape(
        case,
        db=tape_db,
        monkeypatch=monkeypatch,
        core_runtime=True,
    )
    assert actual == case["expected"], (
        f"Core Runtime tape {case['id']} changed:\n"
        + json.dumps(actual, ensure_ascii=False, indent=2)
    )
