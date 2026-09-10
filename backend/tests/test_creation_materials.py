from pathlib import Path
import pytest
from database.connection import DatabaseConnection
from database.crud.characters import create_character, get_characters, update_character
from database.crud.setting_entities import create_setting_entity, get_setting_entities, update_setting_entity
from database.crud.story_background import get_story_background, save_story_background
from application.creation_material_service import materials
from infrastructure.obsidian.material_document import decode, edit
from exceptions import AppError


@pytest.fixture
async def db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books(id,title) VALUES ('pilot','测试作品')")
    yield db
    await db.close()


async def migrate(db):
    svc = materials(db)
    preview = await svc.preview('pilot')
    status = await svc.migrate('pilot', preview['sourceRevision'])
    return Path(status['directory'])


@pytest.mark.asyncio
async def test_three_entry_roundtrip_and_external_conflict(db):
    char = await create_character(db, 'pilot', {'name': '沈青', 'tags': '主角', 'profile_md': '# 档案\n[[城门]] ^bio\n'})
    ent = await create_setting_entity(db, 'pilot', {'name': '城门', 'entity_type': 'location', 'profile_md': '红色'})
    await save_story_background(db, 'pilot', '雾城')
    root = await migrate(db)
    row = (await get_characters(db, 'pilot'))[0]
    assert row['profile_md'] == char['profile_md']
    changed = await update_character(db, row['id'], {'profile_md': '项目修改', 'baseRevision': row['baseRevision']})
    path = next((root / '人物').glob('*.md'))
    assert decode(path.read_bytes(), str(path))['body'] == '项目修改'
    path.write_bytes(edit(path.read_bytes(), str(path), {'profile_md': 'Obsidian 修改'}))
    with pytest.raises(AppError):
        await update_character(db, row['id'], {'profile_md': '过期覆盖', 'baseRevision': changed['baseRevision']})
    fresh = (await get_characters(db, 'pilot'))[0]
    assert fresh['profile_md'] == 'Obsidian 修改'
    entity = (await get_setting_entities(db, 'pilot'))[0]
    await update_setting_entity(db, ent['id'], {'profile_md': '蓝色', 'baseRevision': entity['baseRevision']})
    bg = await get_story_background(db, 'pilot')
    await save_story_background(db, 'pilot', '新的雾城', base_revision=bg['baseRevision'])
    assert decode(next((root / '背景').glob('*.md')).read_bytes(), '背景.md')['body'] == '新的雾城'


@pytest.mark.asyncio
async def test_transaction_rollback_keeps_file_and_projection(db):
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '旧'})
    root = await migrate(db)
    before = (await get_characters(db, 'pilot'))[0]
    with pytest.raises(RuntimeError):
        async with db.transaction():
            await update_character(db, before['id'], {'profile_md': '新', 'baseRevision': before['baseRevision']})
            raise RuntimeError('history insertion failed')
    assert (await get_characters(db, 'pilot'))[0]['profile_md'] == '旧'
    assert decode(next((root / '人物').glob('*.md')).read_bytes(), '人物.md')['body'] == '旧'


@pytest.mark.asyncio
async def test_rename_duplicate_and_missing_fail_closed(db):
    await create_character(db, 'pilot', {'name': '沈青'})
    root = await migrate(db)
    path = next((root / '人物').glob('*.md'))
    moved = path.with_name('沈青.md')
    path.rename(moved)
    assert (await get_characters(db, 'pilot'))[0]['name'] == '沈青'
    duplicate = moved.with_name('副本.md')
    duplicate.write_bytes(moved.read_bytes())
    with pytest.raises(AppError):
        await get_characters(db, 'pilot')
    duplicate.unlink()
    moved.unlink()
    with pytest.raises(AppError):
        await get_characters(db, 'pilot')


