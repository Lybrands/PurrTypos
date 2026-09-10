"""Read the frozen R01 source and analyze it in an isolated local database."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('technique_evaluation', ROOT / 'scripts/evaluate-writing-techniques.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

from application.composition_factory import create_agent_composition
from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from application.novel_analysis_service import NovelAnalysisService
from application.novel_source_service import NovelSourceService
from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


async def main(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Use a new empty evidence directory')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.synthetic_case:
        cases, sources = evaluation.fixtures()
        use_case = next(case for case in cases['cases'] if case['id'] == args.synthetic_case)
        fixture = sources[use_case['generatorInput']['sourceIds'][0]]
        source = fixture['text']
        source_digest = fixture['sha256']
        focus = use_case['generatorInput']['focus']
        label = args.synthetic_case
        evaluation.write(args.output / 'frozen-review-input.json', {'source': fixture, 'case': use_case})
    else:
        with sqlite3.connect(args.settings_db.resolve().as_uri() + '?mode=ro', uri=True) as connection:
            connection.execute('PRAGMA query_only=ON')
            row = connection.execute('SELECT text_content FROM novel_source_sections WHERE id=? AND revision_id=?', ('iS61KjqU', 'cEyyAuho')).fetchone()
        source = row[0]
        source_digest = hashlib.sha256(source.encode()).hexdigest()
        if source_digest != 'e6d921b0f170673658d1513dc0ecc8dd0b52c47431d1d64c9ef8c7dde52c566e':
            raise ValueError('source_changed')
        # Freeze local review evidence before sending anything to the model.
        observations = []
        for key, statement, excerpt in [
            ('R01-O1', '按人物抬头、看见、怀疑、触摸、敲击的次序逐步提供异常及核验信息；叙述未解释异常的世界规则。', '也许是眼花了？'),
            ('R01-O2', '触觉与敲击所得的正常结果和刚才看见的消失并置，使人物无法用眼花的猜测完全消解异常。', '但那个男人确实不见了。'),
            ('R01-O3', '章末停在人物主动靠近、闭眼准备承受后果的动作上，没有给出尝试是否成功的结果。', '墙壁近在咫尺，林月闭上眼睛，准备迎接撞击的疼痛。'),
        ]:
            start = source.index(excerpt)
            observations.append({'id': key, 'statement': statement, 'sourceSha256': source_digest, 'start': start, 'end': start + len(excerpt), 'excerpt': excerpt})
        use_case = {'id': 'R01', 'independentUse': [{'id': 'R01-U1', 'task': '写一小段夜班仓管的场景：他看到货箱标签上的日期自行改变，先尝试常规解释，再亲自核验，核验未消除矛盾。他决定进行一次新的尝试；在尝试结果出现前结束。不要使用穿墙、神祇或梦境。'}]}
        evaluation.write(args.output / 'frozen-review-input.json', {'sourceRef': {'revisionId': 'cEyyAuho', 'sectionId': 'iS61KjqU', 'sha256': source_digest},
            'observations': observations, 'independentUse': use_case['independentUse'],
            'rejectIf': ['把本章剧情或器物当作必须沿用的模板', '解释来源没有给出的异常规则', '保证必然产生悬念效果']})
        focus = '重点提炼日常场景出现异常后，信息如何按人物感知逐步揭示，以及章末怎样安排尚未完成的行动。'
        label = 'R01'
    requested = set(args.independent_use_id or [])
    available = {item['id'] for item in use_case.get('independentUse', [])}
    if not requested <= available:
        raise ValueError('Unknown independent use ID: ' + ', '.join(sorted(requested - available)))
    use_case = {**use_case, 'independentUse': [item for item in use_case.get('independentUse', []) if item['id'] in requested]}
    evaluation.write(args.output / 'selected-independent-use.json', use_case['independentUse'])
    if args.freeze_only:
        print('Frozen observations locally; no credentials read and no model calls', flush=True)
        return
    config = evaluation.configured_model(args.settings_db, args.model)
    runtime = ScreenplayAgentRuntimeRequest(apiKey=config['apiKey'], baseURL=config['baseUrl'], apiProvider=config['apiProvider'],
        options={'model': config['name'], 'model_profile': config['presetId'], 'profile_binding': 'compatible',
                 'thinking': {'type': 'enabled'}, 'max_generation_tokens': 16384}, contextWindow=config['contextWindow'])
    db = DatabaseConnection(args.output)
    await db.init()
    composition = create_agent_composition(db)
    report = {'sourceSha256': source_digest, 'model': config['name'], 'status': 'running', 'contentReview': 'not_executed'}
    task = None
    try:
        source_service = NovelSourceService(db)
        preview = source_service.preview_external_import(file_name=label + '.txt', extension='.txt', content=source)
        revision = await source_service.confirm_external_import(title=label + ' 验收副本', file_name=label + '.txt', extension='.txt', content=source,
            expected_content_digest=preview['contentDigest'], confirm_single_section=True, rights_confirmed=True, model_data_boundary_confirmed=True)
        service = NovelAnalysisService(db, composition)
        command = label + '-' + uuid.uuid4().hex
        task = service.dispatch(source_revision_id=revision['id'], section_ids=tuple(section['id'] for section in revision['sections']),
            task_idempotency_key=command, run_command_id=command, prompt=focus,
            failed_resume_attempts=0, runtime=runtime)
        await asyncio.wait_for(asyncio.shield(task), 900)
        view = (await service.list_for_revision(revision['id']))[0]
        report.update(view)
        report['status'] = view['taskStatus']
        if view.get('artifactRef'):
            artifact = await NovelAnalysisArtifactStore(db).require(view['artifactRef'])
            evaluation.write(args.output / 'analysis-artifact.json', artifact)
            candidate = artifact['techniqueResult'].get('candidate')
            if candidate:
                library = WritingTechniqueService(db)
                files = library.techniques._version_files({'kind': 'technique', 'id': candidate['techniqueId'], 'versionId': candidate['versionId']})
                evaluation.write(args.output / 'files.json', files)
                if use_case['independentUse']:
                    await evaluation.independent_use(config, use_case, files, args.output)
    except Exception as error:
        report.update(status='failed', errorType=type(error).__name__, errorCode=getattr(error, 'code', ''))
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        evaluation.write(args.output / 'report.json', report)
        await composition.shutdown()
        await db.close()
        print(json.dumps({'status': report['status'], 'runId': report.get('runId'), 'errorCode': report.get('errorCode')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='kimi-k2.6')
    parser.add_argument('--synthetic-case', choices=['E01', 'E02', 'E03', 'E04', 'E05', 'E06', 'E07a', 'E07b', 'E08'])
    parser.add_argument('--freeze-only', action='store_true')
    parser.add_argument('--independent-use-id', action='append', default=[], help='Explicitly selected writing request IDs; omitted means analysis only')
    parser.add_argument('--settings-db', type=Path, default=Path.home() / 'Library/Application Support/purrtypos/purrtypos.db')
    asyncio.run(main(parser.parse_args()))
