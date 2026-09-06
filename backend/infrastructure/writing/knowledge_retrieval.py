"""PurrA public retrievers with authority resolved from persisted writing Runs."""
import json
from dataclasses import dataclass

from purra.retrieval import RetrievalError, RetrievalHit
from infrastructure.writing.retrieval import _authorized_book
from domains.writing.knowledge import KnowledgeError


@dataclass(frozen=True)
class NovelKnowledgeRetriever:
    db: object
    knowledge: object
    read: bool = False

    async def retrieve(self, request, signal=None):
        book_id = await _authorized_book(self.db, request, signal, limit=12)
        run = await self.db.fetch_one('SELECT binding_attributes_json FROM ai_agent_runs WHERE id=?', [request.run_id])
        scope = json.loads(run['binding_attributes_json']).get('novelKnowledgeScope')
        if not scope:
            raise RetrievalError('Knowledge was not bound when this Run started', code='retrieval_access_denied')
        kwargs = {}
        if self.read:
            try:
                args = json.loads(request.query)
                if not isinstance(args, dict) or set(args) - {'documentId', 'revision', 'chunkId'} or not args.get('documentId') or not args.get('revision'):
                    raise ValueError()
                kwargs = {'document_id': args['documentId'], 'revision': args['revision'], 'chunk_id': args.get('chunkId')}
            except (ValueError, TypeError):
                raise RetrievalError('Read requires documentId and revision', code='retrieval_access_denied') from None
        try:
            result = await self.knowledge.search(book_id, request.query, scope=scope, limit=request.limit,
                                                  token_budget=3000, signal=signal, context_block='readNovelKnowledge' if self.read else 'searchNovelKnowledge', **kwargs)
        except KnowledgeError as error:
            raise RetrievalError(error.code, code=error.code) from error
        return tuple(RetrievalHit(id=item['id'], content=item['content'], source=receipt.source,
            version=receipt.version, metadata={**dict(receipt.metadata), 'evidenceId': receipt.evidence_id})
            for item, receipt in zip(result['items'], result['receipts']))


def guard_legacy_registration(registration, db, knowledge):
    """Enforce persisted time scope even if a recovered tool allowlist is stale."""
    from dataclasses import replace
    from purra.contracts import ToolHandlerResult
    name = registration.schema.name
    blocked = {
        'getBookCharacters', 'listBookCharacters', 'getSettingEntities', 'listSettingEntities',
        'getStoryBackground', 'searchMemories', 'searchSparkIdeas', 'getGlobalOutline',
        'queryOutline', 'listOutlines', 'getStoryHealthDashboard', 'updateCharacter',
        'updateSettingEntity', 'editStoryBackground', 'updateOutline', 'editGlobalOutline',
    }

    async def check(state, arguments):
        if not state.run_id:
            return None
        run = await db.fetch_one('SELECT binding_attributes_json FROM ai_agent_runs WHERE id=?', [state.run_id])
        attrs = json.loads(run['binding_attributes_json'] or '{}') if run else {}
        scope = attrs.get('novelKnowledgeScope')
        if not scope:
            return None
        book_id = attrs.get('bookId')
        try:
            _, chapters = await knowledge.check_scope(book_id, scope)
            if scope.get('purpose') != 'discussion':
                if name in blocked:
                    return 'knowledge_historical_projection_unavailable'
                if name in ('getChapterContent', 'batchGetChapterContents', 'editChapterContent'):
                    target = arguments.get('chapterId') or scope.get('chapterId')
                    if target in ('当前章节', '当前章', '本章'):
                        target = scope.get('chapterId')
                    ids = arguments.get('chapterIds', []) if name == 'batchGetChapterContents' else [target]
                    current = scope.get('chapterId')
                    if current not in chapters or any(i not in chapters or chapters.index(i) > chapters.index(current) for i in ids):
                        return 'knowledge_chapter_outside_scope'
        except KnowledgeError as error:
            return error.code
        return None

    async def handler(state, arguments, signal=None):
        reason = await check(state, arguments)
        if reason:
            return ToolHandlerResult(json.dumps({'error': reason}), error_code=reason)
        return await registration.handler(state, arguments, signal)

    async def call_handler(state, arguments, tool_call, signal=None):
        reason = await check(state, arguments)
        if reason:
            return ToolHandlerResult(json.dumps({'error': reason}), error_code=reason)
        return await registration.call_handler(state, arguments, tool_call, signal)

    return replace(registration, handler=handler, call_handler=call_handler if registration.call_handler else None)


def read_knowledge_registration(registration, parameters):
    """Expose typed document locators while retaining the public RetrieverTool boundary."""
    from dataclasses import replace
    from purra.contracts import ToolDataContract

    async def handler(state, arguments, signal=None):
        return await registration.handler(state, {'query': json.dumps(dict(arguments), ensure_ascii=False)}, signal)

    return replace(registration, schema=replace(registration.schema, parameters=parameters), handler=handler,
        data_contract=ToolDataContract(model_owned_paths=('documentId', 'revision', 'chunkId'),
                                       host_bound_paths=('limit', 'scope'), host_derived_paths=('run_id',)))
