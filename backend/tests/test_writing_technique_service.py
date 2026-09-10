import pytest

from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from domains.writing.techniques import TechniqueError


ENTRY = "---\nname: 视角控制\ndescription: 在误会场景中按人物可知范围分配信息。\n---\n只写人物当前能知道的信息。"


@pytest.fixture
async def service(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield WritingTechniqueService(db)
    finally:
        await db.close()


async def published(service):
    draft = await service.create_draft(operation_id="create")
    object_id, draft_id = draft["techniqueId"], draft["draftId"]
    draft = await service.apply_changes(object_id, draft_id, expected_revision=0, operation_id="write",
        changes=[{"action": "put", "path": "SKILL.md", "content": ENTRY}])
    draft = await service.seal("technique", object_id, draft_id, expected_revision=1,
                               expected_tree_digest=draft["treeDigest"], operation_id="seal")
    await service.publish("technique", object_id, ref=draft["sealedRef"], expected_published_head=None, operation_id="publish")
    return draft["sealedRef"]


async def test_manual_default_and_session_inheritance_are_independent(service):
    assert await service.get_mode("book", "book") == "manual"
    assert await service.initialize_session("old", "book") == "manual"
    await service.set_mode("book", "book", "auto")
    assert await service.initialize_session("old", "book") == "manual"
    assert await service.initialize_session("new", "book") == "auto"
    await service.set_mode("session", "new", "manual")
    assert await service.get_mode("book", "book") == "auto"


async def test_scheme_full_version_and_index_rebuild_preserve_business_state(service):
    ref = await published(service)
    content = {"schemaVersion": 1, "name": "误会场景", "description": "用于双方掌握信息不同的对话", "composition": "按当前人物视角使用。", "members": [ref]}
    draft = await service.create_draft(kind="scheme", operation_id="scheme", content=content)
    scheme_id, draft_id = draft["schemeId"], draft["draftId"]
    draft = await service.seal("scheme", scheme_id, draft_id, expected_revision=0,
                               expected_tree_digest=draft["treeDigest"], operation_id="seal")
    await service.publish("scheme", scheme_id, ref=draft["sealedRef"], expected_published_head=None, operation_id="publish")
    assert service.schemes.read_scheme(draft["sealedRef"], verify_members=True) == content
    await service.set_mode("book", "book", "auto")
    await service.db.execute("DELETE FROM writing_technique_catalog")
    assert len(await service.rebuild_index()) == 2
    assert await service.get_mode("book", "book") == "auto"
    assert (await service.get_object("scheme", scheme_id))["publishedHead"] == draft["sealedRef"]["versionId"]
    await service.set_status("technique", ref["id"], "archived", operation_id="archive")
    assert not await service.list_objects("technique")
    assert service.techniques.read_version_file(ref, "SKILL.md")["content"] == ENTRY
    with pytest.raises(TechniqueError):
        service.schemes.read_scheme(draft["sealedRef"], verify_members=True)


async def test_files_commit_before_index_and_operation_retry_recovers(service, monkeypatch):
    original = service._index

    async def fail(*_):
        raise OSError("index unavailable")

    monkeypatch.setattr(service, "_index", fail)
    with pytest.raises(OSError):
        await service.create_draft(operation_id="recover")
    monkeypatch.setattr(service, "_index", original)
    recovered = await service.create_draft(operation_id="recover")
    assert len(await service.list_objects("technique")) == 1
    assert (await service.get_object("technique", recovered["techniqueId"]))["draft"]["draftId"] == recovered["draftId"]


async def test_scheme_exchange_keeps_members_unpublished_until_review_and_replays(service):
    from application.writing_technique_exchange import export_scheme_bundle, import_scheme_bundle, preview_scheme_bundle
    ref = await published(service)
    content = {"schemaVersion": 1, "name": "视角方案", "description": "按人物信息安排对话", "composition": "先确定人物知道什么。", "members": [ref]}
    draft = await service.create_draft(kind="scheme", operation_id="original-scheme", content=content)
    sealed = await service.seal("scheme", draft["schemeId"], draft["draftId"], expected_revision=0,
        expected_tree_digest=draft["treeDigest"], operation_id="seal-original")
    raw = export_scheme_bundle(service, sealed["sealedRef"])
    bundle = preview_scheme_bundle(raw, service.techniques.limits)
    imported = await import_scheme_bundle(service, bundle=bundle, operation_id="import-bundle")
    assert await import_scheme_bundle(service, bundle=bundle, operation_id="import-bundle") == imported
    member = imported["content"]["members"][0]
    assert member["id"] != ref["id"] and member["versionId"] == ref["versionId"]
    assert service.techniques.read_version_file(member, "SKILL.md")["content"] == ENTRY
    assert (await service.get_object("technique", member["id"]))["publishedHead"] is None
    with pytest.raises(TechniqueError, match="已发布"):
        await service.seal("scheme", imported["schemeId"], imported["draftId"], expected_revision=0,
            expected_tree_digest=imported["treeDigest"], operation_id="seal-imported")
    assert not await service.db.fetch_all("SELECT * FROM writing_technique_grants")
    bundle["techniques"][0]["files"]["SKILL.md"] += "tampered"
    from domains.writing.techniques import canonical_bytes
    with pytest.raises(TechniqueError, match="版本校验"):
        preview_scheme_bundle(canonical_bytes(bundle), service.techniques.limits)


async def test_archive_restore_and_permanent_delete_retry(service):
    ref = await published(service)
    object_id = ref['id']
    with pytest.raises(TechniqueError, match='先归档'):
        await service.delete_object('technique', object_id, operation_id='delete',
            revision_token=(await service.deletion_preview('technique', object_id))['revisionToken'])
    await service.set_status('technique', object_id, 'archived', operation_id='archive')
    assert not await service.list_objects('technique')
    assert len(await service.list_objects('technique', include_archived=True)) == 1
    await service.set_status('technique', object_id, 'active', operation_id='restore')
    assert len(await service.list_objects('technique')) == 1
    await service.set_status('technique', object_id, 'archived', operation_id='archive-again')
    preview = await service.deletion_preview('technique', object_id)
    assert preview['canDelete'] and preview['draftCount'] == 1 and preview['versionCount'] == 1
    values = dict(operation_id='delete', revision_token=preview['revisionToken'])
    deleted = await service.delete_object('technique', object_id, **values)
    assert await service.delete_object('technique', object_id, **values) == deleted
    assert not (service.root / 'techniques' / object_id).exists()
    assert not await service.list_objects('technique', include_archived=True)
    assert not await service.rebuild_index()
    with pytest.raises(TechniqueError, match='永久删除'):
        await service.create_draft(operation_id='create')
    with pytest.raises(TechniqueError):
        service.techniques.read_version_file(ref, 'SKILL.md')


async def test_delete_blocks_scheme_draft_and_archived_published_references(service):
    ref = await published(service)
    content = {'schemaVersion': 1, 'name': '引用方案', 'description': '测试', 'composition': '', 'members': [ref]}
    draft = await service.create_draft(kind='scheme', operation_id='scheme', content=content)
    sealed = await service.seal('scheme', draft['schemeId'], draft['draftId'], expected_revision=0,
        expected_tree_digest=draft['treeDigest'], operation_id='seal-scheme')
    await service.publish('scheme', draft['schemeId'], ref=sealed['sealedRef'], expected_published_head=None, operation_id='publish-scheme')
    await service.set_status('scheme', draft['schemeId'], 'archived', operation_id='archive-scheme')
    await service.set_status('technique', ref['id'], 'archived', operation_id='archive-technique')
    preview = await service.deletion_preview('technique', ref['id'])
    assert not preview['canDelete']
    assert preview['references'][0]['name'] == '引用方案'
    with pytest.raises(TechniqueError, match='仍被写作方案引用'):
        await service.delete_object('technique', ref['id'], operation_id='delete-technique', revision_token=preview['revisionToken'])
    scheme_preview = await service.deletion_preview('scheme', draft['schemeId'])
    await service.delete_object('scheme', draft['schemeId'], operation_id='delete-scheme', revision_token=scheme_preview['revisionToken'])
    assert service.techniques.read_version_file(ref, 'SKILL.md')['content'] == ENTRY
    preview = await service.deletion_preview('technique', ref['id'])
    assert preview['canDelete']
    await service.delete_object('technique', ref['id'], operation_id='delete-technique', revision_token=preview['revisionToken'])
    with pytest.raises(TechniqueError, match='永久删除'):
        await service.create_draft(kind='scheme', operation_id='stale-client', content=content)


async def test_delete_rechecks_new_references_and_stale_confirmation(service):
    ref = await published(service)
    await service.set_status('technique', ref['id'], 'archived', operation_id='archive')
    preview = await service.deletion_preview('technique', ref['id'])
    await service.set_status('technique', ref['id'], 'active', operation_id='restore')
    await service.set_status('technique', ref['id'], 'archived', operation_id='archive-again')
    with pytest.raises(TechniqueError, match='内容已改变'):
        await service.delete_object('technique', ref['id'], operation_id='delete', revision_token=preview['revisionToken'])
    preview = await service.deletion_preview('technique', ref['id'])
    await service.create_draft(kind='scheme', operation_id='new-reference', content={
        'schemaVersion': 1, 'name': '新引用', 'description': '测试', 'composition': '', 'members': [ref]})
    with pytest.raises(TechniqueError, match='新引用'):
        await service.delete_object('technique', ref['id'], operation_id='delete', revision_token=preview['revisionToken'])
    assert (service.root / 'techniques' / ref['id']).exists()


async def test_delete_cleanup_failure_can_retry_without_resurrection(service, monkeypatch):
    import application.writing_technique_deletion as deletion
    ref = await published(service)
    await service.set_status('technique', ref['id'], 'archived', operation_id='archive')
    preview = await service.deletion_preview('technique', ref['id'])
    original = deletion.shutil.rmtree
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(deletion.shutil, 'rmtree', fail)
    args = dict(operation_id='delete', revision_token=preview['revisionToken'])
    with pytest.raises(OSError):
        await service.delete_object('technique', ref['id'], **args)
    assert not await service.list_objects('technique', include_archived=True)
    with pytest.raises(TechniqueError, match='永久删除'):
        await service.set_status('technique', ref['id'], 'active', operation_id='restore')
    monkeypatch.setattr(deletion.shutil, 'rmtree', original)
    assert (await service.delete_object('technique', ref['id'], **args))['deleted']
    assert not (service.root / '.deleting' / f"technique_{ref['id']}").exists()


async def test_archive_and_delete_http_contract(service, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import routers.writing_techniques as routes
    monkeypatch.setattr(routes, '_service', lambda: service)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api')
    ref = await published(service)
    base = f"/api/writing-techniques/objects/technique/{ref['id']}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.put(base + '/status', json={'status': 'archived', 'operationId': 'archive'})).status_code == 200
        preview = (await client.get(base + '/deletion-preview')).json()['data']
        assert preview['canDelete']
        response = await client.post(base + '/delete', json={'revisionToken': preview['revisionToken'], 'operationId': 'delete'})
        assert response.json()['data']['deleted']
        assert (await client.get('/api/writing-techniques/objects/technique?includeArchived=true')).json()['data'] == []
        assert (await client.post(base + '/delete', json={'revisionToken': preview['revisionToken'], 'operationId': 'delete'})).json()['data']['deleted']


async def test_analysis_library_tracks_unpublished_and_archived_drafts(service, monkeypatch):
    from routers import writing_techniques as router
    monkeypatch.setattr(router, '_service', lambda: service)
    candidate = await service.create_draft(operation_id='candidate',
        owner={'analysisId': 'analysis-a'}, storage_scope='analysis_candidate')
    draft = await service.create_draft(operation_id='library', owner={'analysisId': 'analysis-a'})
    await service.create_draft(operation_id='unrelated', owner={'analysisId': 'analysis-b'})
    result = await router.analysis_library('analysis-a')
    assert result['success']
    assert [item['id'] for item in result['data']] == [draft['techniqueId']]
    assert candidate['techniqueId'] not in [item['id'] for item in result['data']]
    assert result['data'][0]['status'] == 'active'
    await service.set_status('technique', draft['techniqueId'], 'archived', operation_id='archive-library')
    assert (await router.analysis_library('analysis-a'))['data'][0]['status'] == 'archived'
    assert (await router.analysis_library('unknown'))['data'] == []
