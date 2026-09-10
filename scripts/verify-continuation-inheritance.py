"""Real source analysis -> inherited workspace -> writing Run, on synthetic data."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['PURRTYPOS_DEV_DIAGNOSTICS'] = '1'
spec = importlib.util.spec_from_file_location('evaluation', ROOT / 'scripts/evaluate-writing-techniques.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
from application.agent_run_service import AgentRunService
from application.composition_factory import create_agent_composition
from application.continuation_service import ContinuationService
from application.continuation_context import ContinuationContextService
from application.model_runtime import model_request_from_runtime, reasoning_mode_from_options
from application.novel_analysis_service import NovelAnalysisService
from application.novel_source_service import NovelSourceService
from application.writing_technique_service import WritingTechniqueService
from application.writing_technique_access import WritingTechniqueAccess
from database.connection import DatabaseConnection
from domains.writing.contracts import WritingDomainContext
from purra.api import AgentCoreRunOptions
from purra.contracts import AgentMessage, AgentRunRequest, AgentRunResult, PlanningMode, RunBinding
from purra.output import AgentOutputEvent, OutputEventKind
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


async def main(args):
    if not args.output.resolve().is_relative_to(Path('/tmp').resolve()):
        raise ValueError('Acceptance writes must stay in an isolated /tmp directory')
    if args.output.exists() and any(args.output.iterdir()) and not args.writing_only:
        raise ValueError('Use an empty isolated output directory')
    args.output.mkdir(parents=True, exist_ok=True)
    config = evaluation.configured_model(args.settings_db, args.model)
    runtime = ScreenplayAgentRuntimeRequest(apiKey=config['apiKey'], baseURL=config['baseUrl'], apiProvider=config['apiProvider'], options={'model': config['name'], 'model_profile': config['presetId'], 'profile_binding': 'compatible', 'thinking': {'type': 'enabled'}, 'max_generation_tokens': 8192}, contextWindow=config['contextWindow'])
    source = json.loads((ROOT / 'docs/fixtures/writing-techniques-p0/sources.json').read_text())['sources'][0]['text']
    source = '# 第一章\n' + source
    (args.output / 'synthetic-source.md').write_text(source)
    db = DatabaseConnection(args.output)
    await db.init()
    composition = create_agent_composition(db)
    report = {'model': config['name'], 'protocol': config['apiProvider'], 'synthetic': True, 'status': 'running'}
    started = time.monotonic()
    task = None
    try:
        if args.writing_only:
            row = await db.fetch_one("SELECT * FROM continuation_operations ORDER BY rowid DESC LIMIT 1")
            if not row:
                raise RuntimeError('missing_inherited_fixture')
            book = row['book_id']
            inherited = json.loads(row['manifest_json'])
            if args.new_continuation:
                continuations = ContinuationService(db)
                source_args = dict(source_revision_id=inherited['sourceRevisionId'], source_analysis_id=inherited['sourceAnalysisId'], fork_section_id=inherited['forkSectionId'])
                preview = await continuations.preview_canon(**source_args)
                created = await continuations.create_continuation(title='吊篮之后 · 联合验收', **source_args, expected_snapshot_digest=preview['snapshotDigest'], operation_id='acceptance-' + uuid.uuid4().hex)
                book, inherited = created['book']['id'], created['inheritance']
            ref = inherited['defaultTechniques'][0]
            library = WritingTechniqueService(db)
            report.update(bookId=book, inheritance=inherited)
        else:
            sources = NovelSourceService(db)
            preview = sources.preview_external_import(file_name='sample.md', extension='.md', content=source)
            revision = await sources.confirm_external_import(title='吊篮 · 隔离样本', file_name='sample.md', extension='.md', content=source, expected_content_digest=preview['contentDigest'], confirm_single_section=True, rights_confirmed=True, model_data_boundary_confirmed=True)
            analysis = NovelAnalysisService(db, composition)
            print('real source analysis started', flush=True)
            task = analysis.dispatch(source_revision_id=revision['id'], section_ids=tuple(item['id'] for item in revision['sections']), task_idempotency_key='inheritance-analysis', run_command_id='inheritance-analysis', prompt='分析人物状态、空间与动作先后及叙述节奏，保留证据，提炼该片段支持的技法。', failed_resume_attempts=0, runtime=runtime)
            await asyncio.wait_for(asyncio.shield(task), 900)
            view = (await analysis.list_for_revision(revision['id']))[0]
            report['analysis'] = view
            if not view.get('artifactRef'):
                raise RuntimeError('analysis_missing_artifact')
            published = await analysis.publish(view['artifactRef'])
            report['analysisId'] = published['id']
            candidate = published['summary']['techniqueResult'].get('candidate')
            if not candidate:
                raise RuntimeError('analysis_has_no_usable_technique')
            library = WritingTechniqueService(db)
            candidate_ref = {'kind': 'technique', 'id': candidate['techniqueId'], 'versionId': candidate['versionId']}
            draft = await library.create_draft(operation_id='save-analysis', from_version=candidate_ref, owner={'analysisId': published['id'], 'sourceRevisionId': revision['id'], 'sourceTechnique': candidate_ref})
            draft = await library.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'], expected_tree_digest=draft['treeDigest'], operation_id='seal-library')
            ref = draft['sealedRef']
            await library.publish('technique', ref['id'], ref=ref, expected_published_head=None, operation_id='publish-library')
            continuations = ContinuationService(db)
            preview = await continuations.preview_canon(source_revision_id=revision['id'], source_analysis_id=published['id'], fork_section_id=revision['sections'][-1]['id'])
            created = await continuations.create_continuation(title='吊篮之后', source_revision_id=revision['id'], source_analysis_id=published['id'], fork_section_id=revision['sections'][-1]['id'], expected_snapshot_digest=preview['snapshotDigest'], operation_id='create-continuation')
            book = created['book']['id']
            report['bookId'] = book
            report['inheritance'] = created['inheritance']
        session_id = str(await db.execute_and_get_id("INSERT INTO ai_sessions(book_id,scope) VALUES (?,'setting')", [book]))
        await library.initialize_session(session_id, book)
        reserved = await WritingTechniqueAccess(db).reserve_input(operation_id='writing-input-' + session_id, book_id=book, session_id=session_id, mode='manual', manual=None)
        model = model_request_from_runtime(runtime)
        prompt = '续写下一章约200字。先用 readContinuationSourceSection（省略 sectionId）发现原作历史目录，再用该工具传入目录中的 sectionId 读取最后一章，再读取继承的人物和背景资料，按本轮技法衔接人物状态与场景，创建新章节并提交正文修改建议。不要重复原文，不改动历史章节；不以总结或解释代替正文。'
        request = AgentRunRequest(messages=(AgentMessage(role='user', content=prompt),), model=model, domain_context=WritingDomainContext(book_id=book, writing_technique_input_id=reserved['inputId']).to_core_context(), session_id=session_id, mode='agent', tools_enabled=True, planning_mode=PlanningMode.AUTO, context_window=model.capability_snapshot.context_window_tokens)
        print('real continuation writing started', flush=True)
        result = None
        async def execute():
            nonlocal result
            async for event in AgentRunService(composition).run(request=request, api_key=config['apiKey'], signal=asyncio.Event(), options=AgentCoreRunOptions(reasoning_mode=reasoning_mode_from_options(runtime.options), binding=RunBinding(namespace='writing', aggregate_id=book, command_id='continuation-writing-' + session_id))):
                if isinstance(event, AgentRunResult):
                    result = event
                elif isinstance(event, AgentOutputEvent) and event.kind is OutputEventKind.RUNTIME and event.payload.get('eventType') == 'approval.requested':
                    approval = event.payload['data']
                    # The acceptance task authorizes chapter creation only in this synthetic book.
                    approved = approval.get('toolName') == 'createWritingChapter'
                    await composition.resolve_approval(approval['approvalId'], approved)
                    report.setdefault('approvalDecisions', []).append({'tool': approval.get('toolName'), 'approved': approved})
        await asyncio.wait_for(execute(), 900)
        report.update(runId=result.run_id, status=result.status.value, error=result.error, finalResponse=result.final_response)
        events = await db.fetch_all('SELECT event_type,payload_json FROM ai_agent_run_events WHERE run_id=? ORDER BY id', [result.run_id])
        opened = [json.loads(e['payload_json']) for e in events if e['event_type']=='stream.opened']
        inputs = [call.get('inputMessages', []) for e in opened for call in e.get('callParameters', [])]
        actual = json.dumps(inputs, ensure_ascii=False)
        entry = await library.read_version_file(ref, 'SKILL.md')
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
        actual_strings = list(strings(inputs))
        report['entryInActualInput'] = any(entry['content'] in text or json.dumps(entry['content'], ensure_ascii=False) in text for text in actual_strings)
        report['inputCaptured'] = any(inputs)
        def in_actual(value):
            return any(value in text or json.dumps(value, ensure_ascii=False) in text for text in actual_strings)
        report['historyInActualInput'] = in_actual(source)
        baselines = await db.fetch_all("SELECT kind,body FROM continuation_material_baselines WHERE book_id=?", [book])
        report['materialKindsInActualInput'] = sorted({row['kind'] for row in baselines if in_actual(row['body'])})
        report['toolCalls'] = [call for e in events if e['event_type']=='tool.calls_started' for call in json.loads(e['payload_json']).get('calls', [])]
        report['evidence'] = [r for e in opened for r in e.get('contextEvidence', [])]
        report['writingChapters'] = await db.fetch_all("SELECT c.id,c.title FROM outline_chapters c JOIN outlines o ON o.id=c.outline_id WHERE o.book_id=? AND o.type='writing'", [book])
        report['createdChapters'] = [json.loads(e['payload_json']) for e in events if e['event_type']=='writing.chapter_created']
        report['chapterProposals'] = [json.loads(e['payload_json']) for e in events if e['event_type']=='writing.proposed_chapter_diff']
        report['providerUsage'] = [json.loads(e['payload_json']) for e in events if e['event_type']=='provider.usage']
        report['callEvents'] = [json.loads(e['payload_json']) for e in events if e['event_type']=='model.call_recorded']
        (args.output / f'writing-events-{result.run_id}.json').write_text(json.dumps(events, ensure_ascii=False, indent=2))
        # Fail closed: diagnostics alone cannot count as a completed run.
        report['jointPassed'] = (result.status.value in {'completed', 'done'} and report['entryInActualInput']
            and report['historyInActualInput'] and {'character', 'background'} <= set(report['materialKindsInActualInput'])
            and bool(report['createdChapters']) and any(item.get('proposedText', '').strip() for item in report['chapterProposals']))
    except Exception as error:
        report.update(status='failed', errorType=type(error).__name__, error=str(error))
    finally:
        report['elapsedSeconds'] = round(time.monotonic()-started, 2)
        (args.output / ('writing-only-report.json' if args.writing_only else 'report.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if task and not task.done():
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        await composition.shutdown()
        await db.close()
        print(json.dumps({key: report.get(key) for key in ['status','jointPassed','runId','errorType','elapsedSeconds']}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--writing-only', action='store_true')
    parser.add_argument('--new-continuation', action='store_true', help='Create a fresh continuation from the existing real analysis before writing')
    parser.add_argument('--model', default='kimi-k2.6')
    parser.add_argument('--settings-db', type=Path, default=Path.home() / 'Library/Application Support/purrtypos/purrtypos.db')
    asyncio.run(main(parser.parse_args()))
