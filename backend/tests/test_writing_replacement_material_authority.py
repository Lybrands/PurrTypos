"""Bound-book material authority for the replacement Writing Agent.

共享资料（Obsidian vault）绑定的作品上，Agent 的资料读写必须走文件权威：
读取携带文件版本 baseRevision 与 materialLink，写入经过链接规范化、
文件版本 CAS、两阶段文件提交与基线保护。
"""
from pathlib import Path

import pytest

from agents.writing.material_write_model import (
    SqliteWritingMaterialRepository,
    WritingMaterialMutationError,
)
from agents.writing.read_model import SqliteWritingReadRepository, WritingReadScope
from application.creation_material_service import materials
from database.connection import DatabaseConnection
from database.crud.characters import create_character
from database.crud.setting_entities import create_setting_entity
from infrastructure.obsidian.material_document import decode, edit


@pytest.fixture
async def db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books(id,title) VALUES ('pilot','测试作品')")
    yield db
    await db.close()


async def migrate(db) -> Path:
    svc = materials(db)
    preview = await svc.preview('pilot')
    status = await svc.migrate('pilot', preview['sourceRevision'])
    return Path(status['directory'])


async def _mapping(db, kind: str, entity_id) -> dict:
    return await db.fetch_one(
        "SELECT * FROM creation_material_files WHERE book_id=? AND kind=? AND entity_id=?",
        ['pilot', kind, str(entity_id)],
    )


@pytest.mark.asyncio
async def test_bound_reads_carry_file_revision_and_material_link(db):
    char = await create_character(db, 'pilot', {'name': '沈青', 'tags': '主角', 'profile_md': '档案'})
    ent = await create_setting_entity(db, 'pilot', {'name': '灯塔', 'entity_type': 'location', 'profile_md': '红色'})
    await migrate(db)

    reads = SqliteWritingReadRepository(db)
    scope = WritingReadScope('pilot')

    detail = await reads.characters(scope, include_profile=True)
    item = detail['items'][0]
    mapping = await _mapping(db, 'character', char['id'])
    assert item['baseRevision'] == mapping['revision']
    assert item['materialLink'].startswith('[[资料/人物/沈青--')
    assert item['materialLink'].endswith('|沈青]]')
    assert item['materialId'] == mapping['material_id']

    listing = await reads.characters(scope)
    assert listing['items'][0]['materialLink'] == item['materialLink']

    entities = await reads.setting_entities(scope, include_profile=True)
    entity_mapping = await _mapping(db, 'entity', ent['id'])
    assert entities['items'][0]['baseRevision'] == entity_mapping['revision']
    assert entities['items'][0]['materialLink'].startswith('[[资料/设定/灯塔--')

    background = await reads.story_background(scope)
    assert background['background']['materialLink'].startswith('[[资料/背景/故事背景--')


@pytest.mark.asyncio
async def test_bound_update_commits_file_and_rejects_stale_revision(db):
    char = await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '旧档案'})
    root = await migrate(db)
    reads = SqliteWritingReadRepository(db)
    writes = SqliteWritingMaterialRepository(db)
    scope = WritingReadScope('pilot')

    base = (await reads.characters(scope, include_profile=True))['items'][0]['baseRevision']
    receipt = await writes.commit_character(
        scope, character_id=char['id'], base_revision=base,
        patch={'profileMd': 'Agent 修改'},
    )
    path = next((root / '人物').glob('*.md'))
    assert decode(path.read_bytes(), str(path))['body'] == 'Agent 修改'
    mapping = await _mapping(db, 'character', char['id'])
    assert receipt['committedRevision'] == mapping['revision']
    # 回执版本可直接作为下一次更新的 baseRevision。
    again = await writes.commit_character(
        scope, character_id=char['id'], base_revision=receipt['committedRevision'],
        patch={'profileMd': 'Agent 再次修改'},
    )
    assert decode(path.read_bytes(), str(path))['body'] == 'Agent 再次修改'

    # 外部（Obsidian）编辑后，携带旧版本写入必须被拒绝而不是覆盖。
    path.write_bytes(edit(path.read_bytes(), str(path), {'profile_md': 'Obsidian 修改'}))
    with pytest.raises(WritingMaterialMutationError) as caught:
        await writes.commit_character(
            scope, character_id=char['id'], base_revision=again['committedRevision'],
            patch={'profileMd': '过期覆盖'},
        )
    assert caught.value.code == 'writing_character_revision_conflict'
    assert decode(path.read_bytes(), str(path))['body'] == 'Obsidian 修改'


