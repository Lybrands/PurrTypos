"""Verify native writing Runs with manual/automatic technique admission.

Uses disposable author files and records real model input receipts. No original
manuscript or configured database content is modified.
"""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
os.environ['PURRTYPOS_DEV_DIAGNOSTICS'] = '1'
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('technique_evaluation', ROOT / 'scripts/evaluate-writing-techniques.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

from application.agent_run_service import AgentRunService
from application.composition_factory import create_agent_composition
from application.model_runtime import model_request_from_runtime, reasoning_mode_from_options
from application.writing_technique_access import WritingTechniqueAccess
from application.writing_technique_exchange import import_technique
from database.connection import DatabaseConnection
from domains.writing.contracts import WritingDomainContext
from purra.api import AgentCoreRunOptions
from purra.contracts import AgentMessage, AgentRunRequest, AgentRunResult, PlanningMode, RunBinding
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


async def main(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Use an empty evidence directory')
    args.output.mkdir(parents=True, exist_ok=True)
    config = evaluation.configured_model(args.settings_db, args.model)
    runtime = ScreenplayAgentRuntimeRequest(apiKey=config['apiKey'], baseURL=config['baseUrl'], apiProvider=config['apiProvider'],
        options={'model': config['name'], 'model_profile': config['presetId'], 'profile_binding': 'compatible',
                 **({'thinking': {'type': args.reasoning_mode}} if args.reasoning_mode != 'default' else {}), 'max_generation_tokens': 8192}, contextWindow=config['contextWindow'])
    db = DatabaseConnection(args.output)
    await db.init()
    composition = create_agent_composition(db)
    report = []
    try:
        await db.execute("INSERT INTO books(id,title) VALUES ('fixture-book','隔离运行验收')")
        await db.execute("INSERT INTO ai_sessions(id,book_id,scope) VALUES (1,'fixture-book','setting')")
        access = WritingTechniqueAccess(db)
        draft = await import_technique(access.library, operation_id='runtime-fixture', files={
            'SKILL.md': '---\nname: 场景聚焦\ndescription: 应对眼前危险时调整叙述节奏，回忆时保持物件线索。\n---\n按场景目标决定取材。写追赶或避险时，先阅读[危险场景](危险.md)；写回忆时才读[回忆场景](回忆.md)。',
            '危险.md': '把注意力放在眼前可行动的信息上。先写感官信号，再写身体应对，最后留下尚未解决的变化。例子请自行创作。',
            '回忆.md': '借物件引出过去，再通过人物当下的动作回到现在。'})
        draft = await access.library.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'],
            expected_tree_digest=draft['treeDigest'], operation_id='seal')
        ref = draft['sealedRef']
        await access.library.publish('technique', ref['id'], ref=ref, expected_published_head=None, operation_id='publish')
        await access.grant('fixture-book', ref)
        model = model_request_from_runtime(runtime)
        for case, mode, manual in [('manual-empty', 'manual', []), ('manual-selected', 'manual', [ref]), ('automatic', 'auto', [])]:
            if args.cases and case not in args.cases:
                continue
            reserved = await access.reserve_input(operation_id=case, book_id='fixture-book', session_id='1', mode=mode, manual=manual)
            context = WritingDomainContext(book_id='fixture-book', writing_technique_input_id=reserved['inputId'])
            prompt = '写约150字的场景：送信员在市场中躲开追赶。直接在回复里给出正文，不保存小说章节。'
            if mode == 'auto':
                prompt += '请搜索已授权写作技法，选择适用的一项，从入口按条件读取所需辅助文件后写作。'
            elif manual:
                prompt += '请使用本轮指定的技法，从入口按条件读取所需辅助文件后写作。'
            request = AgentRunRequest(messages=(AgentMessage(role='user', content=prompt),), model=model,
                domain_context=context.to_core_context(), session_id='1', mode='agent', tools_enabled=True,
                planning_mode=PlanningMode.REACTIVE, context_window=model.capability_snapshot.context_window_tokens)
            print(case, 'started', flush=True)
            result = None
            async def execute():
                nonlocal result
                async for event in AgentRunService(composition).run(request=request, api_key=config['apiKey'], signal=asyncio.Event(),
                    options=AgentCoreRunOptions(reasoning_mode=reasoning_mode_from_options(runtime.options),
                        binding=RunBinding(namespace='writing', aggregate_id='fixture-book', command_id=str(uuid.uuid4())))):
                    if isinstance(event, AgentRunResult):
                        result = event
            try:
                await asyncio.wait_for(execute(), 240)
                record = {'case': case, 'reasoningMode': args.reasoning_mode, 'runId': result.run_id, 'status': result.status.value, 'error': result.error, 'text': result.final_response}
                events = await db.fetch_all('SELECT event_type,payload_json FROM ai_agent_run_events WHERE run_id=? ORDER BY id', [result.run_id])
                tool_names = [call['name'] for event in events if event['event_type'] == 'tool.calls_started' for call in json.loads(event['payload_json']).get('calls', [])]
                record['toolNames'] = tool_names
                opened = [json.loads(event['payload_json']) for event in events if event['event_type'] == 'stream.opened']
                inputs = [call.get('inputMessages', []) for event in opened for call in event.get('callParameters', [])]
                actual = json.dumps(inputs, ensure_ascii=False)
                record['inputCaptured'] = any(inputs)
                record['entryInActualInput'] = '场景聚焦' in actual if any(inputs) else None
                record['dangerBodyInActualInput'] = '先写感官信号' in actual if any(inputs) else None
                record['unrelatedBodyInActualInput'] = '借物件引出过去' in actual if any(inputs) else None
                record['contextEvidence'] = [receipt for event in opened for receipt in event.get('contextEvidence', [])]
                record['prompt'] = prompt
                state = await db.fetch_one('SELECT * FROM writing_technique_run_state WHERE run_id=?', [result.run_id])
                record['techniqueState'] = state
                report.append(record)
            except Exception as error:
                report.append({'case': case, 'status': 'failed', 'errorType': type(error).__name__, 'errorCode': getattr(error, 'code', '')})
            evaluation.write(args.output / 'report.json', report)
            print(case, report[-1]['status'], flush=True)
    finally:
        await composition.shutdown()
        await db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reasoning-mode', choices=['default', 'enabled', 'disabled'], default='enabled')
    parser.add_argument('--cases', nargs='+', choices=['manual-empty', 'manual-selected', 'automatic'])
    parser.add_argument('--model', default='kimi-k2.6')
    parser.add_argument('--settings-db', type=Path, default=Path.home() / 'Library/Application Support/purrtypos/purrtypos.db')
    asyncio.run(main(parser.parse_args()))