@pytest.mark.asyncio
async def test_prepare_failure_rolls_back_published_files_and_sql(db, monkeypatch):
    from infrastructure.obsidian.material_transaction import MaterialFileChange
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '原始'})
    root = await migrate(db)
    row = (await get_characters(db, 'pilot'))[0]
    original = MaterialFileChange.prepare
    def failed(change):
        original(change)
        raise OSError('simulated failure after publish')
    monkeypatch.setattr(MaterialFileChange, 'prepare', failed)
    with pytest.raises(OSError):
        await update_character(db, row['id'], {'profile_md': '未提交', 'baseRevision': row['baseRevision']})
    assert decode(next((root / '人物').glob('*.md')).read_bytes(), '人物.md')['body'] == '原始'
    assert (await get_characters(db, 'pilot'))[0]['profile_md'] == '原始'


@pytest.mark.asyncio
async def test_external_save_in_publish_window_is_preserved(db, monkeypatch):
    import os
    from infrastructure.obsidian import material_transaction as tx
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '原始'})
    root = await migrate(db)
    row = (await get_characters(db, 'pilot'))[0]
    path = next((root / '人物').glob('*.md'))
    external = edit(path.read_bytes(), str(path), {'profile_md': '同时保存的外部版本'})
    original = os.rename
    def race(src, dst, **kwargs):
        result = original(src, dst, **kwargs)
        if str(dst).endswith('.before'):
            path.write_bytes(external)
        return result
    monkeypatch.setattr(tx.os, 'rename', race)
    with pytest.raises(AppError):
        await update_character(db, row['id'], {'profile_md': '项目版本', 'baseRevision': row['baseRevision']})
    assert path.read_bytes() == external
    assert (await get_characters(db, 'pilot'))[0]['profile_md'] == '同时保存的外部版本'


@pytest.mark.asyncio
async def test_restart_recovers_uncommitted_file_publication(db):
    from infrastructure.obsidian.material_transaction import MaterialFileChange
    from application.creation_material_service import recover_materials
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '原始'})
    root = await migrate(db)
    path = next((root / '人物').glob('*.md'))
    raw = path.read_bytes()
    change = MaterialFileChange(root, str(path.relative_to(root)), raw, edit(raw, str(path), {'profile_md': '未提交'}))
    change.prepare()
    await recover_materials(db)
    assert path.read_bytes() == raw
    await recover_materials(db)
    assert path.read_bytes() == raw


@pytest.mark.asyncio
async def test_migration_preview_revision_and_idempotency(db):
    svc = materials(db)
    before = await svc.preview('pilot')
    await create_character(db, 'pilot', {'name': '新人物'})
    with pytest.raises(AppError):
        await svc.migrate('pilot', before['sourceRevision'])
    preview = await svc.preview('pilot')
    one = await svc.migrate('pilot', preview['sourceRevision'])
    assert one == await svc.migrate('pilot', preview['sourceRevision'])
    assert len(list(Path(one['directory']).rglob('*.md'))) == 2


@pytest.mark.asyncio
async def test_navigation_targets_registered_vault_root(db):
    root = await migrate(db)
    result = await materials(db).navigation('pilot')
    assert result['path'] == str(root.parent)
    assert result['vaultPath'] == str(root.parent)
    assert result['uri'].startswith('obsidian://open?path=')
    assert '%2F' in result['uri']


@pytest.mark.asyncio
async def test_backup_includes_files_and_missing_resources_rejected(db, tmp_path):
    from application.project_backup import build_project_backup, extract_project_backup, ProjectBackupError
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '原始'})
    root = await migrate(db)
    data = await db.export_to_buffer()
    with pytest.raises(ProjectBackupError):
        build_project_backup(data, tmp_path / 'no-memory')
    archive = build_project_backup(data, tmp_path / 'no-memory', material_root=materials(db).root)
    extracted = extract_project_backup(archive, tmp_path / 'restored')
    assert list(extracted.material_path.rglob('*.md'))


def test_unknown_properties_links_blocks_and_body_roundtrip():
    from infrastructure.obsidian.material_document import create, render
    raw = create('id', 'character', {'name': '人物', 'profile_md': '\n[[城门]]\n^block\n'})
    doc = decode(raw, '人物.md')
    doc['metadata']['aliases'] = ['旧名']
    doc['metadata']['custom_note'] = '备注'
    raw = render(doc['metadata'], doc['body'])
    changed = edit(raw, '人物.md', {'name': '新名'})
    result = decode(changed, '人物.md')
    assert result['body'] == doc['body']
    assert result['metadata']['aliases'] == ['旧名']
    assert result['metadata']['custom_note'] == '备注'


