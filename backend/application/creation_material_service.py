"""Novel material authority: explicit per-book migration and shared file CRUD."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from exceptions import AppError
from domains.writing.knowledge import KnowledgeError
from infrastructure.obsidian import material_document as document
from infrastructure.obsidian.material_transaction import MaterialFileChange, pending, revision, ensure_directory
from infrastructure.obsidian.reader import scan

TABLES = {'character': ('characters', 'id'), 'entity': ('setting_entities', 'id'), 'background': ('story_background', 'book_id')}


class CreationMaterialService:
    def __init__(self, db):
        self.db = db
        self.root = db.get_db_path().parent.resolve() / 'creation-materials'

    async def binding(self, book):
        row = await self.db.fetch_one('SELECT * FROM creation_material_books WHERE book_id=?', [str(book)])
        if row and row['state'] != 'active':
            raise AppError('共享资料未随数据库恢复，请使用包含资料文件的完整备份恢复。', 409)
        return row

    def directory(self, binding):
        name = binding['directory']
        if not name or any(c not in '0123456789abcdef' for c in name):
            raise AppError('资料目录登记无效', 409)
        return self.root / name / '资料'

    async def status(self, book):
        binding = await self.binding(book)
        return {'mode': 'markdown' if binding else 'database',
                'directory': str(self.directory(binding)) if binding else None,
                'deleted': [{'id': r['material_id'], 'name': json.loads(r['row_json']).get('name', '故事背景')} for r in await self.db.fetch_all('SELECT material_id,row_json FROM creation_material_trash WHERE book_id=?', [book])]}

    async def navigation(self, book):
        binding = await self.binding(book)
        if not binding:
            raise AppError('共享资料尚未初始化', 409)
        vault = self.directory(binding).parent
        if not vault.is_dir():
            raise AppError('共享资料目录不可用', 503)
        return {'uri': 'obsidian://open?path=' + quote(str(vault), safe=''), 'path': str(vault), 'vaultPath': str(vault)}

    async def snapshot(self, book):
        if not await self.db.fetch_one('SELECT id FROM books WHERE id=?', [book]):
            raise AppError('作品不存在', 404)
        result = []
        for kind, (table, key) in TABLES.items():
            rows = await self.db.fetch_all(f'SELECT * FROM {table} WHERE book_id=? ORDER BY {key}', [book])
            if kind == 'background' and not rows:
                rows = [{'book_id': book, 'content': ''}]
            for row in rows:
                result.append({'kind': kind, 'entityId': str(row[key]), 'row': row})
        return result

    async def preview(self, book):
        if await self.binding(book):
            raise AppError('此作品已经使用共享 Markdown', 409)
        rows = await self.snapshot(book)
        fingerprint = revision(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode())
        return {'sourceRevision': fingerprint, 'count': len(rows),
                'items': [{'kind': x['kind'], 'id': x['entityId'], 'name': x['row'].get('name', '故事背景')} for x in rows],
                'scopeNotice': '保留已有内容；尚未填写章节和知情范围的资料不会自动成为全书公开事实。'}

    async def migrate(self, book, expected, *, source_links=None):
        async with self.db.transaction():
            existing = await self.binding(book)
            if existing:
                if existing['migration_revision'] == expected:
                    return await self.status(book)
                raise AppError('作品资料已经迁移', 409)
            preview = await self.preview(book)
            if preview['sourceRevision'] != expected:
                raise AppError('资料在预览后发生变化，请重新预览', 409)
            knowledge = await self.db.fetch_one("SELECT * FROM novel_knowledge_bindings WHERE book_id=? AND state!='unbound'", [book])
            if knowledge:
                raise AppError('请先解除原只读目录绑定，再迁移本作品资料；原笔记不会删除。', 409)
            directory = uuid4().hex
            binding = {'book_id': book, 'directory': directory}
            root = self.directory(binding)
            ensure_directory(self.root)
            ensure_directory(root.parent, exclusive=True)
            ensure_directory(root, exclusive=True)
            await self.db.execute('INSERT INTO creation_material_books(book_id,directory,migration_revision) VALUES (?,?,?)', [book, directory, expected])
            items = await self.snapshot(book)
            layouts = [self.layout(item['kind'], item['row']) for item in items]
            entries = [*(source_links or []), *[
                {'path': layout[1], 'title': item['row'].get('name', '故事背景')}
                for item, layout in zip(items, layouts)]]
            for item, layout in zip(items, layouts):
                kind, entity_id, row = item['kind'], item['entityId'], item['row']
                if kind == 'background' and not await self.db.fetch_one('SELECT book_id FROM story_background WHERE book_id=?', [book]):
                    await self.db.execute('INSERT INTO story_background(book_id,content) VALUES (?,?)', [book, ''])
                await self.add(book, kind, entity_id, row, layout=layout, source_links=entries if source_links is not None else None)
            from application.memory_delivery import record_source_deletion
            heads = await self.db.fetch_all("SELECT source_id FROM memory_source_heads WHERE book_id=? AND deleted=0", [book])
            for head in heads:
                if head['source_id'].startswith(('character:', 'setting-entity:', 'story-background:', 'setting-diff:')):
                    await record_source_deletion(self.db, book_id=book, source_id=head['source_id'])
            from application.novel_knowledge_service import get_novel_knowledge_service
            svc = get_novel_knowledge_service(self.db)
            await self.db.execute('''INSERT INTO novel_knowledge_bindings
                (id,book_id,root,generation,state,version,host_id) VALUES (?,?,?,1,'active',1,?)
                ON CONFLICT(book_id) DO UPDATE SET root=excluded.root,generation=generation+1,
                version=version+1,state='active',host_id=excluded.host_id,scope_json='{}',semantic_config=NULL''',
                [uuid4().hex, book, str(root), svc._host_id])
        return await self.status(book)

    async def stage(self, root, path, before, after):
        change = MaterialFileChange(root, path, before, after)
        await self.db.execute('INSERT INTO creation_material_commits VALUES (?)', [change.id])
        self.db.enlist_file_change(change)
        return change.id

    @staticmethod
    def layout(kind, row):
        material_id = uuid4().hex
        directory = {'character': '人物', 'entity': '设定', 'background': '背景'}[kind]
        title = re.sub(r'[^\w\u3400-\u9fff -]', '_', str(row.get('name') or '故事背景'))[:60].strip() or '资料'
        path = f'{directory}/{title}--{material_id[:12]}.md'
        return material_id, path

    async def add(self, book, kind, entity_id, row, *, layout=None, source_links=None):
        binding = await self.binding(book)
        if not binding:
            return
        material_id, path = layout or self.layout(kind, row)
        root = self.directory(binding)
        ensure_directory(root / Path(path).parent)
        baseline = await self.db.fetch_one('SELECT body FROM continuation_material_baselines WHERE book_id=? AND kind=? AND entity_id=?', [book, kind, str(entity_id)])
        if baseline and source_links is not None:
            from infrastructure.obsidian.material_links import resolve_source_links
            baseline['body'] = resolve_source_links(baseline['body'], source_links)
            await self.db.execute('UPDATE continuation_material_baselines SET body=? WHERE book_id=? AND kind=? AND entity_id=?',
                                  [baseline['body'], book, kind, str(entity_id)])
        raw = document.create(material_id, kind, row, baseline=baseline['body'] if baseline else None)
        document.decode(raw, path)
        await self.stage(root, path, None, raw)
        await self.db.execute('INSERT INTO creation_material_files VALUES (?,?,?,?,?,?)',
                              [material_id, book, kind, str(entity_id), path, revision(raw)])

    async def synchronize(self, book):
        binding = await self.binding(book)
        if not binding:
            return
        root = self.directory(binding)
        for change in pending(root):
            receipt = await self.db.fetch_one('SELECT id FROM creation_material_commits WHERE id=?', [change.id])
            change.recover(bool(receipt))
        mappings = await self.db.fetch_all('SELECT * FROM creation_material_files WHERE book_id=?', [book])
        try:
            files, _ = scan(root)
            indexed = {}
            for path, raw in files.items():
                if not isinstance(raw, bytes):
                    raise ValueError(f'{path}: 文件不可用')
                parsed = document.parse(raw, path)
                identity = parsed['metadata'].get('purr_id')
                if identity:
                    indexed.setdefault(identity, []).append((path, raw))
            staged = {p.relative for p in self.db._file_participants if p.root == root}
            validated = []
            for mapping in mappings:
                if mapping['path'] in staged:
                    continue
                matches = indexed.get(mapping['material_id'], [])
                if len(matches) != 1:
                    raise ValueError(f"{mapping['path']}: 文件缺失或标识重复，请恢复文件或修正标识")
                path, raw = matches[0]
                parsed = document.decode(raw, path, material_id=mapping['material_id'], kind=mapping['kind'])
                baseline = await self.db.fetch_one('SELECT body FROM continuation_material_baselines WHERE book_id=? AND kind=? AND entity_id=?', [book, mapping['kind'], mapping['entity_id']])
                if (parsed['metadata'].get('purr_baseline') or None) != (baseline['body'] if baseline else None):
                    raise ValueError('原作继承基线不能修改')
                validated.append((mapping, path, raw, parsed))
        except (ValueError, OSError, KnowledgeError) as error:
            raise AppError(f'共享资料无法读取：{error}', 409) from error
        for mapping, path, raw, parsed in validated:
            if mapping['revision'] != revision(raw):
                table, key = TABLES[mapping['kind']]
                values = document.values(parsed)
                await self.db.execute(f'UPDATE {table} SET ' + ','.join(f'{k}=?' for k in values) + f' WHERE {key}=?',
                                      [*values.values(), mapping['entity_id']])
                # Revoke prior derived facts; unscoped file changes must not be
                # promoted into global memory outside knowledge admission.
                from services.memory_deposition_service import record_deleted_source
                source = {'character': 'character', 'entity': 'setting-entity', 'background': 'story-background'}[mapping['kind']]
                await record_deleted_source(self.db, book_id=book, source_base=f"{source}:{mapping['entity_id']}")
            await self.db.execute('UPDATE creation_material_files SET path=?,revision=? WHERE material_id=?',
                                  [path, revision(raw), mapping['material_id']])

    async def source_revision(self, book):
        if not await self.binding(book):
            return None
        async with self.db.transaction():
            await self.synchronize(book)
            rows = await self.db.fetch_all('SELECT material_id,revision FROM creation_material_files WHERE book_id=? ORDER BY material_id', [book])
            return revision(json.dumps(rows, sort_keys=True).encode())

    async def normalize_links(self, book, data):
        if not await self.binding(book):
            return dict(data)
        from infrastructure.obsidian.material_links import resolve_links
        entries = []
        for kind, (table, key) in TABLES.items():
            title = "'故事背景'" if kind == 'background' else 't.name'
            entries.extend(await self.db.fetch_all(
                f'SELECT m.path, {title} AS title FROM creation_material_files m '
                f'JOIN {table} t ON CAST(t.{key} AS TEXT)=m.entity_id AND t.book_id=m.book_id '
                'WHERE m.book_id=? AND m.kind=?', [book, kind]))
        result = dict(data)
        try:
            for field in ('profile_md', 'content'):
                if isinstance(result.get(field), str):
                    result[field] = resolve_links(result[field], entries)
        except ValueError as error:
            raise AppError(str(error), 409) from error
        return result

    async def annotate(self, book, kind, rows):
        mappings = await self.db.fetch_all('SELECT * FROM creation_material_files WHERE book_id=? AND kind=?', [book, kind])
        by_id = {m['entity_id']: m for m in mappings}
        key = TABLES[kind][1]
        for row in rows:
            mapping = by_id.get(str(row[key]))
            if mapping:
                row['baseRevision'] = mapping['revision']
                row['materialId'] = mapping['material_id']
                from infrastructure.obsidian.material_links import material_link
                try:
                    row['materialLink'] = material_link(mapping['path'], row.get('name', '故事背景'))
                except ValueError:
                    pass
            baseline = await self.db.fetch_one('SELECT body,records_json FROM continuation_material_baselines WHERE book_id=? AND kind=? AND entity_id=?', [book, kind, str(row[key])])
            if baseline:
                row['inheritedBaseline'] = baseline['body']
                row['inheritedEvidence'] = json.loads(baseline['records_json'])
        return rows

    async def update(self, book, kind, entity_id, data, expected, *, delete=False):
        binding = await self.binding(book)
        if not binding:
            return False
        await self.synchronize(book)
        mapping = await self.db.fetch_one('SELECT * FROM creation_material_files WHERE book_id=? AND kind=? AND entity_id=?', [book, kind, str(entity_id)])
        if not mapping:
            raise AppError('共享资料登记缺失', 409)
        if delete and await self.db.fetch_one('SELECT 1 FROM continuation_material_baselines WHERE book_id=? AND kind=? AND entity_id=?', [book, kind, str(entity_id)]):
            raise AppError('原作继承资料不能删除；可修改本书后续发展', 409)
        if expected != mapping['revision']:
            raise AppError('资料版本已变化或缺少基础版本，请重新读取后再保存。', 409)
        from infrastructure.obsidian.reader import read_stable
        root = self.directory(binding)
        raw = read_stable(root, mapping['path'])
        if revision(raw) != expected:
            raise AppError('资料正在被其他编辑器修改，请重试。', 409)
        after = None if delete else document.edit(raw, mapping['path'], data)
        operation_id = await self.stage(root, mapping['path'], raw, after)
        if delete:
            table, key = TABLES[kind]
            row = await self.db.fetch_one(f'SELECT * FROM {table} WHERE {key}=?', [entity_id])
            await self.db.execute('INSERT INTO creation_material_trash VALUES (?,?,?,?,?)', [mapping['material_id'], book, json.dumps(mapping), json.dumps(row), operation_id])
            await self.db.execute('DELETE FROM creation_material_files WHERE material_id=?', [mapping['material_id']])
        else:
            await self.db.execute('UPDATE creation_material_files SET revision=? WHERE material_id=?', [revision(after), mapping['material_id']])
        return True

    async def restore(self, book, material_id):
        async with self.db.transaction():
            binding = await self.binding(book)
            if not binding:
                raise AppError('作品没有共享资料', 409)
            trash = await self.db.fetch_one('SELECT * FROM creation_material_trash WHERE book_id=? AND material_id=?', [book, material_id])
            if not trash:
                raise AppError('已删除资料不存在或已经恢复', 409)
            mapping, row = json.loads(trash['mapping_json']), json.loads(trash['row_json'])
            root = self.directory(binding)
            from infrastructure.obsidian.material_transaction import directory_fd, read_at
            with directory_fd(root / Path(mapping['path']).parent) as fd:
                raw = read_at(fd, '.purr-' + trash['operation_id'] + '.before')
            parsed = document.decode(raw, mapping['path'], material_id=material_id, kind=mapping['kind'])
            row.update(document.values(parsed))
            table, key = TABLES[mapping['kind']]
            if await self.db.fetch_one(f'SELECT {key} FROM {table} WHERE {key}=?', [row[key]]):
                raise AppError('资料身份已被占用，无法恢复', 409)
            columns = {c['name'] for c in await self.db.fetch_all(f'PRAGMA table_info({table})')}
            if not set(row).issubset(columns):
                raise AppError('资料恢复格式不兼容', 409)
            await self.stage(root, mapping['path'], None, raw)
            await self.db.execute(f'INSERT INTO {table} (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
            await self.db.execute('INSERT INTO creation_material_files VALUES (?,?,?,?,?,?)', [material_id, book, mapping['kind'], mapping['entity_id'], mapping['path'], revision(raw)])
            await self.db.execute('DELETE FROM creation_material_trash WHERE material_id=?', [material_id])
        return await self.status(book)


def materials(db):
    return CreationMaterialService(db)


async def recover_materials(db):
    svc = materials(db)
    if not svc.root.exists():
        return
    for directory in svc.root.iterdir():
        if directory.is_symlink() or not directory.is_dir() or any(c not in '0123456789abcdef' for c in directory.name):
            continue
        for change in pending(directory / '资料'):
            receipt = await db.fetch_one('SELECT id FROM creation_material_commits WHERE id=?', [change.id])
            change.recover(bool(receipt))
