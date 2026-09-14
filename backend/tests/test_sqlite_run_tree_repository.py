import asyncio
from dataclasses import replace
import pytest
from purra.api import AgentCapabilityGrant, BeginRootAgentCommand, ChildAgentSpec, SpawnAgentsCommand
from purra.errors import ContractViolationError
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository


@pytest.mark.asyncio
async def test_restart_preserves_receipts_leases_and_checkpoints(tmp_path):
    now = [1000]
    db=DatabaseConnection(tmp_path); await db.init()
    try:
        tree=SqliteRunTreeRepository(db,clock_ms=lambda:now[0])
        root=await tree.begin_root(BeginRootAgentCommand(run_id='root',agent_id='agent',name='root',title='Root',instruction='synthetic',objective='synthetic',capability_grant=AgentCapabilityGrant(can_spawn_agents=True),idempotency_key='root'))
        cmd=SpawnAgentsCommand(parent_run_id=root.run_id,idempotency_key='spawn',children=(ChildAgentSpec(name='child',title='Child',instruction='synthetic',objective='synthetic'),))
        receipt=await tree.spawn_agents(cmd)
        child=(await tree.list_descendants(root.run_id))[0]
        claimed=await tree.claim_run(child.run_id,owner_id='worker',lease_duration_ms=100)
        await db.close()
        await db.init()
        restarted=SqliteRunTreeRepository(db,clock_ms=lambda:now[0])
        replay = await restarted.spawn_agents(cmd)
        assert replay.replayed
        assert replace(replay, replayed=False) == receipt
        assert await restarted.get_run(child.run_id)==claimed
        now[0]+=101
        with pytest.raises(ContractViolationError):
            await restarted.complete_run(child.run_id,expected_context_version=0,result='answer',content_ref='synthetic://answer',fingerprint='hash',lease_owner_id='worker',lease_epoch=claimed.lease_epoch)
        reclaimed=await restarted.claim_run(child.run_id,owner_id='new-worker',lease_duration_ms=100)
        assert reclaimed.lease_epoch > claimed.lease_epoch
        await restarted.complete_run(child.run_id,expected_context_version=0,result='answer',content_ref='synthetic://answer',fingerprint='hash',lease_owner_id='new-worker',lease_epoch=reclaimed.lease_epoch)
        fresh=SqliteRunTreeRepository(db,clock_ms=lambda:now[0])
        node=await fresh.get_agent(child.agent_id)
        assert (await fresh.get_checkpoint(node.context_checkpoint_id)).fingerprint=='hash'
        assert (await fresh.aggregate_runs(root.run_id,[child.run_id])).results[0]['status']=='done'
        before=await db.fetch_one('SELECT COUNT(*) AS n FROM ai_agent_tree_commands_v3')
        with pytest.raises(ContractViolationError):
            await fresh.spawn_agents(SpawnAgentsCommand(parent_run_id=root.run_id,idempotency_key='evil',children=(ChildAgentSpec(name='evil',title='Evil',instruction='x',objective='x',capability_grant=AgentCapabilityGrant(can_spawn_agents=True,allowed_tools=('unauthorized',))),)))
        assert await db.fetch_one('SELECT COUNT(*) AS n FROM ai_agent_tree_commands_v3')==before
    finally: await db.close()


@pytest.mark.asyncio
async def test_two_connections_serialize_claims_and_reject_unknown_journal(tmp_path):
    a,b=DatabaseConnection(tmp_path),DatabaseConnection(tmp_path)
    await a.init(); await b.init()
    try:
        x,y=SqliteRunTreeRepository(a),SqliteRunTreeRepository(b)
        await x.begin_root(BeginRootAgentCommand(run_id='root',agent_id='agent',name='root',title='Root',instruction='x',objective='x',capability_grant=AgentCapabilityGrant(can_spawn_agents=True),idempotency_key='root'))
        await x.spawn_agents(SpawnAgentsCommand(parent_run_id='root',idempotency_key='spawn',children=(ChildAgentSpec(name='child',title='Child',instruction='x',objective='x'),)))
        child=(await x.list_descendants('root'))[0]
        claims=await asyncio.gather(x.claim_run(child.run_id,owner_id='a'),y.claim_run(child.run_id,owner_id='b'))
        assert sum(c is not None for c in claims)==1
        await a.execute("UPDATE ai_agent_tree_commands_v3 SET schema='future/v2'")
        with pytest.raises(ContractViolationError,match='Unsupported tree journal'):
            await SqliteRunTreeRepository(a).get_run('root')
    finally: await a.close(); await b.close()


@pytest.mark.asyncio
async def test_older_journal_is_preserved_without_replay(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await db.execute("CREATE TABLE ai_agent_tree_commands (evidence TEXT)")
        await db.execute("INSERT INTO ai_agent_tree_commands VALUES ('old evidence')")
        tree = SqliteRunTreeRepository(db)
        await tree.begin_root(BeginRootAgentCommand(run_id="root", agent_id="agent", name="root",
            title="Root", instruction="Own", objective="Work", capability_grant=AgentCapabilityGrant(), idempotency_key="root"))
        assert await db.fetch_all("SELECT * FROM ai_agent_tree_commands") == [{"evidence": "old evidence"}]
        assert await tree.list_agent_descendants("agent") == ()
    finally:
        await db.close()