@pytest.mark.asyncio
async def test_bound_update_normalizes_model_written_links(db):
    await create_character(db, 'pilot', {'name': '沈青', 'profile_md': '档案'})
    ent = await create_setting_entity(db, 'pilot', {'name': '灯塔', 'entity_type': 'location', 'profile_md': '红色'})
    root = await migrate(db)
    reads = SqliteWritingReadRepository(db)
    writes = SqliteWritingMaterialRepository(db)
    scope = WritingReadScope('pilot')

    items = (await reads.characters(scope, include_profile=True))['items']
    target = next(item for item in items if item['name'] == '沈青')

    # 唯一目标：模型手写的裸链接被规范化为权威 materialLink。
    await writes.commit_character(
        scope, character_id=target['id'], base_revision=target['baseRevision'],
        patch={'profileMd': '住在 [[灯塔]] 附近'},
    )
    mapping = await _mapping(db, 'entity', ent['id'])
    expected = f'[[资料/设定/灯塔--{mapping["material_id"][:12]}|灯塔]]'
    char_row = await db.fetch_one("SELECT profile_md FROM characters WHERE name='沈青'")
    assert char_row['profile_md'] == f'住在 {expected} 附近'

    # 同名（人物与设定同名）：拒绝而不是猜选。
    await create_character(db, 'pilot', {'name': '灯塔', 'profile_md': '同名人物'})
    base = (await reads.characters(scope, include_profile=True))['items'][0]['baseRevision']
    with pytest.raises(WritingMaterialMutationError) as caught:
        await writes.commit_character(
            scope, character_id=target['id'], base_revision=base,
            patch={'profileMd': '又提到 [[灯塔]]'},
        )
    assert caught.value.code == 'writing_material_link_ambiguous'


@pytest.mark.asyncio
async def test_bound_create_registers_material_file(db):
    root = await migrate(db)
    reads = SqliteWritingReadRepository(db)
    writes = SqliteWritingMaterialRepository(db)
    scope = WritingReadScope('pilot')

    receipt = await writes.commit_create_setting_entity(
        scope, name='钟楼', entity_type='location', profile_md='报时',
    )
    mapping = await _mapping(db, 'entity', receipt['targetId'])
    assert mapping is not None
    assert receipt['committedRevision'] == mapping['revision']
    path = root / mapping['path']
    assert path.exists()
    assert decode(path.read_bytes(), str(path))['body'] == '报时'
    # 创建回执的版本即后续读取与更新的 baseRevision。
    fresh = (await reads.setting_entities(scope, include_profile=True))['items'][0]
    assert fresh['baseRevision'] == receipt['committedRevision']


@pytest.mark.asyncio
async def test_bound_delete_respects_baseline_and_moves_to_trash(db):
    ent = await create_setting_entity(db, 'pilot', {'name': '灯塔', 'entity_type': 'location', 'profile_md': '红色'})
    plain = await create_setting_entity(db, 'pilot', {'name': '钟楼', 'entity_type': 'other', 'profile_md': ''})
    await db.execute(
        "INSERT INTO continuation_material_baselines (book_id,kind,entity_id,source_key,body,records_json) "
        "VALUES ('pilot','entity',?,'source-1','原作继承的灯塔设定','[]')",
        [str(ent['id'])],
    )
    root = await migrate(db)
    reads = SqliteWritingReadRepository(db)
    writes = SqliteWritingMaterialRepository(db)
    scope = WritingReadScope('pilot')

    items = (await reads.setting_entities(scope, include_profile=True))['items']
    by_name = {item['name']: item for item in items}
    assert by_name['灯塔']['inheritedBaseline'] == '原作继承的灯塔设定'

    with pytest.raises(WritingMaterialMutationError) as caught:
        await writes.commit_delete_setting_entity(
            scope, entity_id=ent['id'], base_revision=by_name['灯塔']['baseRevision'],
        )
    assert caught.value.code == 'writing_material_baseline_protected'

    await writes.commit_delete_setting_entity(
        scope, entity_id=plain['id'], base_revision=by_name['钟楼']['baseRevision'],
    )
    assert await _mapping(db, 'entity', plain['id']) is None
    assert not (root / '设定' / '其他').glob('钟楼--*.md') or not any(
        (root / '设定' / '其他').glob('钟楼--*.md')
    )
    trash = await db.fetch_one(
        "SELECT * FROM creation_material_trash WHERE book_id='pilot'"
    )
    assert trash is not None


@pytest.mark.asyncio
async def test_bound_background_edit_preserves_inherited_baseline(db):
    await db.execute(
        "INSERT INTO continuation_material_baselines (book_id,kind,entity_id,source_key,body,records_json) "
        "VALUES ('pilot','background','pilot','source-1','旧作背景','[]')"
    )
    await db.execute("INSERT INTO story_background(book_id,content) VALUES ('pilot','本书背景')")
    root = await migrate(db)
    reads = SqliteWritingReadRepository(db)
    writes = SqliteWritingMaterialRepository(db)
    scope = WritingReadScope('pilot')

    background = (await reads.story_background(scope))['background']
    assert background['inheritedBaseline'] == '旧作背景'
    assert background['content'] == '本书背景'

    receipt = await writes.commit_story_background(
        scope, content='新的本书背景', base_revision=background['baseRevision'],
        clear_content=False,
    )
    path = next((root / '背景').glob('*.md'))
    doc = decode(path.read_bytes(), str(path))
    assert doc['body'].startswith('# 原作继承基线（只读）\n\n旧作背景\n\n# 本书后续发展\n\n')
    assert doc['body'].endswith('新的本书背景')
    fresh = (await reads.story_background(scope))['background']
    assert fresh['content'] == '新的本书背景'
    assert fresh['baseRevision'] == receipt['committedRevision']
