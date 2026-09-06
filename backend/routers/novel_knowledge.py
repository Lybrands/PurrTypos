"""Management endpoints are never model tools; binding paths require desktop proof."""
import hmac
import os

from fastapi import APIRouter, Header, Query
from application.novel_knowledge_service import get_novel_knowledge_service
from dependencies import get_db
from domains.writing.knowledge import KnowledgeError
from exceptions import AppError
from schemas.novel_knowledge import BindingRequest, ConfigureRequest, NavigationRequest, SearchRequest, SelectionRequest

router = APIRouter(prefix='/books/{book_id}/knowledge', tags=['novel-knowledge'])


def service():
    return get_novel_knowledge_service(get_db())


async def call(awaitable):
    try:
        return {'success': True, 'data': await awaitable}
    except KnowledgeError as error:
        raise AppError(error.code, error.status_code) from error
    except (OSError, UnicodeError):
        raise AppError('knowledge_file_unavailable', 503) from None


@router.post('/binding/preview')
async def preview(book_id: str, body: SelectionRequest):
    return await call(service().preview_selection(book_id, body.selectionToken))


@router.post('/binding')
async def bind(book_id: str, body: BindingRequest):
    return await call(service().bind(book_id, token=body.selectionToken, expected_version=body.expectedVersion, command_id=body.commandId))


@router.get('/binding')
async def status(book_id: str):
    return await call(service().status(book_id))


@router.put('/binding')
async def configure(book_id: str, body: ConfigureRequest):
    return await call(service().configure(book_id, expected_version=body.expectedVersion, command_id=body.commandId,
        scope=body.scope.model_dump(exclude_none=True) if body.scope else None, semantic=body.semantic, unbind=body.unbind))


@router.post('/index')
async def refresh(book_id: str):
    return await call(service().refresh(book_id))


@router.post('/index/semantic')
async def semantic_index(book_id: str):
    async def index():
        svc = service()
        await svc.refresh(book_id)
        binding = await svc.binding(book_id, required=True)
        if not svc.vector:
            raise KnowledgeError('semantic_unavailable', 503)
        return await svc.vector.index(binding)
    return await call(index())


@router.get('/documents')
async def documents(book_id: str):
    return await call(service().documents(book_id))


@router.get('/documents/{document_id}')
async def source(book_id: str, document_id: str, revision: str | None = Query(default=None, max_length=100)):
    return await call(service().source(book_id, document_id, revision))


@router.post('/search')
async def search(book_id: str, body: SearchRequest):
    async def run():
        result = await service().search(book_id, body.query, mode=body.mode)
        return {**result, 'receipts': [r.to_mapping() for r in result['receipts']]}
    return await call(run())


@router.post('/navigation')
async def navigation(book_id: str, body: NavigationRequest, x_knowledge_host: str = Header(default='')):
    secret = os.environ.get('PURRTYPOS_KNOWLEDGE_HOST_SECRET', '')
    if not secret or not hmac.compare_digest(secret, x_knowledge_host):
        raise AppError('desktop_navigation_required', 403)
    return await call(service().navigation(book_id, body.documentId, body.revision, body.anchor))


@router.get('/usage')
async def usage(book_id: str):
    async def read():
        import json
        svc = service()
        await svc.book(book_id)
        rows = await svc.db.fetch_all('''SELECT e.id,e.run_id,e.payload_json,e.create_time FROM ai_agent_run_events e
            JOIN ai_agent_runs r ON r.id=e.run_id WHERE e.event_type='stream.opened'
            AND json_extract(r.binding_attributes_json,'$.bookId')=?
            AND json_extract(r.binding_attributes_json,'$.agentProfile')='writing'
            ORDER BY e.id DESC LIMIT 100''', [book_id])
        items = []
        for row in rows:
            payload = json.loads(row['payload_json'])
            for receipt in payload.get('contextEvidence', []):
                if receipt.get('source') != 'novel_knowledge/v1':
                    continue
                meta = receipt.get('metadata', {})
                if meta.get('bookId') != book_id:
                    continue
                items.append({'eventId': row['id'], 'runId': row['run_id'], 'recordedAt': row['create_time'],
                    'documentId': meta.get('documentId'), 'revision': meta.get('revision'),
                    'title': meta.get('title'), 'locator': meta.get('locator'), 'scope': meta.get('scope'),
                    'reasons': meta.get('reasons'), 'evidenceId': receipt.get('evidenceId')})
        return items
    return await call(read())
