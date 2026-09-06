"""Create an isolated disposable book and Vault; never accepts an existing destination."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from database.connection import DatabaseConnection
from application.novel_knowledge_service import get_novel_knowledge_service


async def main():
    root = Path(tempfile.mkdtemp(prefix='purrtypos-obsidian-pilot-'))
    vault = root / 'Vault' / '雾城来信'
    vault.mkdir(parents=True)
    samples = {
        '红门.md': '---\npurr_id: red-door\ntype: world\nstatus: confirmed\ntemporal_scope: global\nknown_to: ["*"]\naliases: [朱门]\n---\n# 红门\n\n红门只能用铜钥匙打开。[[人物/沈青]] 每晚都会经过这里。',
        '人物/沈青.md': '---\npurr_id: shen-qing\ntype: character\nstatus: confirmed\ntemporal_scope: global\nknown_to: ["*"]\n---\n# 沈青\n\n沈青是雾城邮差。',
        '未来秘密.md': '---\npurr_id: future-secret\ntype: timeline\nstatus: confirmed\ntemporal_scope: chapter_range\nvalid_from_chapter: pilot-chapter-3\nknown_to: [shen-qing]\nknowledge_from_chapter: pilot-chapter-3\n---\n# 地下钟楼\n\n沈青到第三章才知道地下钟楼的存在。',
        '待定灵感.md': '---\npurr_id: idea\ntype: reference\nstatus: draft\ntemporal_scope: global\n---\n也许整座城是梦。',
        '布局.canvas': json.dumps({'nodes': [{'id': 'canvas-only', 'type': 'text', 'text': '仅在 Canvas 中出现的紫色鲸鱼', 'x': 0, 'y': 0, 'width': 300, 'height': 200}], 'edges': []}, ensure_ascii=False),
    }
    for name, value in samples.items():
        file = vault / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value)
    db = DatabaseConnection(root / 'data')
    await db.init()
    svc = get_novel_knowledge_service(db)
    try:
        await db.execute("INSERT INTO books(id,title) VALUES ('knowledge-pilot','资料库试点 · 雾城来信')")
        await db.execute("INSERT INTO outlines(id,title,type,book_id) VALUES ('pilot-writing','正文','writing','knowledge-pilot')")
        for i in range(1, 4):
            await db.execute('INSERT INTO outline_chapters(id,outline_id,title,sort) VALUES (?,?,?,?)', [f'pilot-chapter-{i}', 'pilot-writing', f'第{i}章', i])
        await svc.initialize(db._data_dir)
        await svc.bind_selected('knowledge-pilot', root=str(vault), expected_version=0, command_id='pilot-binding')
        await svc.configure('knowledge-pilot', expected_version=1, command_id='pilot-scope', scope={'purpose': 'prose', 'chapterId': 'pilot-chapter-1'})
        print(json.dumps({'root': str(root), 'dataDirectory': str(root / 'data'), 'vaultDirectory': str(root / 'Vault'), 'novelDirectory': str(vault), 'bookId': 'knowledge-pilot'}, ensure_ascii=False))
    finally:
        await svc.close()
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
