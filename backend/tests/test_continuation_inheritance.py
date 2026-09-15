"""Joint inheritance contracts against isolated SQLite and real file stores."""
import json
from pathlib import Path
import pytest

from tests.test_continuations import db, _published_analysis
from application.continuation_service import ContinuationService
from application.continuation_context import ContinuationContextService
from application.creation_material_service import materials
from application.writing_technique_access import WritingTechniqueAccess
from application.writing_technique_service import WritingTechniqueService
from database.crud.characters import get_characters, update_character, delete_character
from database.crud.setting_entities import get_setting_entities
from database.crud.articles import save_article
from domains.writing.techniques import TechniqueError
from exceptions import AppError


async def source(db):
    revision = await _published_analysis(db)
    await db.execute("UPDATE novel_source_analysis_facts SET fact_kind='character_state' WHERE id='fact-before'")
    await db.execute("UPDATE novel_source_analyses SET coverage_end_ordinal=0 WHERE id='analysis-1'")
    service = WritingTechniqueService(db)
    draft = await service.create_draft(operation_id='library', owner={'analysisId': 'analysis-1', 'sourceRevisionId': revision['id']})
    draft = await service.apply_changes(draft['techniqueId'], draft['draftId'], expected_revision=0, operation_id='content', changes=[{'action': 'put', 'path': 'SKILL.md', 'content': '---\nname: 行动留白\ndescription: 先呈现动作，再保留结果。\n---\n使用动作推进场景；结尾停在尚未确定的选择。\n辅助文件按需读取：[节奏](节奏.md)'}, {'action': 'put', 'path': '节奏.md', 'content': '不用每一段都制造悬念。'}])
    draft = await service.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'], expected_tree_digest=draft['treeDigest'], operation_id='seal')
    ref = draft['sealedRef']
    await service.publish('technique', ref['id'], ref=ref, expected_published_head=None, operation_id='publish')
    return revision, ref


async def create(db, revision, operation='create', **overrides):
    service = ContinuationService(db)
    preview = await service.preview_canon(source_revision_id=revision['id'], source_analysis_id='analysis-1', fork_section_id=revision['sections'][0]['id'])
    kwargs = dict(title='红门之后', source_revision_id=revision['id'], source_analysis_id='analysis-1', fork_section_id=revision['sections'][0]['id'], expected_snapshot_digest=preview['snapshotDigest'], operation_id=operation)
    kwargs.update(overrides)
    return await service.create_continuation(**kwargs)


