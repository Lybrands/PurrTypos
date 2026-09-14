from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from application.agent_event_stream import stream_agent_pages
from application.agent_composition import get_agent_composition
from agents.novel_analysis.stream_projection import VersionedNovelAnalysisStreamQuery
from agents.novel_analysis.legacy_read_adapter import NovelAnalysisLegacyReadAdapter
from infrastructure.persistence.agent_output_publisher import InProcessAgentOutputPublisher
from infrastructure.persistence.sqlite_long_task_repository import SqliteLongTaskRepository
from infrastructure.persistence.run_store import create_run
from purra.contracts import RunBinding
from purra.errors import ContractViolationError
from purra.output import (
    AgentOutputEventDraft,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputVisibility,
)
from tests.test_agent_run_queries import temp_db, _seed_run, _append_public_runtime_event
from tests.support.novel_source_fixtures import seed_novel_source

pytestmark = pytest.mark.asyncio


class Connected:
    async def is_disconnected(self):
        return False


def analysis_query(db, composition, *, output_repository=None, historical=None):
    return VersionedNovelAnalysisStreamQuery(
        db,
        output_repository=output_repository or composition.output_journal,
        historical_analysis=historical or NovelAnalysisLegacyReadAdapter(
            db,
            long_tasks=composition.long_task_repository,
        ),
        long_tasks=composition.long_task_repository,
    )


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
    source = await seed_novel_source(temp_db)
    revision = source['id']
    composition = get_agent_composition()
    calls = 0
    service = NovelAnalysisLegacyReadAdapter(
        temp_db,
        long_tasks=composition.long_task_repository,
    )

    class Analysis:
        async def list_for_revision(self, revision_id):
            nonlocal calls
            calls += 1
            return await service.list_for_revision(revision_id)

    query = analysis_query(temp_db, composition, historical=Analysis())
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
    # A legacy unit without durable Task membership is not public history.
    assert third['chunks'] == []
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


async def test_versioned_source_subscription_retains_frozen_legacy_runs(temp_db):
    source = await seed_novel_source(temp_db)
    revision = source['id']
    composition = get_agent_composition()
    service = NovelAnalysisLegacyReadAdapter(
        temp_db,
        long_tasks=composition.long_task_repository,
    )
    query = VersionedNovelAnalysisStreamQuery(
        temp_db,
        output_repository=composition.output_journal,
        historical_analysis=service,
        long_tasks=composition.long_task_repository,
    )
    root = await create_run(
        temp_db,
        session_id=None,
        prompt='分析',
        mode='novel_analysis',
        binding=RunBinding(
            namespace='novel_source_analysis',
            aggregate_id=revision,
            command_id='legacy-root',
        ),
    )
    await _append_public_runtime_event(temp_db, root, 'legacy-event', {})

    page = await query.read_page(revision)

    assert [item['runId'] for item in page['runs']] == [root]
    assert [item['runId'] for item in page['chunks']] == [root]


async def test_source_subscription_keeps_child_operations_but_hides_child_bodies(temp_db):
    source = await seed_novel_source(temp_db)
    revision = source['id']
    composition = get_agent_composition()
    query = analysis_query(temp_db, composition)
    root = await create_run(
        temp_db,
        session_id=None,
        prompt='分析',
        mode='novel_analysis',
        binding=RunBinding(
            namespace='novel_source_analysis', aggregate_id=revision,
            command_id='root',
        ),
    )
    await _append_public_runtime_event(temp_db, root, 'root-started', {})
    first = await query.read_page(revision)

    legacy_unit = await create_run(
        temp_db,
        session_id=None,
        prompt='传统单元',
        mode='novel_analysis_unit',
        binding=RunBinding(
            namespace='novel_source_analysis.unit', aggregate_id=revision,
            command_id='legacy-unit',
        ),
    )
    tree_child = await create_run(
        temp_db,
        session_id=None,
        prompt='私有 Child',
        mode='novel_analysis_unit',
        root_run_id=root,
        parent_run_id=root,
        agent_id='source-analysis-child',
        binding=RunBinding(
            namespace='novel_source_analysis.unit', aggregate_id=revision,
            command_id='tree-child',
        ),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, total_units) "
        "VALUES ('visible-task', 'test', 'novel_source_analysis', ?, ?, 2)",
        [revision, root],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES ('visible-task', ?, 'created')",
        [root],
    )
    for position, run_id in enumerate((legacy_unit, tree_child)):
        await temp_db.execute(
            "INSERT INTO ai_agent_long_task_units "
            "(task_id, unit_id, semantic_key, position, run_id) "
            "VALUES ('visible-task', ?, ?, ?, ?)",
            [f'unit-{position}', f'unit-{position}', position, run_id],
        )
    await _append_public_runtime_event(temp_db, legacy_unit, 'legacy-unit-started', {})
    await _append_public_runtime_event(temp_db, tree_child, 'private-child-body', {})
    await composition.output_journal.append_event(AgentOutputEventDraft(
        run_id=tree_child,
        turn_id=None,
        output_stream_id=None,
        invocation_id=None,
        source_event_key='fixture:tree-child:tool-started',
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.OPERATION_STARTED,
        channel=OutputChannel.OPERATION,
        visibility=OutputVisibility.PUBLIC,
        payload={
            'operationId': 'tree-child-tool',
            'kind': 'tool',
            'startedAt': datetime.now(timezone.utc).isoformat(),
            'display': {
                'labelKey': 'agent.operation.tool',
                'labelParams': {'toolName': 'findAnalysisSourceEvidence'},
            },
        },
        occurred_at=datetime.now(timezone.utc),
    ))

    page = await query.read_page(revision, after=first['nextCursor'])

    assert [chunk['runId'] for chunk in page['chunks']] == [
        legacy_unit,
        tree_child,
    ]
    assert page['chunks'][1]['chunk']['kind'] == 'operation.started'
    assert page['chunks'][1]['chunk']['channel'] == 'operation'
    assert page['nextCursor'] > first['nextCursor']
    assert 'private-child-body' not in json.dumps(page, ensure_ascii=False)

    restarted = analysis_query(temp_db, composition)
    replay = await restarted.read_page(revision, after=first['nextCursor'])
    assert replay['chunks'] == page['chunks']
    assert replay['nextCursor'] == page['nextCursor']
    assert (await restarted.read_page(revision, after=page['nextCursor']))['chunks'] == []