@pytest.mark.asyncio
async def test_delete_and_restore_same_identity(db):
    from database.crud.characters import delete_character
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '原始'})
    root = await migrate(db)
    row = (await get_characters(db, 'pilot'))[0]
    await delete_character(db, row['id'], base_revision=row['baseRevision'])
    assert await get_characters(db, 'pilot') == []
    assert not list((root / '人物').glob('*.md'))
    status = await materials(db).status('pilot')
    await materials(db).restore('pilot', status['deleted'][0]['id'])
    restored = (await get_characters(db, 'pilot'))[0]
    assert restored['id'] == row['id'] and restored['profile_md'] == '原始'


@pytest.mark.asyncio
async def test_tool_proposal_uses_file_revision_and_stale_accept_rejected(db, monkeypatch):
    from types import SimpleNamespace
    from infrastructure.writing.tools.handlers.character_tools import _tool_update_character
    from routers import setting_diff
    from schemas.setting_diff import CommitCharacterDiffRequest
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '原始'})
    root = await migrate(db)
    row = (await get_characters(db, 'pilot'))[0]
    chunks = []
    await _tool_update_character(SimpleNamespace(db=db), {'bookId': 'pilot'}, {'characterId': row['id'], 'profileMd': '模型建议'}, chunks.append)
    proposal = chunks[0]['proposedSettingDiff']
    assert proposal['baseRevision'] == row['baseRevision']
    monkeypatch.setattr(setting_diff, 'get_db', lambda: db)
    body = CommitCharacterDiffRequest(name='沈青', profileMd='模型建议', before=proposal['before'], after=proposal['proposed'], baseRevision=proposal['baseRevision'])
    await setting_diff.commit_character_diff(str(row['id']), body)
    path = next((root / '人物').glob('*.md'))
    assert decode(path.read_bytes(), path.name)['body'] == '模型建议'
    path.write_bytes(edit(path.read_bytes(), path.name, {'profile_md': '外部更新'}))
    with pytest.raises(AppError):
        await setting_diff.commit_character_diff(str(row['id']), body)
    assert decode(path.read_bytes(), path.name)['body'] == '外部更新'


@pytest.mark.asyncio
async def test_scoped_retrieval_and_revision_invalidation(db):
    from application.novel_knowledge_service import get_novel_knowledge_service
    from infrastructure.obsidian.material_document import render
    from domains.writing.knowledge import KnowledgeError
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '沈青在雾城的秘密'})
    root = await migrate(db)
    svc = get_novel_knowledge_service(db)
    await svc.refresh('pilot')
    assert not (await svc.search('pilot', '沈青', mode='fulltext'))['items']
    path = next((root / '人物').glob('*.md'))
    doc = decode(path.read_bytes(), path.name)
    doc['metadata'].update(temporal_scope='global', known_to=['*'])
    path.write_bytes(render(doc['metadata'], doc['body']))
    result = await svc.search('pilot', '沈青', mode='fulltext')
    assert len(result['items']) == 1
    old_scope = await svc.scope_snapshot('pilot')
    path.write_bytes(edit(path.read_bytes(), path.name, {'profile_md': '修改后'}))
    with pytest.raises(KnowledgeError, match='material_source_changed'):
        await svc.check_scope('pilot', old_scope)


@pytest.mark.asyncio
async def test_same_name_other_book_and_symlink_cannot_claim_material(db, tmp_path):
    from database.crud.characters import get_characters
    await create_character(db, 'pilot', {'name': '沈青'})
    root = await migrate(db)
    await db.execute("INSERT INTO books(id,title) VALUES ('other','其他作品')")
    other = await create_character(db, 'other', {'name': '沈青', 'profile_md': '其他作品资料'})
    path = next((root / '人物').glob('*.md'))
    path.unlink()
    outside = tmp_path / 'outside.md'
    outside.write_text('禁止跟随')
    path.symlink_to(outside)
    with pytest.raises(AppError):
        await get_characters(db, 'pilot')
    assert (await get_characters(db, 'other'))[0]['id'] == other['id']
    assert outside.read_text() == '禁止跟随'


