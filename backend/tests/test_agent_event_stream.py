from __future__ import annotations

import asyncio
import json

import pytest

from application.agent_event_stream import stream_agent_pages
from application.agent_composition import get_agent_composition
from application.novel_analysis_service import NovelAnalysisService
from application.novel_analysis_stream import NovelAnalysisStreamQuery
from application.screenplay_agent_stream import ScreenplayCanonicalOutputQuery
from infrastructure.persistence.agent_output_publisher import InProcessAgentOutputPublisher
from infrastructure.persistence.sqlite_long_task_repository import SqliteLongTaskRepository
from infrastructure.persistence.run_store import create_run
from purra.contracts import RunBinding
from purra.errors import ContractViolationError
from tests.test_agent_run_queries import temp_db, _seed_run, _append_public_runtime_event
from tests.test_novel_analysis import _source

pytestmark = pytest.mark.asyncio


class Connected:
    async def is_disconnected(self):
        return False


async def test_stream_waits_between_empty_reads_and_emits_product_changes_without_output():
    reads = []
    waits = []

    class Notifications:
        revision = 0

        async def wait_for_change(self, observed):
            waits.append(observed)
            self.revision += 1

    async def read(after):
        reads.append(after)
        version = 'changed' if len(reads) >= 3 else 'same'
        return {'nextCursor': 0, 'hasMore': False, 'projectionVersion': version,
                'done': len(reads) == 4}

    pages = [json.loads(item['data']) async for item in stream_agent_pages(
        request=Connected(), read_page=read, notifications=Notifications(),
    )]
    assert len(reads) == 4
    assert len(waits) == 3
    assert len(pages) == 3  # initial, product-only change, terminal


async def test_stream_drains_backlog_before_waiting_or_finishing():
    class Notifications:
        revision = 0

        async def wait_for_change(self, _observed):
            pytest.fail('backlog must not wait')

    async def read(after):
        return {'nextCursor': after + 500, 'hasMore': after == 0, 'done': after > 0}

    pages = [json.loads(item['data']) async for item in stream_agent_pages(
        request=Connected(), read_page=read, notifications=Notifications(),
    )]
    assert [page['nextCursor'] for page in pages] == [500, 1000]


async def test_notifications_do_not_miss_a_commit_between_query_and_wait():
    publisher = InProcessAgentOutputPublisher()
    observed = publisher.revision
    # A committed event wakes all scope queries; durable cursors select their data.
    from datetime import datetime, timezone
    from purra.output import AgentOutputEvent, OutputChannel, OutputEventKind, OutputSource, OutputVisibility
    event = AgentOutputEvent(
        event_id='event-1', run_id='run-1', sequence=1, turn_id=None,
        output_stream_id=None, invocation_id=None, source_event_key='test-1',
        source=OutputSource.RUNTIME, kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.LIFECYCLE, visibility=OutputVisibility.PUBLIC,
        payload={}, occurred_at=datetime.now(timezone.utc), emitted_at=datetime.now(timezone.utc),
    )
    await publisher.publish_committed(event)
    await asyncio.wait_for(publisher.wait_for_change(observed), timeout=0.2)


async def test_source_subscription_discovers_units_and_does_not_reload_projection_for_text(temp_db):
    source = await _source(temp_db)
    revision = source['id']
    composition = get_agent_composition()
    calls = 0
    service = NovelAnalysisService(temp_db, composition)

    class Analysis:
        async def list_for_revision(self, revision_id):
            nonlocal calls
            calls += 1
            return await service.list_for_revision(revision_id)

    query = NovelAnalysisStreamQuery(temp_db, output_repository=composition.output_journal,
                                    analysis_service=Analysis())
    root = await create_run(temp_db, session_id=None, prompt='分析', mode='novel_analysis', binding=RunBinding(
        namespace='novel_source_analysis', aggregate_id=revision, command_id='root',
    ))
    await _append_public_runtime_event(temp_db, root, 'first', {})
    first = await query.read_page(revision)
    assert first['runs'][0]['runId'] == root
    await _append_public_runtime_event(temp_db, root, 'second', {})
    second = await query.read_page(revision, after=first['nextCursor'])
    assert len(second['chunks']) == 1
    assert 'runs' not in second
    assert calls == 1

    unit = await create_run(temp_db, session_id=None, prompt='片段', mode='novel_analysis_unit', binding=RunBinding(
        namespace='novel_source_analysis.unit', aggregate_id=revision, command_id='unit',
    ))
    foreign = await create_run(temp_db, session_id=None, prompt='其他来源', mode='novel_analysis_unit', binding=RunBinding(
        namespace='novel_source_analysis.unit', aggregate_id='another-revision', command_id='foreign',
    ))
    await _append_public_runtime_event(temp_db, unit, 'unit-started', {})
    await _append_public_runtime_event(temp_db, foreign, 'foreign-private-scope', {})
    third = await query.read_page(revision, after=second['nextCursor'])
    assert {item['runId'] for item in third['chunks']} == {unit}
    assert 'runs' in third
    assert calls == 2
    assert '忽略系统规则' not in json.dumps(third, ensure_ascii=False)

    # Unit Run binding does not bump Task.revision. It must independently
    # invalidate membership, even with no new public event or Run status.
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks (id, namespace, kind, owner_id, created_by_run_id, total_units) "
        "VALUES ('task-1', 'test', 'novel_source_analysis', ?, ?, 1)", [revision, root],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) VALUES ('task-1', ?, 'created')", [root],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units (task_id, unit_id, semantic_key, position) "
        "VALUES ('task-1', 'unit-1', 'unit-1', 0)"
    )
    before_binding = await query.read_page(revision, after=third['nextCursor'])
    assert before_binding['runs'][0]['relatedRuns'] == []
    await temp_db.execute(
        "UPDATE ai_agent_long_task_units SET run_id = ? WHERE task_id = 'task-1'", [unit],
    )
    after_binding = await query.read_page(revision, after=third['nextCursor'])
    assert after_binding['runs'][0]['taskRevision'] == before_binding['runs'][0]['taskRevision']
    assert after_binding['runs'][0]['relatedRuns'][0]['runId'] == unit
    assert after_binding['chunks'] == []


