import json
from pathlib import Path

import pytest

from application.project_backup import build_project_backup, extract_project_backup, ProjectBackupError
from application.writing_technique_service import WritingTechniqueService
from application.writing_technique_access import WritingTechniqueAccess
from application.writing_technique_exchange import upload_technique
from database.connection import DatabaseConnection
from database.writing_technique_retirement import retire_writing_method_schema, RETIREMENT_ID, LEGACY_TABLES
from routers.files import _replace_project_data
from tests.support.legacy_writing_methods import create_legacy_tables
from tests.support.writing_techniques import ENTRY


async def test_retirement_is_atomic_exact_and_preserves_history(tmp_path, monkeypatch):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await db.execute('DELETE FROM app_schema_migrations WHERE id=?', [RETIREMENT_ID])
        await create_legacy_tables(db)
        await db.execute("INSERT INTO writing_methods(id,name,method_type,draft_markdown) VALUES ('old','旧技法','technique','旧正文')")
        await db.execute("INSERT INTO books(id,title) VALUES ('book','保留小说')")
        await db.execute("INSERT INTO ai_agent_runs(id,status,binding_attributes_json) VALUES ('old-run','done',?)", [json.dumps({'writingMethodBindingSnapshot': {'catalog': [{'revisionId': 'old'}]}})])
        before = await db.fetch_all('SELECT * FROM ai_agent_runs')
        execute = db.execute
        async def fail(sql, parameters=()):
            if sql == 'DROP TABLE "writing_methods"':
                raise OSError('interrupted retirement')
            return await execute(sql, parameters)
        monkeypatch.setattr(db, 'execute', fail)
        with pytest.raises(OSError):
            await retire_writing_method_schema(db)
        assert not await db.fetch_one('SELECT id FROM app_schema_migrations WHERE id=?', [RETIREMENT_ID])
        assert await db.fetch_one("SELECT name FROM writing_methods WHERE id='old'") == {'name': '旧技法'}
        monkeypatch.setattr(db, 'execute', execute)
        await retire_writing_method_schema(db)
        assert not set(LEGACY_TABLES) & {row['name'] for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        service = WritingTechniqueService(db)
        draft = await service.create_draft(operation_id='after-retirement')
        await retire_writing_method_schema(db)
        assert (await service.get_object('technique', draft['techniqueId']))['draftHead'] == draft['draftId']
        assert await db.fetch_all('SELECT * FROM ai_agent_runs') == before
        assert await db.fetch_one("SELECT title FROM books WHERE id='book'") == {'title': '保留小说'}
    finally:
        await db.close()


async def test_full_backup_restores_drafts_uploads_scheme_grants_and_atomic_rollback(tmp_path, monkeypatch):
    source, target = DatabaseConnection(tmp_path / 'source'), DatabaseConnection(tmp_path / 'target')
    await source.init(); await target.init()
    try:
        await source.execute("INSERT INTO books(id,title) VALUES ('book','来源小说')")
        await source.execute("INSERT INTO ai_sessions(id,book_id,scope) VALUES (1,'book','setting')")
        service = WritingTechniqueService(source)
        draft = await service.create_draft(operation_id='draft')
        draft = await service.apply_changes(draft['techniqueId'], draft['draftId'], expected_revision=0, operation_id='files', changes=[
            {'action': 'put', 'path': 'SKILL.md', 'content': ENTRY + '\n[展开](辅助.md)'}, {'action': 'put', 'path': '辅助.md', 'content': '辅助正文'}])
        draft = await service.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'], expected_tree_digest=draft['treeDigest'], operation_id='seal')
        ref = draft['sealedRef']
        await service.publish('technique', ref['id'], ref=ref, expected_published_head=None, operation_id='publish')
        scheme = await service.create_draft(kind='scheme', operation_id='scheme', content={'schemaVersion': 1, 'name': '组合', 'description': '组合说明', 'composition': '按场景采用', 'members': [ref]})
        scheme = await service.seal('scheme', scheme['schemeId'], scheme['draftId'], expected_revision=0, expected_tree_digest=scheme['treeDigest'], operation_id='scheme-seal')
        await service.publish('scheme', scheme['schemeId'], ref=scheme['sealedRef'], expected_published_head=None, operation_id='scheme-publish')
        access = WritingTechniqueAccess(source)
        grant = await access.grant('book', scheme['sealedRef'])
        uploaded = await upload_technique(service, files={'SKILL.md': ENTRY}, operation_id='upload', book_id='book', session_id='1')
        reserved = await access.reserve_input(operation_id='request', book_id='book', session_id='1', mode='manual', manual=[uploaded['ref']])
        archive = await source.export_with_resources(lambda data: build_project_backup(data, source.get_db_path().parent / 'memory-component-v1', service.root))
        extracted = extract_project_backup(archive, tmp_path / 'extracted')
        assert extracted.technique_path is not None
        await _replace_project_data(target, database_path=extracted.database_path, component_path=None, technique_path=extracted.technique_path, data_dir=target.get_db_path().parent)
        restored = WritingTechniqueAccess(target)
        assert (await restored.library.read_version_file(ref, '辅助.md'))['content'] == '辅助正文'
        assert (await restored.candidates('book'))[0]['grantId'] == grant['grantId']
        assert (await restored.load_input(reserved['inputId'], 'book', '1'))['manual'][0]['ref'] == uploaded['ref']
        assert len(await restored.library.list_objects('scheme')) == 1
        await target.execute("UPDATE books SET title='恢复前保留' WHERE id='book'")
        original = target._open_imported_connection
        attempts = 0
        async def fail_once():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError('injected reopen failure')
            await original()
        monkeypatch.setattr(target, '_open_imported_connection', fail_once)
        extracted = extract_project_backup(archive, tmp_path / 'failed-restore')
        with pytest.raises(OSError):
            await _replace_project_data(target, database_path=extracted.database_path, component_path=None, technique_path=extracted.technique_path, data_dir=target.get_db_path().parent)
        assert await target.fetch_one("SELECT title FROM books WHERE id='book'") == {'title': '恢复前保留'}
        assert (await restored.library.read_version_file(ref, '辅助.md'))['content'] == '辅助正文'
    finally:
        await source.close(); await target.close()


async def test_old_database_import_retires_tables_before_connection_is_exposed(tmp_path):
    source, target = DatabaseConnection(tmp_path / 'old'), DatabaseConnection(tmp_path / 'new')
    await source.init(); await target.init()
    try:
        await source.execute('DELETE FROM app_schema_migrations WHERE id=?', [RETIREMENT_ID])
        await create_legacy_tables(source)
        await source.execute("INSERT INTO books(id,title) VALUES ('book','旧备份小说')")
        await target.import_from_buffer(await source.export_to_buffer())
        names = {row['name'] for row in await target.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not set(LEGACY_TABLES) & names
        assert 'writing_technique_grants' in names
        assert await target.fetch_one("SELECT title FROM books WHERE id='book'") == {'title': '旧备份小说'}
    finally:
        await source.close(); await target.close()