async def test_source_subscription_hides_child_created_after_membership_read(temp_db):
    revision = (await seed_novel_source(temp_db))['id']
    composition = get_agent_composition()
    root = await create_run(
        temp_db, session_id=None, prompt='synthetic root', mode='novel_analysis',
        binding=RunBinding(namespace='novel_source_analysis', aggregate_id=revision,
                           command_id='racing-root'),
    )

    class Output:
        inserted = False

        async def list_bound_events(self, **kwargs):
            if not self.inserted:
                self.inserted = True
                child = await create_run(
                    temp_db, session_id=None, prompt='synthetic child',
                    mode='novel_analysis_unit', root_run_id=root, parent_run_id=root,
                    binding=RunBinding(namespace='novel_source_analysis.unit',
                                       aggregate_id=revision, command_id='racing-child'),
                )
                await _append_public_runtime_event(temp_db, child, 'CHILD_PRIVATE', {})
                await _append_public_runtime_event(temp_db, root, 'root-progress', {})
            return await composition.output_journal.list_bound_events(**kwargs)

    query = analysis_query(temp_db, composition, output_repository=Output())
    hidden = await query.read_page(revision, limit=1)
    assert hidden['chunks'] == []
    assert hidden['nextCursor'] > 0
    assert hidden['hasMore']
    visible = await query.read_page(revision, after=hidden['nextCursor'], limit=1)
    assert [item['runId'] for item in visible['chunks']] == [root]
    assert visible['nextCursor'] > hidden['nextCursor']
    assert not visible['hasMore']
    assert 'CHILD_PRIVATE' not in json.dumps([hidden, visible])


async def test_source_projection_does_not_load_execution_budget_contract(temp_db):
    source = await seed_novel_source(temp_db)
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

    composition = get_agent_composition()
    query = analysis_query(temp_db, composition)
    page = await query.read_page(revision)

    assert page['runs'][0]['taskId'] == 'historical-task'
    assert page['runs'][0]['analysisPlan'] == {'summary': '只读历史计划'}
    with pytest.raises(ContractViolationError, match='Unknown long task budget fields'):
        await SqliteLongTaskRepository(temp_db).load('historical-task')


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


async def test_source_subscription_hides_archived_task_units_and_retry_history(temp_db):
    revision = (await seed_novel_source(temp_db))['id']
    composition = get_agent_composition()
    root = await create_run(temp_db, session_id=None, prompt='root', mode='novel_analysis',
                            binding=RunBinding(namespace='novel_source_analysis',
                                               aggregate_id=revision, command_id='archive-root'))
    units = []
    for name in ('previous', 'current'):
        units.append(await create_run(
            temp_db, session_id=None, prompt=name, mode='novel_analysis_unit',
            binding=RunBinding(namespace='novel_source_analysis.unit',
                               aggregate_id=revision, command_id=name)))
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks (id,namespace,kind,owner_id,created_by_run_id,total_units) "
        "VALUES ('archive-task','test','novel_source_analysis',?,?,1)", [revision, root])
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id,run_id,relation) VALUES ('archive-task',?,'created')", [root])
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units (task_id,unit_id,semantic_key,position,run_id,metadata_json) "
        "VALUES ('archive-task','unit','unit',0,?,?)",
        [units[1], json.dumps({'runHistory': [{'runId': units[0]}]})])
    for run_id in (root, *units):
        await _append_public_runtime_event(temp_db, run_id, 'archived-output', {})
    query = analysis_query(temp_db, composition)
    before = await query.read_page(revision)
    assert {item['runId'] for item in before['chunks']} == {root, *units}
    await temp_db.execute(
        "INSERT INTO novel_analysis_superseded_runs VALUES (?, 'replacement', ?)", [root, root])
    after = await query.read_page(revision)
    assert after['chunks'] == []
    assert after['nextCursor'] == before['nextCursor']
