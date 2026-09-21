"""Archive-only removal with dependency checks and durable retry receipts."""
import os
import shutil

from domains.writing.techniques import TechniqueError, canonical_bytes, digest


def deletion_preview(service, kind, object_id):
    store = service.store(kind)
    with store.barrier():
        record = store.get_record(object_id)
        if record.get('storageScope') != 'library':
            raise TechniqueError('invalid_reference', '只能删除技法库中的内容')
        drafts = [store.get_draft(object_id, draft_id) for draft_id in record['draftIds']]
        references = []
        if kind == 'technique':
            for scheme in service.schemes.list_records(include_archived=True):
                hits = []
                for draft_id in scheme['draftIds']:
                    draft = service.schemes.get_draft(scheme['id'], draft_id)
                    if draft['state'] != 'cancelled' and any(ref.get('id') == object_id for ref in draft['content'].get('members', [])):
                        hits.append('草稿' if draft['state'] == 'editing' else '封存稿')
                for version in scheme.get('publishedVersions', []):
                    content = service.schemes.read_scheme({'kind': 'scheme', 'id': scheme['id'], 'versionId': version})
                    if any(ref.get('id') == object_id for ref in content['members']):
                        hits.append('已发布版本')
                if hits:
                    head = service.schemes.get_draft(scheme['id'], scheme['draftHead'])
                    references.append({'id': scheme['id'], 'name': (scheme.get('metadata') or head['content']).get('name') or '未命名方案', 'sources': sorted(set(hits))})
        versions = store._record_path(object_id).parent / 'versions'
        return {'kind': kind, 'id': object_id, 'archived': record['status'] == 'archived',
                'draftCount': len(drafts), 'versionCount': len(list(versions.iterdir())) if versions.exists() else 0,
                'references': references, 'canDelete': record['status'] == 'archived' and not references,
                'revisionToken': digest(canonical_bytes({'record': record, 'drafts': drafts}))}


def delete_files(service, kind, object_id, *, operation_id, revision_token):
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 512:
        raise TechniqueError('operation_conflict', '删除操作需要稳定的 operationId')
    store = service.store(kind)
    with store.barrier():
        receipt_path = store._deletion_path(object_id)
        source = store._record_path(object_id).parent
        pending = store._path('.deleting', f'{kind}_{store._id(object_id)}')
        request = {'operationId': operation_id, 'revisionToken': revision_token}
        if receipt_path.exists():
            receipt = store._json(receipt_path)
            if receipt['request'] != request:
                raise TechniqueError('operation_conflict', '内容已删除，请刷新列表')
        else:
            preview = deletion_preview(service, kind, object_id)
            if not preview['archived']:
                raise TechniqueError('invalid_reference', '请先归档，再永久删除')
            if preview['references']:
                names = '、'.join(ref['name'] for ref in preview['references'])
                raise TechniqueError('invalid_reference', f'仍被写作方案引用：{names}。请先处理引用；归档方案仍保留引用。')
            if preview['revisionToken'] != revision_token:
                raise TechniqueError('draft_conflict', '内容已改变，请重新确认删除范围')
            store._write_json(receipt_path, {'request': request, 'result': {'deleted': True, 'kind': kind, 'id': object_id}})
        # The receipt fences reads and mutations even if cleanup is interrupted.
        if source.exists():
            pending.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, pending)
        if pending.exists():
            shutil.rmtree(pending)
        return store._json(receipt_path)['result']