async def test_joint_creation_freezes_defaults_files_history_and_exact_entry(db):
    revision, ref = await source(db)
    one = await create(db, revision)
    book = one['book']['id']
    assert (await create(db, revision))['book']['id'] == book
    with pytest.raises(AppError, match='同一创建操作'):
        await create(db, revision, title='改变名称')
    assert one['inheritance']['defaultTechniques'] == [ref]
    library = WritingTechniqueService(db)
    await db.execute("INSERT INTO ai_sessions(id,book_id) VALUES (101,?)", [book])
    await library.initialize_session('101', book)
    access = WritingTechniqueAccess(db)
    reserved = await access.reserve_input(operation_id='queued', book_id=book, session_id='101', mode='manual', manual=None)
    await library.set_selection('session', '101', [])
    snapshot = await access.load_input(reserved['inputId'], book, '101')
    assert snapshot['manual'][0]['ref'] == ref
    assert snapshot['manual'][0]['grantId']
    entry = await access.read(snapshot, ref, 'SKILL.md', max_characters=5000)
    assert '行动留白' in entry['content']
    with pytest.raises(TechniqueError, match='先读取'):
        await access.read(snapshot, ref, '节奏.md', max_characters=5000)
    next_input = await access.reserve_input(operation_id='next', book_id=book, session_id='101', mode='manual', manual=None)
    assert not (await access.load_input(next_input['inputId'], book, '101'))['manual']
    await library.initialize_session('101', book)
    assert await library.get_selection('session', '101') == []
    await db.execute("INSERT INTO ai_sessions(id,book_id) VALUES (102,?)", [book])
    await library.initialize_session('102', book)
    assert await library.get_selection('session', '102') == [ref]
    with pytest.raises(TechniqueError, match='继承快照'):
        await access.revoke(book, snapshot['manual'][0]['grantId'])
    with pytest.raises(TechniqueError, match='继承快照'):
        await library.set_status('technique', ref['id'], 'archived', operation_id='archive')
    history = ContinuationContextService(db)
    page = await history.list_source_sections(book_id=book, limit=1)
    assert page['total'] == 1 and page['nextOffset'] is None
    assert page['items'][0]['nodeKind'] == 'source_section'
    for identity in [revision['sections'][0]['id'], 'source:' + book + ':' + revision['sections'][0]['id']]:
        with pytest.raises(AppError, match='只读'):
            await save_article(db, identity, '覆盖')
    with pytest.raises(AppError):
        await history.read_source_section(book_id=book, section_id=revision['sections'][1]['id'])
    first = (await get_characters(db, book))[0]
    assert first['profile_md'] == '' and '红门' in first['inheritedBaseline']
    assert '钥匙' not in first['inheritedBaseline']
    changed = await update_character(db, first['id'], {'profile_md': '伤口已经康复', 'baseRevision': first['baseRevision']})
    assert changed['inheritedBaseline'] == first['inheritedBaseline']
    with pytest.raises(AppError, match='不能删除'):
        await delete_character(db, first['id'], base_revision=changed['baseRevision'])
    second = await create(db, revision, operation='second')
    assert (await get_characters(db, second['book']['id']))[0]['profile_md'] == ''
    await db.execute("UPDATE novel_source_sections SET text_content='新来源' WHERE id=?", [revision['sections'][0]['id']])
    assert '甲打开红门' in (await history.read_source_section(book_id=book, section_id=revision['sections'][0]['id']))['text']


async def test_plot_materials_are_not_setting_entities_and_overview_is_not_canon(db):
    revision = await _published_analysis(db)
    await db.execute("UPDATE novel_source_analyses SET summary_json=? WHERE id='analysis-1'", [json.dumps({'storyOverview': {'summaryMarkdown': '乙以后拿走钥匙'}})])
    service = ContinuationService(db)
    preview = await service.preview_canon(source_revision_id=revision['id'], source_analysis_id='analysis-1', fork_section_id=revision['sections'][0]['id'])
    assert len(preview['records']) == 1
    assert preview['startingPoint']['sourceFactIds'] == ['fact-before']
    assert preview['materialMapping'][0]['kind'] == 'plot'
    created = await create(db, revision)
    book = created['book']['id']
    assert not await get_setting_entities(db, book)
    rows = await db.fetch_all("SELECT body,records_json FROM continuation_material_baselines WHERE book_id=? AND kind='plot'", [book])
    assert len(rows) == 1
    assert '红门' in rows[0]['body'] and '钥匙' not in rows[0]['body']
    assert '甲打开红门。' not in rows[0]['body']
    assert '"evidence"' not in rows[0]['records_json']


async def test_creation_file_failure_rolls_back_and_retry_recovers(db, monkeypatch):
    revision, ref = await source(db)
    from infrastructure.obsidian.material_transaction import MaterialFileChange
    original = MaterialFileChange.prepare
    attempts = 0
    def fail(self):
        nonlocal attempts
        attempts += 1
        original(self)
        if attempts == 2:
            raise OSError('injected file publication failure')
    monkeypatch.setattr(MaterialFileChange, 'prepare', fail)
    with pytest.raises(OSError, match='injected'):
        await create(db, revision)
    assert not await db.fetch_all('SELECT * FROM continuation_operations')
    assert not await db.fetch_all("SELECT * FROM books WHERE creation_mode='continuation'")
    assert not await db.fetch_all('SELECT * FROM continuation_source_sections')
    monkeypatch.setattr(MaterialFileChange, 'prepare', original)
    book = (await create(db, revision))['book']['id']
    assert (await get_characters(db, book))[0]['inheritedBaseline']