@pytest.mark.asyncio
async def test_full_backup_restore_installs_authoritative_files(db, tmp_path):
    from application.project_backup import build_project_backup, extract_project_backup
    from routers.files import _replace_project_data
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '恢复内容'})
    await migrate(db)
    archive = build_project_backup(await db.export_to_buffer(), tmp_path / 'no-memory', material_root=materials(db).root)
    extracted = extract_project_backup(archive, tmp_path / 'unpacked')
    target = DatabaseConnection(tmp_path / 'target')
    await target.init()
    try:
        await _replace_project_data(target, database_path=extracted.database_path, component_path=None,
                                    material_path=extracted.material_path, data_dir=tmp_path / 'target')
        row = (await get_characters(target, 'pilot'))[0]
        assert row['profile_md'] == '恢复内容'
        await update_character(target, row['id'], {'profile_md': '恢复后保存', 'baseRevision': row['baseRevision']})
        assert (await get_characters(target, 'pilot'))[0]['profile_md'] == '恢复后保存'
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_database_only_restore_does_not_write_existing_files(db):
    await create_character(db, 'pilot', {'name': '沈青'})
    root = await migrate(db)
    original = {str(p): p.read_bytes() for p in root.rglob('*.md')}
    await db.import_from_buffer(await db.export_to_buffer())
    with pytest.raises(AppError, match='完整备份'):
        await get_characters(db, 'pilot')
    assert original == {str(p): p.read_bytes() for p in root.rglob('*.md')}


@pytest.mark.asyncio
async def test_migration_does_not_follow_storage_root_symlink(db, tmp_path):
    outside = tmp_path / 'outside-storage'
    outside.mkdir()
    svc = materials(db)
    svc.root.symlink_to(outside, target_is_directory=True)
    preview = await svc.preview('pilot')
    with pytest.raises(OSError):
        await svc.migrate('pilot', preview['sourceRevision'])
    assert list(outside.iterdir()) == []
    assert await svc.binding('pilot') is None


@pytest.mark.asyncio
async def test_new_book_starts_with_markdown_without_migration_action(db, monkeypatch):
    from routers import books
    from schemas.books import CreateBookRequest
    monkeypatch.setattr(books, 'get_db', lambda: db)
    result = await books.create_book(CreateBookRequest(title='新作品'))
    book = result['data']['id']
    state = await materials(db).status(book)
    assert state['mode'] == 'markdown'
    character = await create_character(db, book, {'name': '新人物', 'profile_md': '新人物资料'})
    assert character['baseRevision']
    root = Path(state['directory'])
    assert decode(next((root / '人物').glob('*.md')).read_bytes(), '人物.md')['body'] == '新人物资料'
    assert next((root / '背景').glob('*.md')).is_file()


@pytest.mark.asyncio
async def test_default_material_links_share_file_and_database(db):
    root = await migrate(db)
    target = await create_character(db, 'pilot', {'name': '方崇安'})
    source = await create_character(db, 'pilot', {'name': '张会', 'profile_md': '好友：[[方崇安]]。普通文字方崇安不改。'})
    assert target['materialLink'] in source['profile_md']
    assert '普通文字方崇安不改' in source['profile_md']
    path = next(p for p in (root / '人物').glob('*.md') if '张会' in p.name)
    assert decode(path.read_bytes(), str(path))['body'] == source['profile_md']
    await save_story_background(db, 'pilot', '主角：[[张会]]', base_revision=(await get_story_background(db, 'pilot'))['baseRevision'])
    assert source['materialLink'] in (await get_story_background(db, 'pilot'))['content']
    place = await create_setting_entity(db, 'pilot', {'name': '城门', 'entity_type': 'location', 'profile_md': '守卫：[[张会]]'})
    assert source['materialLink'] in place['profile_md']
    updated = await update_character(db, source['id'], {'profile_md': '无此关系。', 'baseRevision': source['baseRevision']})
    assert '[[' not in updated['profile_md']
    assert (await get_characters(db, 'pilot'))[0]['profile_md'] == ''


