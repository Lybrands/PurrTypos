from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from application.agent_event_stream import stream_agent_pages
from infrastructure.persistence.agent_output_publisher import InProcessAgentOutputPublisher
from purra.output import AgentOutputEvent, OutputChannel, OutputEventKind, OutputSource, OutputVisibility
from tests.test_agent_run_queries import temp_db, _seed_run


pytestmark = pytest.mark.asyncio


class Connected:
    async def is_disconnected(self):
        return False


async def test_stream_waits_between_empty_reads_and_emits_product_changes_without_output():
    reads, waits = [], []

    class Notifications:
        revision = 0

        async def wait_for_change(self, observed):
            waits.append(observed)
            self.revision += 1

    async def read(after):
        reads.append(after)
        return {"nextCursor": 0, "hasMore": False,
                "projectionVersion": "changed" if len(reads) >= 3 else "same",
                "done": len(reads) == 4}

    pages = [json.loads(item["data"]) async for item in stream_agent_pages(
        request=Connected(), read_page=read, notifications=Notifications())]
    assert len(reads) == 4
    assert len(waits) == 3
    assert len(pages) == 3


async def test_stream_drains_backlog_before_waiting_or_finishing():
    class Notifications:
        revision = 0

        async def wait_for_change(self, _observed):
            pytest.fail("backlog must not wait")

    async def read(after):
        return {"nextCursor": after + 500, "hasMore": after == 0,
                "done": after > 0}

    pages = [json.loads(item["data"]) async for item in stream_agent_pages(
        request=Connected(), read_page=read, notifications=Notifications())]
    assert [page["nextCursor"] for page in pages] == [500, 1000]


async def test_notifications_do_not_miss_a_commit_between_query_and_wait():
    publisher = InProcessAgentOutputPublisher()
    observed = publisher.revision
    now = datetime.now(timezone.utc)
    event = AgentOutputEvent(
        event_id="event-1", run_id="run-1", sequence=1, turn_id=None,
        output_stream_id=None, invocation_id=None, source_event_key="test-1",
        source=OutputSource.RUNTIME, kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.LIFECYCLE, visibility=OutputVisibility.PUBLIC,
        payload={}, occurred_at=now, emitted_at=now,
    )
    await publisher.publish_committed(event)
    await asyncio.wait_for(publisher.wait_for_change(observed), timeout=0.2)


async def test_writing_stream_is_session_scoped_and_drains_without_private_product_bodies(temp_db):
    from fastapi import HTTPException
    from routers.ai import stream_agent_run_events

    run_id = await _seed_run(temp_db)
    with pytest.raises(HTTPException) as error:
        await stream_agent_run_events(Connected(), run_id, 8, 0)
    assert error.value.status_code == 404
    await temp_db.execute("UPDATE ai_agent_runs SET status = 'done' WHERE id = ?", [run_id])
    response = await stream_agent_run_events(Connected(), run_id, 7, 0)
    pages = [json.loads(item["data"]) async for item in response.body_iterator]
    assert len(pages) == 1
    assert pages[0]["done"] is True
    assert len(pages[0]["events"]) == 3
    assert "productEvents" not in pages[0]
    assert "private prompt" not in json.dumps(pages)