async def test_external_baseline_tamper_rejected_and_development_editable(db):
    revision, _ = await source(db)
    book = (await create(db, revision))['book']['id']
    root = Path((await materials(db).status(book))['directory'])
    path = next((root / '人物').glob('*.md'))
    raw = path.read_bytes()
    path.write_bytes(raw.replace('红门'.encode(), '蓝门'.encode()))
    with pytest.raises(AppError, match='继承基线'):
        await get_characters(db, book)
    path.write_bytes(raw)
    assert '红门' in (await get_characters(db, book))[0]['inheritedBaseline']


async def test_full_backup_restore_inherits_body_materials_and_permission(db, tmp_path):
    from application.project_backup import build_project_backup, extract_project_backup
    from routers.files import _replace_project_data
    from database.connection import DatabaseConnection
    revision, ref = await source(db)
    book = (await create(db, revision))['book']['id']
    root = db.get_db_path().parent
    archive = await db.export_with_resources(lambda data: build_project_backup(data, root / 'memory-component-v1', root / 'writing-library', root / 'creation-materials'))
    extracted = extract_project_backup(archive, tmp_path / 'extracted')
    target = DatabaseConnection(tmp_path / 'restored-db')
    await target.init()
    try:
        await _replace_project_data(target, database_path=extracted.database_path, component_path=None, technique_path=extracted.technique_path, material_path=extracted.material_path, data_dir=target.get_db_path().parent)
        assert '红门' in (await get_characters(target, book))[0]['inheritedBaseline']
        assert '甲打开红门' in (await ContinuationContextService(target).read_source_section(book_id=book, section_id=revision['sections'][0]['id']))['text']
        assert await WritingTechniqueService(target).get_selection('book', book) == [ref]
        assert (await WritingTechniqueAccess(target).candidates(book))[0]['ref'] == ref
    finally:
        await target.close()


async def test_no_technique_does_not_block_creation_and_manifest_still_validates(db):
    revision = await _published_analysis(db)
    one = await create(db, revision)
    assert one['inheritance']['defaultTechniques'] == []
    service = ContinuationService(db)
    before = await service.preview_canon(source_revision_id=revision['id'], source_analysis_id='analysis-1', fork_section_id=revision['sections'][0]['id'])
    await db.execute("UPDATE novel_source_sections SET content_digest='changed' WHERE id=?", [revision['sections'][0]['id']])
    with pytest.raises(AppError, match='预览已变化'):
        await create(db, revision, operation='new', expected_snapshot_digest=before['snapshotDigest'])


@pytest.mark.parametrize('fork_index,total,chapter_count', [(1,2,1),(3,4,2)])
async def test_multivolume_midpoint_and_end_directory_export(db, fork_index, total, chapter_count):
    from application.novel_source_service import NovelSourceService
    from routers.continuations import export_continuation
    content = '# 第一卷 风起\n\n## 第一章\n甲守着桥。\n\n# 第二卷 潮来\n\n## 第二章\n乙渡过河。'
    sources = NovelSourceService(db)
    preview = sources.preview_external_import(file_name='volumes.md', extension='.md', content=content)
    revision = await sources.confirm_external_import(title='两卷原作', file_name='volumes.md', extension='.md', content=content, expected_content_digest=preview['contentDigest'], confirm_single_section=False, rights_confirmed=True, model_data_boundary_confirmed=True)
    await db.execute("INSERT INTO novel_source_analyses(id,source_revision_id,version_no,coverage_end_ordinal,schema_version,content_digest) VALUES ('vol-analysis',?,1,3,1,'analysis')", [revision['id']])
    service = ContinuationService(db)
    kwargs = dict(source_revision_id=revision['id'], source_analysis_id='vol-analysis', fork_section_id=revision['sections'][fork_index]['id'])
    manifest = await service.preview_canon(**kwargs)
    created = await service.create_continuation(**kwargs, title='两卷续写', operation_id='volume-case', expected_snapshot_digest=manifest['snapshotDigest'])
    book = created['book']['id']
    history = ContinuationContextService(db)
    page = await history.list_source_sections(book_id=book, limit=2)
    assert page['total'] == total and page['chapterCount'] == chapter_count
    assert page['items'][0]['sectionType'] == 'volume'
    assert page['items'][1]['locator']['volumeTitle'] == '第一卷 风起'
    if fork_index == 3:
        page = await history.list_source_sections(book_id=book, offset=page['nextOffset'], limit=2)
        assert page['items'][1]['locator']['volumeTitle'] == '第二卷 潮来'
        assert page['nextOffset'] is None
    else:
        with pytest.raises(AppError):
            await history.read_source_section(book_id=book, section_id=revision['sections'][3]['id'])
    assert len((await export_continuation(book))['data']['entries']) == total
    assert (await export_continuation(book, includeHistory=False))['data']['entries'] == []
    with pytest.raises(AppError, match='完整章节末尾'):
        await service.preview_canon(**{**kwargs,'fork_section_id':revision['sections'][0]['id']})


