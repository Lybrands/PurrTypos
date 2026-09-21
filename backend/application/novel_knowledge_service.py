"""Book-scoped read-only knowledge service. SQLite is authority, vectors are cache."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from purra.cancellation import raise_if_stopped
from purra.context_budget import estimate_json_tokens
from purra.contracts import ContextEvidenceReceipt
from domains.writing.knowledge import KnowledgeError, admission, digest
from infrastructure.obsidian.reader import checked_path, parse, read_stable, scan, SPLITTER_VERSION
from infrastructure.persistence.writing.sqlite_catalog_repository import SqliteWritingCatalogRepository


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def terms(text):
    # Unicode61 receives CJK characters and adjacent bigrams as separate tokens.
    values = re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]', text.lower())
    values += [text[i:i + 2] for i in range(len(text) - 1)
               if all('\u3400' <= c <= '\u9fff' for c in text[i:i + 2])]
    return list(dict.fromkeys(values))


class NovelKnowledgeService:
    def __init__(self, db, *, host_id=None):
        self.db = db
        self.lock = asyncio.Lock()
        self.vector = None
        self._stop = asyncio.Event()
        self._monitor = None
        self._host_id = host_id or 'test'

    async def initialize(self, data_dir: Path):
        marker = data_dir / 'novel-knowledge-host'
        if not marker.exists():
            marker.write_text(secrets.token_hex(32))
            marker.chmod(0o600)
        self._host_id = marker.read_text().strip()
        from infrastructure.writing.knowledge_vector_index import KnowledgeVectorIndex
        self.vector = KnowledgeVectorIndex(self.db, data_dir / 'novel-knowledge-vectors')
        self._monitor = asyncio.create_task(self._watch())

    async def close(self):
        self._stop.set()
        if self._monitor:
            self._monitor.cancel()
            try:
                await self._monitor
            except asyncio.CancelledError:
                pass
        if self.vector:
            await self.vector.close()

    async def _watch(self):
        while not self._stop.is_set():
            rows = await self.db.fetch_all("SELECT book_id FROM novel_knowledge_bindings WHERE state IN ('active','unavailable')")
            for row in rows:
                try:
                    from application.creation_material_service import materials
                    from exceptions import AppError
                    try:
                        async with self.db.transaction():
                            await materials(self.db).synchronize(row['book_id'])
                    except AppError:
                        pass
                    await self.refresh(row['book_id'])
                except KnowledgeError:
                    pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
            except TimeoutError:
                pass

    async def book(self, book_id):
        if not await self.db.fetch_one('SELECT id FROM books WHERE id=?', [book_id]):
            raise KnowledgeError('book_not_found', 404)

    async def binding(self, book_id, *, required=False):
        await self.book(book_id)
        row = await self.db.fetch_one('SELECT * FROM novel_knowledge_bindings WHERE book_id=?', [book_id])
        if required and (not row or row['state'] == 'unbound'):
            raise KnowledgeError('knowledge_unbound', 404)
        if row and row['state'] != 'unbound' and row['host_id'] != self._host_id:
            if required:
                raise KnowledgeError('binding_reauthorization_required', 403)
            row['state'] = 'reauthorization_required'
        return row

    def verify_selection(self, token, book_id):
        secret = os.environ.get('PURRTYPOS_KNOWLEDGE_HOST_SECRET', '')
        if not secret:
            raise KnowledgeError('desktop_binding_required', 403)
        try:
            data, signature = token.split('.')
            expected = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
            if payload['bookId'] != book_id or not 0 <= time.time() - payload['issuedAt'] < 300:
                raise ValueError()
            return payload
        except (ValueError, KeyError, TypeError):
            raise KnowledgeError('selection_token_invalid', 403) from None

    async def preview_selection(self, book_id, token):
        await self.book(book_id)
        selection = self.verify_selection(token, book_id)
        root = self.validate_root(selection['root'])
        await self.check_overlap(book_id, root)
        files, skipped = await asyncio.to_thread(scan, root)
        items = []
        for path, value in files.items():
            try:
                parsed = parse(value, path) if isinstance(value, bytes) else None
            except (ValueError, UnicodeError, RecursionError):
                parsed = None
            items.append({'path': path, 'title': parsed['title'] if parsed else path,
                          'status': parsed['metadata'].get('status', 'unspecified') if parsed else 'invalid'})
        return {'directory': root.name, 'documents': items, 'skipped': skipped,
                'readOnly': True, 'ownershipTransfer': False}

    def validate_root(self, value):
        original = Path(value)
        if not original.is_absolute() or original.is_symlink():
            raise KnowledgeError('invalid_directory', 400)
        root = original.resolve(strict=True)
        if not root.is_dir() or root == Path(root.anchor) or any(p.startswith('.') for p in root.parts):
            raise KnowledgeError('invalid_directory', 400)
        # Selecting a registered Vault root would silently include unrelated books.
        if (root / '.obsidian').exists():
            raise KnowledgeError('select_novel_subdirectory', 400)
        return root

    async def check_overlap(self, book_id, root):
        for other in await self.db.fetch_all("SELECT book_id,root FROM novel_knowledge_bindings WHERE state != 'unbound'"):
            if other['book_id'] != book_id:
                path = Path(other['root'])
                if path.is_relative_to(root) or root.is_relative_to(path):
                    raise KnowledgeError('binding_directory_overlap')

    async def command(self, book_id, command_id, fingerprint):
        row = await self.db.fetch_one('SELECT * FROM novel_knowledge_commands WHERE id=?', [command_id])
        if row:
            if row['book_id'] != book_id or row['fingerprint'] != fingerprint:
                raise KnowledgeError('command_conflict')
            return json.loads(row['result_json'])
        return None

    async def bind(self, book_id, *, token, expected_version, command_id):
        payload = self.verify_selection(token, book_id)
        return await self.bind_selected(book_id, root=payload['root'], expected_version=expected_version,
                                        command_id=command_id)

    async def bind_selected(self, book_id, *, root, expected_version, command_id):
        """Host/test boundary only; HTTP accepts signed selection tokens, never paths."""
        async with self.lock:
            await self.book(book_id)
            root = self.validate_root(root)
            from application.creation_material_service import materials
            shared = await materials(self.db).binding(book_id)
            if shared and root != materials(self.db).directory(shared):
                raise KnowledgeError('shared_material_directory_required', 409)
            fingerprint = digest(['bind', book_id, str(root), expected_version])
            previous = await self.command(book_id, command_id, fingerprint)
            if previous:
                return previous
            await self.check_overlap(book_id, root)
            row = await self.binding(book_id)
            if (row['version'] if row else 0) != expected_version:
                raise KnowledgeError('binding_version_conflict')
            binding_id = row['id'] if row else str(uuid.uuid4())
            generation = (row['generation'] if row else 0) + 1
            result = {'id': binding_id, 'generation': generation, 'version': expected_version + 1}
            async with self.db.transaction():
                await self.db.execute('''INSERT INTO novel_knowledge_bindings
                    (id,book_id,root,generation,state,version,host_id) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(book_id) DO UPDATE SET root=excluded.root,generation=excluded.generation,
                    state='active',version=excluded.version,host_id=excluded.host_id,
                    semantic_config=NULL,scope_json='{}',scan_json='{}',scanned_at=NULL''',
                    [binding_id, book_id, str(root), generation, 'active', result['version'], self._host_id or 'test'])
                await self.db.execute('INSERT INTO novel_knowledge_commands VALUES (?,?,?,?)',
                                      [command_id, book_id, fingerprint, encode(result)])
        await self.refresh(book_id)
        return result

    async def configure(self, book_id, *, expected_version, command_id, scope=None, semantic=None, unbind=False):
        async with self.lock:
            if unbind and await self.db.fetch_one('SELECT book_id FROM creation_material_books WHERE book_id=?', [book_id]):
                raise KnowledgeError('shared_material_binding_required', 409)
            fingerprint = digest([book_id, expected_version, scope, semantic, unbind])
            previous = await self.command(book_id, command_id, fingerprint)
            if previous:
                return previous
            row = await self.binding(book_id, required=True)
            if row['version'] != expected_version:
                raise KnowledgeError('binding_version_conflict')
            if scope is not None:
                await self.validate_scope(book_id, scope)
            config = row['semantic_config']
            if semantic is True:
                if not self.vector:
                    raise KnowledgeError('semantic_unavailable', 503)
                config = await self.vector.configuration_id()
                if not config:
                    raise KnowledgeError('embedding_not_configured', 400)
            elif semantic is False:
                config = None
            result = {'version': row['version'] + 1}
            async with self.db.transaction():
                await self.db.execute('''UPDATE novel_knowledge_bindings SET version=?,state=?,
                    generation=?,scope_json=?,semantic_config=? WHERE id=?''',
                    [result['version'], 'unbound' if unbind else 'active', row['generation'] + int(unbind),
                     encode(scope) if scope is not None else row['scope_json'], config, row['id']])
                await self.db.execute('INSERT INTO novel_knowledge_commands VALUES (?,?,?,?)',
                                      [command_id, book_id, fingerprint, encode(result)])
            return result

    async def chapters(self, book_id):
        rows = await SqliteWritingCatalogRepository(self.db).load_writing_chapters(book_id)
        # Match the writing tree traversal, including volumes without treating them as chapters.
        ordered = []
        def visit(parent=None):
            for row in rows:
                if row['parent_id'] == parent:
                    children = [r for r in rows if r['parent_id'] == row['id']]
                    if not children:
                        ordered.append(row['id'])
                    visit(row['id'])
        visit()
        return ordered

    async def chapter_revision(self, book_id, chapters):
        rows = await self.db.fetch_all("SELECT c.id,a.content FROM outline_chapters c JOIN outlines o ON o.id=c.outline_id LEFT JOIN articles a ON a.chapter_id=c.id WHERE o.book_id=? AND o.type='writing' ORDER BY c.id", [book_id])
        return digest([chapters, [[r['id'], digest(r['content'] or '')] for r in rows]])

    async def validate_scope(self, book_id, scope):
        if scope.get('purpose', 'prose') not in ('prose', 'discussion', 'character'):
            raise KnowledgeError('scope_invalid', 400)
        chapters = await self.chapters(book_id)
        if scope.get('chapterId') and scope['chapterId'] not in chapters:
            raise KnowledgeError('chapter_outside_book', 403)
        if scope.get('purpose') == 'character' and not scope.get('characterId'):
            raise KnowledgeError('character_required', 400)
        if scope.get('characterId'):
            character = scope['characterId']
            host = await self.db.fetch_one('SELECT id FROM characters WHERE book_id=? AND CAST(id AS TEXT)=?', [book_id, character])
            external = await self.db.fetch_one('''SELECT d.id FROM novel_knowledge_documents d
                JOIN novel_knowledge_bindings b ON b.id=d.binding_id AND b.generation=d.generation
                WHERE b.book_id=? AND d.source_id=? AND d.state='eligible' ''', [book_id, character])
            if not host and not external:
                raise KnowledgeError('character_outside_book', 403)

    async def scope_snapshot(self, book_id, chapter_id=None):
        row = await self.db.fetch_one('SELECT * FROM novel_knowledge_bindings WHERE book_id=?', [book_id])
        if not row or row['state'] == 'unbound':
            return {}
        scope = json.loads(row['scope_json'])
        # A saved author discussion mode is explicit user configuration, never model inference.
        scope = {'purpose': 'prose', **scope}
        if chapter_id:
            scope['chapterId'] = chapter_id
        await self.validate_scope(book_id, scope)
        chapters = await self.chapters(book_id)
        from application.creation_material_service import materials
        material_revision = await materials(self.db).source_revision(book_id)
        return {**({'materialRevision': material_revision} if material_revision else {}), **scope, 'bindingId': row['id'], 'generation': row['generation'],
                'bindingVersion': row['version'], 'chapterOrder': await self.chapter_revision(book_id, chapters)}

    async def refresh(self, book_id):
        async with self.lock:
            binding = await self.binding(book_id, required=True)
            if binding['state'] == 'reauthorization_required':
                raise KnowledgeError('binding_reauthorization_required', 403)
            try:
                files, skipped = await asyncio.to_thread(scan, Path(binding['root']))
            except (OSError, KnowledgeError) as error:
                await self.db.execute("UPDATE novel_knowledge_bindings SET state='unavailable' WHERE id=?", [binding['id']])
                raise KnowledgeError(getattr(error, 'code', 'vault_unavailable'), 503) from None
            old = await self.db.fetch_all('SELECT * FROM novel_knowledge_documents WHERE binding_id=? AND generation=?',
                                           [binding['id'], binding['generation']])
            by_path = {d['path']: d for d in old}
            shared = {m['material_id']: m for m in await self.db.fetch_all('SELECT * FROM creation_material_files WHERE book_id=?', [book_id])}
            parsed, identities = {}, {}
            for path, value in files.items():
                try:
                    if isinstance(value, str):
                        raise ValueError(value)
                    item = parse(value, path)
                    owned = shared.get(item['metadata'].get('purr_id'))
                    if owned:
                        from infrastructure.obsidian.material_document import decode
                        decode(value, path, material_id=owned['material_id'], kind=owned['kind'])
                    parsed[path] = item
                    if item['metadata'].get('purr_id'):
                        identities.setdefault(item['metadata']['purr_id'], []).append(path)
                except Exception as error:
                    # Parser exceptions quarantine the current file, never retain old searchable text.
                    parsed[path] = {'error': type(error).__name__ if not isinstance(error, ValueError) else str(error)[:80]}
            known_paths = {str(Path(p).with_suffix('')): p for p in parsed}
            titles = {}
            for path, item in parsed.items():
                if 'error' not in item:
                    for name in [item['title'], Path(path).stem, *item['metadata'].get('aliases', [])]:
                        titles.setdefault(name, set()).add(path)
            shared = {m['material_id']: m for m in await self.db.fetch_all('SELECT * FROM creation_material_files WHERE book_id=?', [book_id])}
            from application.creation_material_service import materials
            shared_binding = await materials(self.db).binding(book_id)
            shared_root = str(materials(self.db).directory(shared_binding)) if shared_binding else None
            # Shared files are indexed under 资料; Obsidian starts at its parent.
            if shared_root == binding['root']:
                known_paths.update({'资料/' + key: value for key, value in list(known_paths.items())})
            host_entities = {}
            for table, kind in [('characters', 'character'), ('setting_entities', 'setting')]:
                for entity in await self.db.fetch_all(f'SELECT id,name FROM {table} WHERE book_id=?', [book_id]):
                    host_entities[f'{kind}:{entity["id"]}'] = entity['name']
            counts = {}
            seen_ids = set()
            async with self.db.transaction():
                for path, item in parsed.items():
                    prior = by_path.get(path)
                    source_id = item.get('metadata', {}).get('purr_id')
                    if not prior and source_id and len(identities[source_id]) == 1:
                        matches = [d for d in old if d['source_id'] == source_id and d['path'] not in files]
                        if len(matches) == 1:
                            prior = matches[0]
                    document_id = prior['id'] if prior else str(uuid.uuid4())
                    seen_ids.add(document_id)
                    meta = item.get('metadata', {})
                    diagnostics = []
                    state = 'eligible'
                    if 'error' in item:
                        state, diagnostics = 'stale', [item['error']]
                    elif source_id and len(identities[source_id]) > 1:
                        state, diagnostics = 'conflict', ['duplicate_id']
                    elif any(p.lower() in ('更新建议', 'proposals') for p in Path(path).parts):
                        state, diagnostics = 'reference', ['proposal_directory']
                    elif not source_id or not meta.get('type') or admission(meta, {'purpose': 'discussion'}, await self.chapters(book_id)):
                        state, diagnostics = 'reference', ['formal_metadata_incomplete']
                    if meta.get('known_to'):
                        resolved_known = []
                        for known in meta['known_to']:
                            if known.startswith('[[') and known.endswith(']]'):
                                target = known[2:-2].split('|')[0].split('#')[0]
                                target_path = known_paths.get(target)
                                candidates = {target_path} if target_path else titles.get(target, set())
                                if len(candidates) == 1:
                                    known_meta = parsed[next(iter(candidates))].get('metadata', {})
                                    identity = known_meta.get('purr_id')
                                    if identity and len(identities.get(identity, [])) == 1:
                                        resolved_known.append(identity)
                                        continue
                                diagnostics.append('known_to_unresolved')
                            else:
                                resolved_known.append(known)
                        meta = {**meta, 'known_to': resolved_known}
                    overlap = meta.get('purr_entity')
                    owned = shared.get(source_id)
                    same_material = bool(owned and shared_root == binding['root'] and meta.get('purr_kind') == owned['kind'])
                    if not same_material and (overlap or item.get('title') in host_entities.values()):
                        state = 'reference'
                        diagnostics.append('host_ownership_retained' if overlap in host_entities else 'entity_overlap_requires_mapping')
                    if overlap and overlap not in host_entities:
                        diagnostics.append('entity_mapping_invalid')
                    revision = item.get('revision')
                    await self.db.execute('''INSERT INTO novel_knowledge_documents VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET path=excluded.path,source_id=excluded.source_id,
                        title=excluded.title,revision=excluded.revision,state=excluded.state,
                        metadata_json=excluded.metadata_json,diagnostics_json=excluded.diagnostics_json''',
                        [document_id, binding['id'], binding['generation'], path, source_id, item.get('title', path),
                         revision, state, encode(meta), encode(diagnostics)])
                    await self.db.execute('''INSERT OR REPLACE INTO novel_knowledge_entity_bindings VALUES (?,?,?,?)''',
                        [document_id, overlap or source_id, 'purrtypos' if overlap else 'obsidian', state])
                    if revision:
                        await self.db.execute('INSERT OR IGNORE INTO novel_knowledge_revisions(document_id,revision,body,metadata_json) VALUES (?,?,?,?)',
                                              [document_id, revision, item['body'], encode(meta)])
                        for chunk in item['chunks']:
                            locator = {k: chunk[k] for k in ('heading', 'line', 'blocks')}
                            chunk_id = digest([document_id, revision, locator, SPLITTER_VERSION])
                            exists = await self.db.fetch_one('SELECT id FROM novel_knowledge_chunks WHERE id=?', [chunk_id])
                            if not exists:
                                await self.db.execute('INSERT INTO novel_knowledge_chunks VALUES (?,?,?,?,?)',
                                    [chunk_id, document_id, revision, encode(locator), chunk['text']])
                                await self.db.execute('INSERT INTO novel_knowledge_fts VALUES (?,?)',
                                    [chunk_id, ' '.join(terms(item['title'] + ' ' + chunk['text']))])
                        for link in item['links']:
                            if link['state'] == 'external':
                                continue
                            target = link['target']
                            if not target:
                                candidates = {path}
                            elif '..' in Path(target).parts or Path(target).is_absolute():
                                candidates = set()
                                link['state'] = 'outside_scope'
                            else:
                                key = str(Path(target).with_suffix('')) if Path(target).suffix.lower() in ('.md', '.markdown') else target
                                local = str(Path(path).parent / key)
                                candidates = {known_paths[k] for k in (key, local) if k in known_paths}
                                if not candidates:
                                    candidates = titles.get(key, set())
                            if link['state'] != 'outside_scope':
                                link['state'] = 'resolved' if len(candidates) == 1 else 'ambiguous' if candidates else 'missing'
                                if len(candidates) == 1:
                                    target_path = next(iter(candidates))
                                    link['path'] = target_path
                                    if target_path == path:
                                        link['selfReference'] = True
                        await self.db.execute('INSERT OR REPLACE INTO novel_knowledge_links VALUES (?,?)', [document_id, encode(item['links'])])
                    counts[state] = counts.get(state, 0) + 1
                for document in old:
                    if document['id'] not in seen_ids:
                        await self.db.execute("UPDATE novel_knowledge_documents SET state='missing' WHERE id=?", [document['id']])
                await self.db.execute("UPDATE novel_knowledge_bindings SET state='active',scan_json=?,scanned_at=CURRENT_TIMESTAMP WHERE id=?",
                                      [encode({'markdown': len(files), 'skipped': skipped, 'states': counts}), binding['id']])
        return await self.status(book_id)

    async def status(self, book_id):
        row = await self.binding(book_id)
        if not row:
            return {'state': 'unbound', 'version': 0, 'semantic': {'state': 'disabled'}}
        info = {k: row[k] for k in ('id', 'generation', 'version', 'state', 'scanned_at')}
        info.update(directory=Path(row['root']).name, scope=json.loads(row['scope_json']), scan=json.loads(row['scan_json']))
        info['sharedStorage'] = bool(await self.db.fetch_one('SELECT book_id FROM creation_material_books WHERE book_id=?', [book_id]))
        info['semantic'] = await self.vector.status(row) if self.vector else {'state': 'disabled'}
        return info

    async def documents(self, book_id):
        binding = await self.binding(book_id, required=True)
        rows = await self.db.fetch_all('SELECT * FROM novel_knowledge_documents WHERE binding_id=? AND generation=? ORDER BY path', [binding['id'], binding['generation']])
        return [self.public_document(r) for r in rows]

    @staticmethod
    def public_document(row):
        return {'id': row['id'], 'path': row['path'], 'title': row['title'], 'revision': row['revision'],
                'state': row['state'], 'metadata': json.loads(row['metadata_json']),
                'diagnostics': json.loads(row['diagnostics_json'])}

    async def source(self, book_id, document_id, revision=None):
        await self.book(book_id)
        row = await self.db.fetch_one('''SELECT d.*,b.book_id,b.root,b.generation AS current_generation,b.state AS binding_state
            FROM novel_knowledge_documents d JOIN novel_knowledge_bindings b ON b.id=d.binding_id
            WHERE d.id=? AND b.book_id=?''', [document_id, book_id])
        if not row:
            raise KnowledgeError('document_not_found', 404)
        used = revision or row['revision']
        stored = await self.db.fetch_one('SELECT * FROM novel_knowledge_revisions WHERE document_id=? AND revision=?', [document_id, used])
        if not stored:
            raise KnowledgeError('revision_not_found', 404)
        current = False
        try:
            binding = await self.binding(book_id, required=True)
            current = binding['state'] == 'active' and row['generation'] == binding['generation'] and digest(await asyncio.to_thread(read_stable, Path(row['root']), row['path'])) == used
        except (OSError, KnowledgeError):
            pass
        links = await self.db.fetch_one('SELECT links_json FROM novel_knowledge_links WHERE document_id=?', [document_id])
        return {**self.public_document(row), 'body': stored['body'], 'usedRevision': used,
                'usedMetadata': json.loads(stored['metadata_json']), 'currentMatches': current,
                'links': json.loads(links['links_json']) if links else []}

    async def navigation(self, book_id, document_id, revision=None, anchor=None):
        binding = await self.binding(book_id, required=True)
        if binding['state'] != 'active':
            raise KnowledgeError('vault_unavailable', 503)
        row = await self.db.fetch_one('SELECT * FROM novel_knowledge_documents WHERE id=? AND binding_id=? AND generation=?',
                                      [document_id, binding['id'], binding['generation']])
        if not row or row['state'] == 'missing':
            raise KnowledgeError('source_missing', 404)
        file = checked_path(Path(binding['root']), row['path'])
        data = await asyncio.to_thread(read_stable, Path(binding['root']), row['path'])
        from application.creation_material_service import materials
        shared = await materials(self.db).binding(book_id)
        vault = materials(self.db).directory(shared).parent if shared else Path(binding['root'])
        return {'uri': 'obsidian://open?path=' + quote(str(file), safe=''), 'path': str(file),
                'vaultPath': str(vault), 'anchor': anchor, 'anchorFallback': bool(anchor), 'currentRevision': digest(data),
                'usedRevision': revision, 'currentMatches': not revision or revision == digest(data)}

    async def check_scope(self, book_id, scope):
        binding = await self.binding(book_id, required=True)
        if binding['state'] != 'active':
            raise KnowledgeError('vault_unavailable', 503)
        if scope.get('bindingId') != binding['id'] or scope.get('generation') != binding['generation'] or scope.get('bindingVersion') != binding['version']:
            raise KnowledgeError('knowledge_scope_changed')
        chapters = await self.chapters(book_id)
        if scope.get('purpose') != 'discussion' and scope.get('chapterOrder') != await self.chapter_revision(book_id, chapters):
            raise KnowledgeError('chapter_order_changed')
        await self.validate_scope(book_id, scope)
        if scope.get('materialRevision'):
            from application.creation_material_service import materials
            from exceptions import AppError
            try:
                current = await materials(self.db).source_revision(book_id)
            except AppError as error:
                raise KnowledgeError('material_source_unavailable') from error
            if current != scope['materialRevision']:
                raise KnowledgeError('material_source_changed')
        return binding, chapters

    async def search(self, book_id, query, *, scope=None, limit=12, token_budget=3000, mode='hybrid', document_id=None, revision=None, chunk_id=None, signal=None, context_block='writing_knowledge'):
        raise_if_stopped(signal)
        if not 1 <= limit <= 12 or len(query) > 4000:
            raise KnowledgeError('retrieval_budget_exceeded', 400)
        # Complete scans catch additions/duplicates as well as deletion; unavailable is not no-match.
        await self.refresh(book_id)
        scope = scope if scope is not None else await self.scope_snapshot(book_id)
        binding, chapters = await self.check_scope(book_id, scope)
        rows = await self.db.fetch_all('''SELECT c.*,d.title,d.state,d.metadata_json,d.path
            FROM novel_knowledge_chunks c JOIN novel_knowledge_documents d ON d.id=c.document_id AND d.revision=c.revision
            WHERE d.binding_id=? AND d.generation=?''', [binding['id'], binding['generation']])
        eligible, excluded = {}, {}
        for row in rows:
            reason = row['state'] if row['state'] != 'eligible' else admission(json.loads(row['metadata_json']), scope, chapters)
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
            else:
                eligible[row['id']] = row
        channels = []
        if document_id:
            matching = [r['id'] for r in eligible.values() if r['document_id'] == document_id and (not chunk_id or r['id'] == chunk_id)]
            if revision and any(eligible[k]['revision'] != revision for k in matching):
                raise KnowledgeError('source_revision_changed')
            if not matching:
                raise KnowledgeError('document_not_admitted', 403)
            channels.append(('read', matching))
        else:
            exact = [r['id'] for r in eligible.values() if query.casefold() in [str(n).casefold() for n in
                     [r['title'], json.loads(r['metadata_json']).get('purr_id'), *json.loads(r['metadata_json']).get('aliases', [])]]]
            channels.append(('exact', exact))
            query_terms = terms(query)
            if query_terms:
                fts = await self.db.fetch_all('SELECT f.chunk_id,bm25(novel_knowledge_fts) AS rank FROM novel_knowledge_fts f JOIN novel_knowledge_chunks c ON c.id=f.chunk_id JOIN novel_knowledge_documents d ON d.id=c.document_id AND d.revision=c.revision WHERE tokens MATCH ? AND d.binding_id=? AND d.generation=? AND c.id IN (SELECT value FROM json_each(?)) ORDER BY rank LIMIT 40',
                                             [' OR '.join('"' + t.replace('"', '""') + '"' for t in query_terms[:128]), binding['id'], binding['generation'], encode(list(eligible))])
                channels.append(('fulltext', [r['chunk_id'] for r in fts if r['chunk_id'] in eligible][:40]))
            if mode == 'hybrid' and binding['semantic_config'] and self.vector:
                try:
                    ranked = await self.vector.query(binding, query, list(eligible), signal)
                    channels.append(('semantic', ranked))
                except Exception:
                    raise_if_stopped(signal)
                    excluded['semantic_unavailable'] = 1
        scores, reasons = {}, {}
        for channel, ids in channels:
            for rank, key in enumerate(ids[:40]):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                reasons.setdefault(key, []).append(channel)
        selected, receipts, spent, deferred = [], [], 0, 0
        for key in sorted(scores, key=lambda k: (-scores[k], k))[:40]:
            row = eligible.get(key)
            if not row:
                continue
            metadata = json.loads(row['metadata_json'])
            content = encode({'title': row['title'], 'status': metadata.get('status'), 'scope': metadata,
                              'locator': json.loads(row['locator_json']), 'text': row['text']})
            cost = estimate_json_tokens(content)
            if spent + cost > token_budget or len(selected) >= limit:
                deferred += 1
                continue
            receipt = ContextEvidenceReceipt(
                evidence_id='novel-knowledge:' + key + ':' + digest([context_block, scope, reasons[key]])[:24], context_block=context_block, source='novel_knowledge/v1',
                item_id=key, version=binding['generation'], metadata={'bookId': book_id, 'documentId': row['document_id'],
                    'revision': row['revision'], 'scope': scope, 'locator': json.loads(row['locator_json']),
                    'contentDigest': digest(content), 'title': row['title'], 'reasons': reasons[key]})
            selected.append({'id': key, 'documentId': row['document_id'], 'title': row['title'], 'revision': row['revision'],
                             'content': content, 'locator': json.loads(row['locator_json']), 'reasons': reasons[key]})
            receipts.append(receipt)
            spent += cost
        await self.validate(book_id, receipts, signal=signal)
        return {'state': 'matched' if selected else 'no_match', 'items': selected, 'receipts': receipts,
                'tokens': spent, 'deferred': deferred, 'excluded': excluded}

    async def validate(self, book_id, receipts, *, signal=None):
        for receipt in receipts:
            raise_if_stopped(signal)
            if receipt.source != 'novel_knowledge/v1':
                continue
            meta = receipt.metadata
            scope = dict(meta.get('scope') or {})
            binding, chapters = await self.check_scope(book_id, scope)
            row = await self.db.fetch_one('''SELECT c.*,d.state,d.path,d.title,d.metadata_json FROM novel_knowledge_chunks c
                JOIN novel_knowledge_documents d ON d.id=c.document_id AND d.revision=c.revision
                WHERE c.id=? AND d.binding_id=? AND d.generation=?''',
                [receipt.item_id, binding['id'], binding['generation']])
            if not row or row['state'] != 'eligible' or meta.get('bookId') != book_id or row['revision'] != meta.get('revision') or admission(json.loads(row['metadata_json']), scope, chapters):
                raise KnowledgeError('knowledge_evidence_invalid')
            try:
                raw = await asyncio.to_thread(read_stable, Path(binding['root']), row['path'])
            except (OSError, KnowledgeError):
                raise KnowledgeError('knowledge_evidence_stale') from None
            content = encode({'title': row['title'], 'status': json.loads(row['metadata_json']).get('status'),
                'scope': json.loads(row['metadata_json']), 'locator': json.loads(row['locator_json']), 'text': row['text']})
            if meta.get('contentDigest') != digest(content):
                raise KnowledgeError('knowledge_evidence_content_changed')
            if receipt.evidence_id != 'novel-knowledge:' + row['id'] + ':' + digest([receipt.context_block, scope, list(meta.get('reasons') or [])])[:24] or receipt.version != binding['generation'] or meta.get('documentId') != row['document_id']:
                raise KnowledgeError('knowledge_evidence_invalid')
            if digest(raw) != row['revision']:
                raise KnowledgeError('knowledge_evidence_stale')


def get_novel_knowledge_service(db):
    service = getattr(db, '_novel_knowledge_service', None)
    if service is None:
        service = NovelKnowledgeService(db)
        if hasattr(db, '__dict__'):
            db._novel_knowledge_service = service
    return service
