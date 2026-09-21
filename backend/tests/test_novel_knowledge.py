"""Temporary Vault and SQLite only. Embedding is deterministic, never a live Provider."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from dataclasses import replace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from database.connection import DatabaseConnection
from application.novel_knowledge_service import get_novel_knowledge_service
from application.novel_knowledge_evidence import NovelKnowledgeEvidenceValidator
from domains.writing.knowledge import KnowledgeError
from infrastructure.obsidian.reader import parse, read_stable
from infrastructure.writing.knowledge_vector_index import KnowledgeVectorIndex
from purra_mem0 import EmbeddingResult


def note(body='红门只能用铜钥匙打开。', **meta):
    values = {'purr_id': 'red-door', 'type': 'world', 'status': 'confirmed',
              'temporal_scope': 'global', 'known_to': ['*'], **meta}
    import yaml
    return '---\n' + yaml.safe_dump(values, allow_unicode=True) + '---\n# 红门\n\n' + body


@pytest_asyncio.fixture
async def env(tmp_path):
    db = DatabaseConnection(tmp_path / 'db')
    await db.init()
    for book in ['a', 'b']:
        await db.execute('INSERT INTO books(id,title) VALUES (?,?)', [book, book])
        await db.execute("INSERT INTO outlines(id,book_id,title,type) VALUES (?,?,?,'writing')", ['outline-' + book, book, book])
        for i in range(1, 4):
            await db.execute('INSERT INTO outline_chapters(id,outline_id,title,sort) VALUES (?,?,?,?)', [f'{book}-{i}', 'outline-' + book, str(i), i])
    root = tmp_path / 'vault' / '我的小说'
    root.mkdir(parents=True)
    (root / '红门.md').write_text(note())
    svc = get_novel_knowledge_service(db)
    try:
        await svc.bind_selected('a', root=str(root), expected_version=0, command_id='bind-a')
        yield db, svc, root
    finally:
        await svc.close()
        await db.close()


async def search(svc, query='红门', **scope):
    if scope:
        row = await svc.binding('a')
        await svc.configure('a', expected_version=row['version'], command_id='scope-' + str(time.time_ns()), scope=scope)
    return await svc.search('a', query)


@pytest.mark.asyncio
async def test_chinese_exact_fulltext_alias_and_read(env):
    _, svc, root = env
    (root / '红门.md').write_text(note(aliases=['朱门']))
    for query, reason in [('红门', 'exact'), ('朱门', 'exact'), ('铜钥匙', 'fulltext')]:
        result = await search(svc, query)
        assert result['items'] and reason in result['items'][0]['reasons']
        assert str(root) not in json.dumps(result['items'], ensure_ascii=False)
    item = result['items'][0]
    read = await svc.search('a', 'read', document_id=item['documentId'], revision=item['revision'])
    assert read['items']
    assert len(read['items']) == len(read['receipts'])


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata,scope,expected', [
    ({'status': 'draft'}, {}, False), ({'status': 'deprecated'}, {}, False),
    ({'temporal_scope': 'chapter_range', 'valid_from_chapter': 'a-2'}, {'purpose': 'prose', 'chapterId': 'a-1'}, False),
    ({'temporal_scope': 'chapter_range', 'valid_from_chapter': 'a-2'}, {'purpose': 'prose', 'chapterId': 'a-2'}, True),
    ({'temporal_scope': 'chapter_range', 'valid_from_chapter': 'a-1', 'valid_to_chapter': 'a-2'}, {'purpose': 'prose', 'chapterId': 'a-2'}, False),
    ({'temporal_scope': 'chapter_range', 'valid_from_chapter': 'b-1'}, {'purpose': 'discussion'}, False),
    ({'known_to': []}, {'purpose': 'prose'}, False),
    ({'known_to': [], 'temporal_scope': 'chapter_range', 'valid_from_chapter': 'a-3'}, {'purpose': 'discussion'}, True),
    ({'knowledge_from_chapter': 'a-2'}, {'purpose': 'prose', 'chapterId': 'a-1'}, False),
])
async def test_state_time_and_character_admission(env, metadata, scope, expected):
    _, svc, root = env
    (root / '红门.md').write_text(note(**metadata))
    result = await search(svc, **scope)
    assert bool(result['items']) == expected


@pytest.mark.asyncio
async def test_canvas_content_never_indexed_and_no_writes(env):
    _, svc, root = env
    (root / '布局.canvas').write_text('{"nodes":[{"text":"画布独有秘密蓝龙"}]}')
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert not (await search(svc, '蓝龙'))['items']
    status = await svc.status('a')
    assert status['scan']['skipped']['.canvas'] == 1
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    (root / '蓝龙.md').write_text(note('画布独有秘密蓝龙', purr_id='blue-dragon'))
    assert (await search(svc, '蓝龙'))['items']


@pytest.mark.asyncio
async def test_duplicate_invalid_delete_unavailable_and_history(env):
    _, svc, root = env
    result = await search(svc)
    item, receipt = result['items'][0], result['receipts'][0]
    (root / '红门.md').write_text('---\ninvalid: [\n---\n坏 YAML')
    assert not (await search(svc))['items']
    with pytest.raises(KnowledgeError):
        await svc.validate('a', [receipt])
    source = await svc.source('a', item['documentId'], item['revision'])
    assert '铜钥匙' in source['body'] and not source['currentMatches']
    (root / '红门.md').write_text(note())
    (root / '重复.md').write_text(note())
    assert not (await search(svc))['items']
    (root / '重复.md').unlink()
    assert (await search(svc))['items']
    root.rename(root.with_name('离线'))
    with pytest.raises(KnowledgeError, match='vault_unavailable'):
        await search(svc)
    assert (await svc.documents('a'))[0]['state'] != 'missing'
    root.with_name('离线').rename(root)
    (root / '红门.md').unlink()
    assert not (await search(svc))['items']
    with pytest.raises(KnowledgeError):
        await svc.navigation('a', item['documentId'])


@pytest.mark.asyncio
async def test_revision_generation_order_and_forged_receipts(env):
    db, svc, root = env
    receipt = (await search(svc))['receipts'][0]
    with pytest.raises(KnowledgeError):
        await svc.validate('a', [replace(receipt, evidence_id='forged')])
    (root / '红门.md').write_text(note('现在只能用银钥匙打开。'))
    with pytest.raises(KnowledgeError, match='stale'):
        await svc.validate('a', [receipt])
    receipt = (await search(svc, '银钥匙'))['receipts'][0]
    await db.execute("UPDATE outline_chapters SET sort=10 WHERE id='a-1'")
    with pytest.raises(KnowledgeError, match='chapter_order_changed'):
        await svc.validate('a', [receipt])
    receipt = (await search(svc, '银钥匙'))['receipts'][0]
    version = (await svc.binding('a'))['version']
    await svc.configure('a', expected_version=version, command_id='unbind', unbind=True)
    with pytest.raises(KnowledgeError):
        await svc.validate('a', [receipt])
    await svc.bind_selected('a', root=str(root), expected_version=version + 1, command_id='rebind')
    with pytest.raises(KnowledgeError, match='scope_changed'):
        await svc.validate('a', [receipt])


@pytest.mark.asyncio
async def test_overlap_paths_and_cross_book(env, tmp_path):
    _, svc, root = env
    with pytest.raises(KnowledgeError, match='overlap'):
        await svc.bind_selected('b', root=str(root.parent), expected_version=0, command_id='other')
    outside = tmp_path / 'outside.md'
    outside.write_text(note('另一作品独有的秘密', purr_id='outside'))
    (root / '逃逸.md').symlink_to(outside)
    (root / '隐藏').mkdir()
    (root / '隐藏' / 'escape.md').symlink_to(outside)
    assert not (await search(svc, '独有'))['items']
    with pytest.raises(KnowledgeError):
        read_stable(root, '../outside.md')
    item = (await search(svc))['items'][0]
    with pytest.raises(KnowledgeError):
        await svc.source('b', item['documentId'])
    (root / '.hidden.md').write_text(note('隐藏绝密', purr_id='hidden'))
    assert not (await search(svc, '绝密'))['items']


@pytest.mark.asyncio
async def test_ownership_never_overrides_cards(env):
    db, svc, root = env
    await db.execute("INSERT INTO characters(book_id,name) VALUES ('a','红门')")
    result = await search(svc)
    assert not result['items']
    doc = (await svc.documents('a'))[0]
    assert doc['state'] == 'reference'
    assert 'entity_overlap_requires_mapping' in doc['diagnostics']
    assert (await db.fetch_one("SELECT name FROM characters WHERE book_id='a'"))['name'] == '红门'


@pytest.mark.asyncio
async def test_known_to_links_and_unknown_scope(env):
    _, svc, root = env
    (root / '甲.md').write_text(note('人物甲', purr_id='jia', type='character').replace('# 红门', '# 甲'))
    (root / '红门.md').write_text(note(known_to=['[[甲]]']))
    await svc.refresh('a')
    assert not (await search(svc))['items']
    assert (await search(svc, purpose='character', characterId='jia', chapterId='a-1'))['items']
    with pytest.raises(KnowledgeError, match='character_outside_book'):
        await search(svc, purpose='character', characterId='another-book')


@pytest.mark.asyncio
async def test_budget_no_partial_receipts_and_stable_rename(env):
    _, svc, root = env
    result = await svc.search('a', '红门', token_budget=1)
    assert result['items'] == result['receipts'] == [] and result['deferred'] > 0
    document = (await svc.documents('a'))[0]
    (root / '红门.md').rename(root / '改名 空格#&.md')
    result = await search(svc)
    assert result['items'][0]['documentId'] == document['id']
    navigation = await svc.navigation('a', document['id'], anchor='^块')
    assert Path(navigation['vaultPath']).is_dir()
    assert navigation['uri'].startswith('obsidian://open?path=')
    assert '%23%26' in navigation['uri'] and navigation['anchorFallback']


def test_parser_links_flat_yaml_and_limits(tmp_path):
    parsed = parse(note('## 标题\n[[人物/甲#^块|别名]] ![[乙]] [甲](人物/甲.md#标题) [[自己]]\n内容 ^block').encode(), '人物.md')
    assert parsed['links'][0]['anchor'] == '^块'
    assert len(parsed['links']) == 4
    assert any(c['blocks'] == ['block'] for c in parsed['chunks'])
    for text in ['---\na: &a [*a]\n---\n正文', '---\na: 1\na: 2\n---\n正文', '---\nknown_to: 甲\n---\n正文']:
        with pytest.raises(Exception):
            parse(text.encode(), 'bad.md')
    (tmp_path / 'big.md').write_bytes(b'x' * (1024 * 1024 + 1))
    with pytest.raises(KnowledgeError, match='file_limit'):
        read_stable(tmp_path, 'big.md')


class Gateway:
    calls = 0
    closed = False
    fail = False
    def __init__(self, config):
        pass
    async def embed(self, texts, signal):
        Gateway.calls += 1
        if Gateway.fail:
            raise TimeoutError()
        return EmbeddingResult([(1.0, 0.0, 0.0, 0.0) for _ in texts], input_tokens=17)
    async def close(self):
        Gateway.closed = True


@pytest.mark.asyncio
async def test_qdrant_incremental_rebuild_failure_and_current_revision(env, tmp_path):
    db, svc, root = env
    config = {'apiProvider': 'openai', 'model': 'fake', 'apiKey': 'test', 'baseUrl': 'https://example.invalid/v1', 'dimensions': 4}
    await db.execute('INSERT INTO settings(key,value) VALUES (?,?)', ['memory_embedding_config', json.dumps(config)])
    svc.vector = KnowledgeVectorIndex(db, tmp_path / 'vectors', gateway_factory=Gateway)
    Gateway.calls = 0
    Gateway.fail = False
    await svc.configure('a', expected_version=1, command_id='semantic', semantic=True)
    binding = await svc.binding('a')
    await svc.vector.index(binding)
    calls = Gateway.calls
    await svc.vector.index(binding)
    assert Gateway.calls == calls
    result = await svc.search('a', '门的通行凭证', mode='hybrid')
    assert 'semantic' in result['items'][0]['reasons']
    old = result['receipts'][0]
    (root / '红门.md').write_text(note('现在只有铁钥匙可以通行。'))
    result = await svc.search('a', '铜钥匙', mode='hybrid')
    assert all('只能用铜' not in i['content'] for i in result['items'])
    with pytest.raises(KnowledgeError):
        await svc.validate('a', [old])
    Gateway.fail = True
    result = await svc.search('a', '铁钥匙', mode='hybrid')
    assert result['items'] and result['excluded']['semantic_unavailable']
    Gateway.fail = False
    await svc.vector.index(await svc.binding('a'))
    collection = 'novel_' + binding['semantic_config'][:24]
    svc.vector.client.delete_collection(collection)
    await svc.vector.index(await svc.binding('a'))
    assert (await svc.search('a', '通行凭证', mode='hybrid'))['items']
    config['model'] = 'changed'
    await db.execute('UPDATE settings SET value=? WHERE key=?', [json.dumps(config), 'memory_embedding_config'])
    assert (await svc.status('a'))['semantic']['state'] == 'configuration_changed'


def token(secret, book, root):
    payload = base64.urlsafe_b64encode(json.dumps({'bookId': book, 'root': str(root), 'issuedAt': int(time.time()), 'nonce': 'test'}).encode()).decode().rstrip('=')
    return payload + '.' + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_signed_binding_http_commands_and_restore(env, monkeypatch):
    db, svc, root = env
    from dependencies import set_db, clear_db
    from routers.novel_knowledge import router
    from exceptions import AppError
    from fastapi.responses import JSONResponse
    app = FastAPI()
    app.include_router(router, prefix='/api')
    @app.exception_handler(AppError)
    async def handle(request, error):
        return JSONResponse({'success': False, 'error': str(error)}, status_code=error.status_code)
    set_db(db)
    monkeypatch.setenv('PURRTYPOS_KNOWLEDGE_HOST_SECRET', 'secret')
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            r = await client.post('/api/books/a/knowledge/binding', json={'root': str(root)})
            assert r.status_code == 422
            r = await client.post('/api/books/a/knowledge/binding/preview', json={'selectionToken': token('bad', 'a', root)})
            assert r.status_code == 403
            r = await client.post('/api/books/a/knowledge/binding/preview', json={'selectionToken': token('secret', 'b', root)})
            assert r.status_code == 403
            r = await client.post('/api/books/a/knowledge/binding/preview', json={'selectionToken': token('secret', 'a', root)})
            assert r.status_code == 200
            payload = {'expectedVersion': 1, 'commandId': 'idempotent', 'scope': {'purpose': 'discussion'}}
            first = await client.put('/api/books/a/knowledge/binding', json=payload)
            second = await client.put('/api/books/a/knowledge/binding', json=payload)
            assert first.json() == second.json()
            payload['scope']['purpose'] = 'prose'
            assert (await client.put('/api/books/a/knowledge/binding', json=payload)).status_code == 409
            doc = (await svc.documents('a'))[0]
            r = await client.post('/api/books/a/knowledge/navigation', json={'documentId': doc['id']})
            assert r.status_code == 403
    finally:
        clear_db(db)
@pytest.mark.asyncio
async def test_restart_and_database_restore_require_fresh_authority(env):
    db, svc, root = env
    # Initialize writes only the host application marker, not the Vault.
    await svc.initialize(db._data_dir)
    row = await svc.binding('a')
    await svc.bind_selected('a', root=str(root), expected_version=row['version'], command_id='host-binding')
    receipt = (await search(svc))['receipts'][0]
    await svc.close()
    from application.novel_knowledge_service import NovelKnowledgeService
    restarted = NovelKnowledgeService(db)
    await restarted.initialize(db._data_dir)
    try:
        assert (await search(restarted))['items']
        payload = await db.export_to_buffer()
        await db.import_from_buffer(payload)
        with pytest.raises(KnowledgeError, match='reauthorization'):
            await restarted.validate('a', [receipt])
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_scope_revocation_blocks_model_calls_without_knowledge_receipts(env):
    _, svc, _ = env
    class MemoryValidator:
        async def validate_evidence(self, receipts, **kwargs):
            pass
    validator = NovelKnowledgeEvidenceValidator(svc, MemoryValidator(), 'a', await svc.scope_snapshot('a'))
    await validator.validate_evidence(())
    row = await svc.binding('a')
    await svc.configure('a', expected_version=row['version'], command_id='revoke-empty', unbind=True)
    with pytest.raises(KnowledgeError):
        await validator.validate_evidence(())


@pytest.mark.asyncio
async def test_fulltext_admission_precedes_candidate_limit(env):
    _, svc, root = env
    # Ineligible higher-ranked matches must not crowd the valid candidate out of recall.
    for i in range(405):
        (root / f'草稿{i}.md').write_text(note('铜钥匙', purr_id=f'draft-{i}', status='draft'))
    result = await svc.search('a', '铜钥匙', mode='fulltext')
    assert result['items']
    assert all(item['title'] == '红门' for item in result['items'])


@pytest.mark.asyncio
async def test_author_scope_survives_new_chapter_without_loosening_prose_or_source_revision(env):
    db, svc, root = env
    prose = await svc.scope_snapshot('a')
    await db.execute("INSERT INTO outline_chapters(id,outline_id,title,sort) VALUES ('new-1','outline-a','新章',4)")
    with pytest.raises(KnowledgeError, match='chapter_order_changed'):
        await svc.check_scope('a', prose)
    result = await search(svc, purpose='discussion')
    discussion = await svc.scope_snapshot('a')
    await db.execute("INSERT INTO outline_chapters(id,outline_id,title,sort) VALUES ('new-2','outline-a','下一章',5)")
    await svc.check_scope('a', discussion)
    await svc.validate('a', result['receipts'])
    (root / '红门.md').write_text(note('红门改用银钥匙。'))
    with pytest.raises(KnowledgeError, match='stale'):
        await svc.validate('a', result['receipts'])
