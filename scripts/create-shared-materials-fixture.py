"""Create only a fresh temporary project for shared Markdown acceptance."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from database.connection import DatabaseConnection
from database.crud.characters import create_character
from database.crud.setting_entities import create_setting_entity
from database.crud.story_background import save_story_background
from application.creation_material_service import materials
from application.novel_knowledge_service import get_novel_knowledge_service


async def main():
    directory = Path(tempfile.mkdtemp(prefix='purr-shared-materials-')).resolve()
    db = DatabaseConnection(directory)
    await db.init()
    knowledge = get_novel_knowledge_service(db)
    try:
        await knowledge.initialize(directory)
        await db.execute('INSERT INTO books(id,title) VALUES (?,?)', ['shared-pilot', '共享资料测试作品'])
        await create_character(db, 'shared-pilot', {'name': '沈青', 'tags': '测试人物', 'profile_md': '# 人物档案\n\n这是临时验收资料。沈青住在雾城。\n'})
        await create_setting_entity(db, 'shared-pilot', {'name': '雾城', 'entity_type': 'location', 'profile_md': '临时测试地点。'})
        await save_story_background(db, 'shared-pilot', '临时故事背景。')
        preview = await materials(db).preview('shared-pilot')
        status = await materials(db).migrate('shared-pilot', preview['sourceRevision'])
        await knowledge.refresh('shared-pilot')
        output = {'dataDirectory': str(directory), 'bookId': 'shared-pilot', 'materialsDirectory': status['directory'], 'obsidianVault': str(Path(status['directory']).parent)}
        (directory / 'fixture.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        await knowledge.close()
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