@pytest.mark.asyncio
async def test_material_links_ambiguity_isolation_and_renames(db):
    root = await migrate(db)
    a = await create_character(db, 'pilot', {'name': '同名'})
    await create_character(db, 'pilot', {'name': '同名'})
    with pytest.raises(AppError, match='同名'):
        await create_character(db, 'pilot', {'name': '新人物', 'profile_md': '好友：[[同名]]'})
    explicit = await create_character(db, 'pilot', {'name': '明确', 'profile_md': a['materialLink']})
    assert explicit['profile_md'] == a['materialLink']
    await db.execute("INSERT INTO books(id,title) VALUES ('other','其他作品')")
    await create_character(db, 'other', {'name': '别书人物'})
    foreign = await create_character(db, 'pilot', {'name': '隔离', 'profile_md': '[[别书人物]]'})
    assert foreign['profile_md'] == '[[别书人物]]'
    target_path = next(p for p in (root / '人物').glob('明确--*.md'))
    target_path.rename(target_path.with_name('重命名.md'))
    linked = await create_character(db, 'pilot', {'name': '引用', 'profile_md': '[[明确]]'})
    assert '[[资料/人物/重命名|明确]]' == linked['profile_md']


def test_material_link_literals_aliases_and_anchors():
    from infrastructure.obsidian.material_links import resolve_links
    entries = [{'title': '张会', 'path': '人物/张会--123.md'}]
    body = '[[张会#经历|老张]] `[[张会]]`\n```md\n[[张会]]\n```\n<!-- [[张会]] -->\n\\[[张会]]'
    result = resolve_links(body, entries)
    assert result.startswith('[[资料/人物/张会--123#经历|老张]]')
    assert result.endswith('`[[张会]]`\n```md\n[[张会]]\n```\n<!-- [[张会]] -->\n\\[[张会]]')
    assert resolve_links(result, entries) == result


@pytest.mark.asyncio
async def test_agent_relation_preview_is_linked_but_not_applied(db):
    from types import SimpleNamespace
    from infrastructure.writing.tools.handlers.character_tools import _tool_update_character
    from application.novel_knowledge_service import get_novel_knowledge_service
    import json
    await migrate(db)
    target = await create_character(db, 'pilot', {'name': '好友'})
    source = await create_character(db, 'pilot', {'name': '主角', 'profile_md': '原资料'})
    events = []
    result = await _tool_update_character(SimpleNamespace(db=db), {'bookId': 'pilot'},
        {'characterId': source['id'], 'profileMd': '朋友：[[好友]]'}, events.append)
    diff = next(e['proposedSettingDiff'] for e in events if 'proposedSettingDiff' in e)
    assert diff['proposed']['profileMd'] == '朋友：' + target['materialLink']
    assert (await get_characters(db, 'pilot'))[1]['profile_md'] == '原资料'
    await update_character(db, source['id'], {'profile_md': diff['proposed']['profileMd'], 'baseRevision': diff['baseRevision']})
    await get_novel_knowledge_service(db).refresh('pilot')
    rows = await db.fetch_all('SELECT links_json FROM novel_knowledge_links')
    links = [link for row in rows for link in json.loads(row['links_json'])]
    assert any(link['state'] == 'resolved' and '好友' in link['path'] for link in links)


def test_source_links_only_connect_unique_existing_materials():
    from infrastructure.obsidian.material_links import resolve_source_links
    entries = [{'title': '甲', 'path': '人物/甲--one.md'},
               {'title': '同名', 'path': '人物/同名--two.md'},
               {'title': '同名', 'path': '设定/同名--three.md'}]
    body = '[[甲]]与[[未知|来客]]、[[同名]]；`[[甲]]`。'
    assert resolve_source_links(body, entries) == '[[资料/人物/甲--one|甲]]与来客、同名；`[[甲]]`。'
    assert resolve_source_links('[[资料/人物/甲--one#经历|他]]', entries) == '[[资料/人物/甲--one#经历|他]]'
