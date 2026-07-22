from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from domains.writing.story_memory import SourceReference
from domains.writing.story_memory_ledger import StoryMemoryLedger
from domains.writing.story_settings import (
    CharacterState,
    PlotThread,
    PlotThreadState,
    RelationshipState,
    StorySettingChange,
    TimelineEvent,
    WorldFact,
)
from infrastructure.persistence.writing.sqlite_story_memory_repository import (
    SqliteStoryMemoryRepository,
)
from routers.story_memory import (
    apply_story_memory_delta,
    get_story_memory,
    stage_story_memory_delta,
)
from schemas.story_memory import StageStoryMemoryDeltaRequest

@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    set_db(connection)
    try:
        yield connection
    finally:
        clear_db(connection)
        await connection.close()


def test_typed_settings_have_stable_business_keys():
    settings = (
        CharacterState("lin mo", "current location", "旧城区"),
        RelationshipState("lin", "su", "trust", "fragile"),
        WorldFact("moon-law", "月光会令魔法失效"),
        TimelineEvent("arrival", "抵达", "林墨进入旧城区"),
        PlotThread("missing-sister", "失踪的妹妹", "林墨寻找妹妹"),
    )

    assert [item.target_key for item in settings] == [
        "character:lin%20mo:state:current%20location",
        "relationship:directed:lin:su:trust",
        "world_fact:moon-law",
        "timeline_event:arrival",
        "plot_thread:missing-sister",
    ]


@pytest.mark.asyncio
async def test_typed_setting_is_persisted_through_ledger(db: DatabaseConnection):
    ledger = StoryMemoryLedger(SqliteStoryMemoryRepository(db))
    delta = await ledger.stage_settings(
        book_id="book-1",
        chapter_id="chapter-1",
        changes=(
            StorySettingChange(
                setting=CharacterState("lin", "location", "旧城区"),
                source=SourceReference(
                    chapter_id="chapter-1",
                    excerpt="林墨抵达旧城区。",
                ),
            ),
        ),
    )

    assert await ledger.current_state("book-1") == ()
    await ledger.approve_delta(delta.id)
    state = await ledger.current_state("book-1")

    assert state[0].memory_key == "character:lin:state:location"
    assert state[0].payload["characterId"] == "lin"
    assert state[0].payload["value"] == "旧城区"


@pytest.mark.asyncio
async def test_story_memory_api_stages_and_applies_all_setting_types(
    db: DatabaseConnection,
):
    request = StageStoryMemoryDeltaRequest.model_validate(
        {
            "bookId": "book-1",
            "chapterId": "chapter-8",
            "sourceRevision": "rev-8",
            "sourceType": "manual",
            "changes": [
                {
                    "kind": "character_state",
                    "characterId": "lin",
                    "attribute": "location",
                    "value": "旧城区",
                    "source": {"excerpt": "林墨进入旧城区。"},
                },
                {
                    "kind": "relationship_state",
                    "sourceCharacterId": "lin",
                    "targetCharacterId": "su",
                    "relationType": "trust",
                    "state": "fragile",
                    "source": {"excerpt": "林墨仍无法完全信任苏禾。"},
                },
                {
                    "kind": "world_fact",
                    "factId": "moon-law",
                    "statement": "月光会令魔法失效",
                    "truthMode": "canon",
                    "source": {"excerpt": "月光下，所有法术都消散了。"},
                },
                {
                    "kind": "timeline_event",
                    "eventId": "city-arrival",
                    "title": "抵达旧城区",
                    "summary": "林墨第一次进入旧城区",
                    "participantIds": ["lin"],
                    "narrativeOrder": 8,
                    "source": {"excerpt": "林墨第一次踏入旧城区。"},
                },
                {
                    "kind": "plot_thread",
                    "threadId": "missing-sister",
                    "title": "失踪的妹妹",
                    "summary": "林墨继续寻找妹妹",
                    "state": "advancing",
                    "openedChapterId": "chapter-1",
                    "source": {"excerpt": "新的线索指向旧城区。"},
                },
            ],
        }
    )

    staged = await stage_story_memory_delta(request)
    assert staged["data"]["status"] == "pending"
    assert len(staged["data"]["changes"]) == 5

    await apply_story_memory_delta(str(staged["data"]["id"]))
    current = await get_story_memory("book-1", None)

    assert [item["kind"] for item in current["data"]] == [
        "character_state",
        "plot_thread",
        "relationship_state",
        "timeline_event",
        "world_fact",
    ]
    assert all(item["version"] == 1 for item in current["data"])


def test_invalid_relationship_and_resolved_thread_are_rejected():
    with pytest.raises(ValueError, match="two different characters"):
        RelationshipState("lin", "lin", "trust", "strong")

    with pytest.raises(ValueError, match="resolved_chapter_id"):
        PlotThread(
            "missing-sister",
            "失踪的妹妹",
            "已经找到",
            state=PlotThreadState.RESOLVED,
        )