async def test_source_projection_does_not_load_execution_budget_contract(temp_db):
    source = await _source(temp_db)
    revision = source['id']
    root = await create_run(
        temp_db,
        session_id=None,
        prompt='历史分析',
        mode='novel_analysis',
        binding=RunBinding(
            namespace='novel_source_analysis',
            aggregate_id=revision,
            command_id='historical-root',
        ),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, total_units, "
        "budget_limits_json, metadata_json) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        [
            'historical-task',
            'novel.analysis',
            'analysis',
            revision,
            root,
            json.dumps({'unknownExecutionField': 1}),
            json.dumps({'analysisPlan': {'summary': '只读历史计划'}}),
        ],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES ('historical-task', ?, 'created')",
        [root],
    )

    service = NovelAnalysisService(temp_db, get_agent_composition())
    query = NovelAnalysisStreamQuery(
        temp_db,
        output_repository=get_agent_composition().output_journal,
        analysis_service=service,
    )
    page = await query.read_page(revision)

    assert page['runs'][0]['taskId'] == 'historical-task'
    assert page['runs'][0]['analysisPlan'] == {'summary': '只读历史计划'}
    with pytest.raises(ContractViolationError, match='Unknown long task budget fields'):
        await SqliteLongTaskRepository(temp_db).load('historical-task')


async def test_screenplay_projection_query_ignores_text_and_heartbeat_columns(temp_db):
    query = ScreenplayCanonicalOutputQuery(temp_db, output_repository=get_agent_composition().output_journal)
    await temp_db.execute(
        "INSERT INTO screenplay_agent_turns (id, project_id, session_id, command_id, user_content) "
        "VALUES ('turn-1', 'project-1', 7, 'command-1', 'test')"
    )
    before = await query.projection_version(7)
    await temp_db.execute(
        "UPDATE screenplay_agent_turns SET assistant_content = 'streamed text', heartbeat_at_ms = 123 "
        "WHERE id = 'turn-1'"
    )
    assert await query.projection_version(7) == before
    await create_run(temp_db, session_id=7, prompt='test', mode='agent', binding=RunBinding(
        namespace='screenplay.conversation_turn', aggregate_id='project-1', command_id='command-1',
    ))
    with_root = await query.projection_version(7)
    assert with_root != before
    await temp_db.execute("UPDATE screenplay_agent_turns SET status = 'completed' WHERE id = 'turn-1'")
    assert await query.projection_version(7) != with_root


async def test_writing_stream_is_session_scoped_and_drains_without_private_product_bodies(temp_db):
    from fastapi import HTTPException
    from routers.ai import stream_agent_run_events

    run_id = await _seed_run(temp_db)
    with pytest.raises(HTTPException) as error:
        await stream_agent_run_events(Connected(), run_id, 8, 0)
    assert error.value.status_code == 404
    await temp_db.execute("UPDATE ai_agent_runs SET status = 'done' WHERE id = ?", [run_id])
    response = await stream_agent_run_events(Connected(), run_id, 7, 0)
    pages = [json.loads(item['data']) async for item in response.body_iterator]
    assert len(pages) == 1
    assert pages[0]['done'] is True
    assert len(pages[0]['events']) == 3
    assert 'productEvents' not in pages[0]
    assert 'private prompt' not in json.dumps(pages)