async def test_restart_after_sql_commit_recovers_files_and_idempotent_creation(db):
    import asyncio, sys
    revision, ref = await source(db)
    code = '''import asyncio,os,sys
from pathlib import Path
from database.connection import DatabaseConnection
from tests.test_continuation_inheritance import create
from infrastructure.obsidian.material_transaction import MaterialFileChange
import json
async def run():
 db=DatabaseConnection(Path(sys.argv[1]))
 await db.init()
 MaterialFileChange.committed=lambda self: os._exit(23)
 await create(db,json.loads(sys.argv[2]))
asyncio.run(run())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(db.get_db_path().parent), json.dumps(revision), cwd=str(Path(__file__).resolve().parents[1]), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    assert process.returncode == 23, stderr.decode()
    from application.creation_material_service import recover_materials
    await recover_materials(db)
    book = (await create(db, revision))['book']['id']
    assert len(await db.fetch_all('SELECT * FROM continuation_operations')) == 1
    assert '红门' in (await get_characters(db, book))[0]['inheritedBaseline']
    assert await WritingTechniqueService(db).get_selection('book', book) == [ref]


async def test_source_links_and_editable_plot_conversion(db):
    revision = await _published_analysis(db)
    # Reuse a real evidence-backed fact to check each supported projection.
    await db.execute("UPDATE novel_source_analysis_facts SET fact_kind='foreshadowing', value_json=?, lifecycle_status='active' WHERE id='fact-before'",
                     [json.dumps('[[故事背景]]中的红门尚未解开，[[不存在的角色]]未确认')])
    created = await create(db, revision)
    book = created['book']['id']
    outline = await db.fetch_one("SELECT markdown_content FROM outlines WHERE book_id=? AND type='global'", [book])
    assert '# 续写起点与待推进事项' in outline['markdown_content']
    assert '[[资料/背景/' in outline['markdown_content']
    assert '[[不存在的角色]]' not in outline['markdown_content']
    assert '钥匙' not in outline['markdown_content']
    rows = await db.fetch_all("SELECT * FROM story_memory_records WHERE book_id=? AND kind='plot_thread'", [book])
    assert len(rows) == 1 and rows[0]['status'] == 'confirmed'
    assert json.loads(rows[0]['payload_json'])['openedChapterId'] == f"source:{book}:{revision['sections'][0]['id']}"
    assert json.loads(rows[0]['payload_json']).get('expectedResolutionChapterId') is None
    binding = await materials(db).binding(book)
    root = materials(db).directory(binding)
    files = list((root / '原作情节').glob('*.md'))
    assert len(files) == 1 and '[[资料/背景/' in files[0].read_text()
    assert (await create(db, revision))['book']['id'] == book
    assert len(await db.fetch_all("SELECT memory_key FROM story_memory_records WHERE book_id=? AND kind='plot_thread'", [book])) == 1

    await db.execute("UPDATE novel_source_analysis_facts SET lifecycle_status='resolved' WHERE id='fact-before'")
    resolved = await create(db, revision, operation='resolved')
    assert not await db.fetch_all("SELECT memory_key FROM story_memory_records WHERE book_id=? AND kind='plot_thread'", [resolved['book']['id']])


@pytest.mark.parametrize(('fact_kind', 'material_kind'), [('background', 'background'), ('world_rule', 'entity')])
async def test_analysis_background_and_world_settings_match_material_destination(db, fact_kind, material_kind):
    revision = await _published_analysis(db)
    await db.execute("UPDATE novel_source_analysis_facts SET fact_kind=? WHERE id='fact-before'", [fact_kind])
    created = await create(db, revision)
    book = created['book']['id']
    baseline = await db.fetch_one('SELECT kind,entity_id,body FROM continuation_material_baselines WHERE book_id=?', [book])
    assert baseline['kind'] == material_kind
    mapping = await db.fetch_one('SELECT path FROM creation_material_files WHERE book_id=? AND kind=? AND entity_id=?', [book, material_kind, baseline['entity_id']])
    assert mapping['path'].startswith('背景/' if fact_kind == 'background' else '设定/')
    assert '红门' in baseline['body']


async def test_canonical_character_material_keeps_creation_fields_during_inheritance(db):
    revision = await _published_analysis(db)
    value = {"name": "林澈", "tags": "主角, 守门人", "profile_md": "## 基本信息\n负责看守红门。"}
    await db.execute(
        "UPDATE novel_source_analysis_facts SET fact_kind='character_summary', "
        "subject_key='林澈', predicate='人物归纳', value_json=? WHERE id='fact-before'",
        [json.dumps(value, ensure_ascii=False)],
    )
    created = await create(db, revision, operation='structured-character')
    character = (await get_characters(db, created['book']['id']))[0]
    assert character['name'] == '林澈'
    assert character['tags'] == '主角, 守门人'
    assert character['profile_md'] == ''
    assert '负责看守红门' in character['inheritedBaseline']


async def test_explicit_no_techniques_omits_available_source_defaults(db):
    revision, ref = await source(db)
    created = await create(db, revision, use_source_techniques=False)
    assert created['inheritance']['defaultTechniques'] == []
    assert await WritingTechniqueAccess(db).candidates(created['book']['id']) == []


async def test_candidate_is_copied_to_usable_default_and_retry_is_idempotent(db, monkeypatch):
    from application.source_analysis_techniques import register, results
    revision = await _published_analysis(db)
    await db.execute("UPDATE novel_source_analyses SET coverage_end_ordinal=0 WHERE id='analysis-1'")
    library = WritingTechniqueService(db)
    draft = await library.create_draft(operation_id='candidate', storage_scope='analysis_candidate', owner={'analysisId':'analysis-1'})
    draft = await library.apply_changes(draft['techniqueId'], draft['draftId'], expected_revision=0, operation_id='candidate-content', changes=[{'action':'put','path':'SKILL.md','content':'---\nname: 留白\ndescription: 以动作留白。\n---\n使用动作推进场景。'}])
    sealed = await library.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'], expected_tree_digest=draft['treeDigest'], operation_id='candidate-seal')
    ref = sealed['sealedRef']
    await register(db,'analysis-1',ref,'candidate')
    before = await results(db,'analysis-1',0,include_candidates=True)
    assert before[0]['available'] and before[0]['name']=='留白'
    assert not (await results(db,'analysis-1',-1,include_candidates=True))[0]['available']
    assert not (await results(db,'analysis-1',0))[0]['available']
    from infrastructure.obsidian.material_transaction import MaterialFileChange
    original = MaterialFileChange.prepare
    def fail_file(change):
        raise RuntimeError('candidate creation interrupted')
    monkeypatch.setattr(MaterialFileChange, 'prepare', fail_file)
    with pytest.raises(RuntimeError, match='interrupted'):
        await create(db,revision)
    assert await results(db,'analysis-1',0,include_candidates=True)==before
    monkeypatch.setattr(MaterialFileChange, 'prepare', original)
    created = await create(db,revision)
    inherited = created['inheritance']['defaultTechniques']
    assert len(inherited)==1 and inherited[0]['id']!=ref['id']
    assert inherited[0]['versionId']==ref['versionId']
    assert (await WritingTechniqueAccess(db)._expand(inherited[0],verify_files=True))['metadata']['name']=='留白'
    assert (await library.get_object('technique',ref['id']))['storageScope']=='analysis_candidate'
    assert await results(db,'analysis-1',0,include_candidates=True)==before
    assert (await create(db,revision))['book']['id']==created['book']['id']
    omitted = await create(db,revision,operation='omit-candidate',use_source_techniques=False)
    assert omitted['inheritance']['defaultTechniques']==[]
