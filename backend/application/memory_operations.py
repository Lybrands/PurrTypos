"""Book-scoped application operations over the PurrA memory component."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from purra.contracts import (
    AgentMessage,
    ContextEvidenceReceipt,
    ModelCompletion,
)
from purra_mem0 import (
    MemoryBudget,
    MemoryError,
    MemoryRecord,
    MemoryRef,
    MemoryResolution,
    MemoryScope,
    MemorySource,
    assemble_memory_context,
)
from purra.retrieval import RetrievalRequest

from application.model_request_service import ModelRequestService, BackgroundModelContext
from infrastructure.memory import MemoryResourceError
from services.model_settings_service import (
    get_setting_value,
)


LOCAL_USER_ID = "purrtypos-local-user"
MEMORY_MODEL_ID_KEY = "memory_model_id"
MEMORY_KINDS = frozenset({
    "canon",
    "plot",
    "character",
    "world",
    "foreshadowing",
    "style",
    "summary",
})
MEMORY_SCOPE_TYPES = frozenset({"book", "chapter", "character", "outline"})


class MemoryOperationError(RuntimeError):
    """Stable, redacted application failure for every memory entry point."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _required_text(value: Any, *, code: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise MemoryOperationError(code)
    return text


def memory_metadata(
    *,
    kind: str,
    scope_type: str = "book",
    scope_id: str | None = None,
    summary: str = "",
    keywords: str = "",
    importance: int = 3,
    confidence: float = 1.0,
    pinned: bool = False,
) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip()
    normalized_scope = str(scope_type or "").strip()
    if normalized_kind not in MEMORY_KINDS:
        raise MemoryOperationError("memory_kind_invalid")
    if normalized_scope not in MEMORY_SCOPE_TYPES:
        raise MemoryOperationError("memory_scope_invalid")
    if type(importance) is not int or not 1 <= importance <= 5:
        raise MemoryOperationError("memory_importance_invalid")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise MemoryOperationError("memory_confidence_invalid")
    normalized_confidence = float(confidence)
    if not 0 <= normalized_confidence <= 1:
        raise MemoryOperationError("memory_confidence_invalid")
    normalized_scope_id = None
    if scope_id is not None:
        normalized_scope_id = _required_text(
            scope_id,
            code="memory_scope_id_invalid",
            maximum=512,
        )
    if normalized_scope != "book" and normalized_scope_id is None:
        raise MemoryOperationError("memory_scope_id_required")
    return {
        "kind": normalized_kind,
        "scopeType": normalized_scope,
        "scopeId": normalized_scope_id,
        "summary": str(summary or "").strip()[:500],
        "keywords": str(keywords or "").strip()[:500],
        "importance": importance,
        "confidence": normalized_confidence,
        "pinned": bool(pinned),
    }


def memory_record_dict(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "version": record.version,
        "state": record.state,
        "text": record.text,
        "source": {
            "id": record.source.id,
            "revision": record.source.revision,
        },
        "inferred": record.inferred,
        "expiresAt": record.expires_at,
        "metadata": dict(record.metadata),
        "reason": record.reason,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "resolutionKey": record.resolution_key,
    }


def _ref_dict(ref: MemoryRef) -> dict[str, Any]:
    return {"id": ref.id, "version": ref.version}


def _operation_dict(operation) -> dict[str, Any]:
    review = operation.review
    resolution = operation.resolution
    return {
        "key": operation.key,
        "state": operation.state,
        "ids": list(operation.ids),
        "review": (
            {
                "key": review.key,
                "candidate": _ref_dict(review.candidate),
                "epoch": review.epoch,
                "matches": [
                    {"item": _ref_dict(match.item), "kind": match.kind}
                    for match in review.matches
                ],
                "proposal": (
                    None
                    if review.proposal is None
                    else {
                        "kind": review.proposal.kind,
                        "items": [
                            _ref_dict(item) for item in review.proposal.items
                        ],
                        "keep": review.proposal.keep,
                        "reviewKey": review.proposal.review_key,
                    }
                ),
            }
            if review is not None
            else None
        ),
        "resolution": (
            {
                "kind": resolution.kind,
                "items": [_ref_dict(item) for item in resolution.items],
                "keep": resolution.keep,
                "reviewKey": resolution.review_key,
            }
            if resolution is not None
            else None
        ),
    }


class MemoryApplicationService:
    """The only PurrTypos application boundary allowed to mutate memory."""

    def __init__(self, db, resource) -> None:
        self._db = db
        self._resource = resource

    async def create_manual(
        self,
        *,
        book_id: str,
        operation_key: str,
        text: str,
        metadata: Mapping[str, Any],
        state: str = "active",
    ) -> dict[str, Any]:
        key = self._key("api-create", operation_key)
        source = MemorySource(f"manual:{operation_key}", "1")
        return await self.add_source(
            book_id=book_id,
            key=key,
            text=text,
            source=source,
            metadata=metadata,
            state=state,
        )

    async def add_source(
        self,
        *,
        book_id: str,
        key: str,
        text: str,
        source: MemorySource,
        metadata: Mapping[str, Any],
        state: str = "active",
        reason: str | None = None,
    ) -> dict[str, Any]:
        clean_text = _required_text(
            text,
            code="memory_text_invalid",
            maximum=20_000,
        )
        clean_key = self._trusted_key(key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                receipt = await memory.add(
                    clean_text,
                    source=source,
                    key=clean_key,
                    metadata=dict(metadata),
                    state=state,
                    reason=reason,
                )
                return await self._receipt_record(memory, receipt)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def extract_source(
        self,
        *,
        book_id: str,
        key: str,
        messages: Sequence[Mapping[str, str]],
        source: MemorySource,
        metadata: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        clean_key = self._trusted_key(key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=True, book_id=book_id)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=True,
            ) as memory:
                receipt = await memory.extract(
                    messages,
                    source=source,
                    key=clean_key,
                    metadata=dict(metadata),
                )
                records = []
                for item_id in receipt.ids:
                    record = await memory.get(item_id, include_inactive=True)
                    if record is None:
                        raise MemoryOperationError("memory_receipt_invalid")
                    records.append(memory_record_dict(record))
                return records
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def update(
        self,
        *,
        book_id: str,
        item_id: str,
        version: int,
        operation_key: str,
        text: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        clean_key = self._trusted_key(operation_key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                current = await memory.get(item_id, include_inactive=True)
                if current is None:
                    raise MemoryOperationError("memory_not_found")
                if text is None:
                    if metadata is None:
                        raise MemoryOperationError("memory_update_empty")
                    receipt = await memory.annotate(
                        item_id,
                        dict(metadata),
                        version=version,
                        key=clean_key,
                    )
                else:
                    source = MemorySource(
                        f"memory-edit:{operation_key}",
                        str(version + 1),
                    )
                    kwargs = {} if metadata is None else {"metadata": dict(metadata)}
                    receipt = await memory.update(
                        item_id,
                        _required_text(
                            text,
                            code="memory_text_invalid",
                            maximum=20_000,
                        ),
                        version=version,
                        source=source,
                        key=clean_key,
                        **kwargs,
                    )
                return await self._receipt_record(memory, receipt)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def set_state(
        self,
        *,
        book_id: str,
        item_id: str,
        version: int,
        operation_key: str,
        state: str,
        reason: str,
    ) -> dict[str, Any]:
        clean_key = self._trusted_key(operation_key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                receipt = await memory.set_state(
                    item_id,
                    state,
                    version=version,
                    key=clean_key,
                    reason=reason,
                )
                return await self._receipt_record(memory, receipt)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def link(
        self,
        *,
        book_id: str,
        from_ref: MemoryRef,
        to_ref: MemoryRef,
        relation: str,
        note: str,
        operation_key: str,
    ) -> dict[str, Any]:
        clean_key = self._trusted_key(operation_key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                receipt = await memory.link(
                    from_ref,
                    to_ref,
                    relation,
                    key=clean_key,
                    note=note,
                )
                return {
                    "key": receipt.key,
                    "state": receipt.state,
                    "ids": list(receipt.ids),
                }
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def review(
        self,
        *,
        book_id: str,
        candidate: MemoryRef,
        operation_key: str,
        signal=None,
    ) -> dict[str, Any]:
        clean_key = self._key("review", operation_key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=True, book_id=book_id)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=True,
            ) as memory:
                operation = await memory.review(
                    candidate,
                    key=clean_key,
                    instructions=(
                        "Evaluate story continuity only. Do not infer that similar "
                        "wording is the same business event. Return uncertain when "
                        "the evidence cannot support a resolution."
                    ),
                    signal=signal,
                )
                return _operation_dict(operation)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def resolve(
        self,
        *,
        book_id: str,
        resolution: MemoryResolution,
        operation_key: str,
        signal=None,
    ) -> dict[str, Any]:
        clean_key = self._key("resolve", operation_key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                operation = await memory.resolve(
                    resolution,
                    key=clean_key,
                    signal=signal,
                )
                return _operation_dict(operation)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def delete(
        self,
        *,
        book_id: str,
        item_id: str,
        version: int,
        operation_key: str,
        signal=None,
    ) -> dict[str, Any]:
        clean_key = self._key("delete", operation_key)
        await self._require_book(book_id)
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                operation = await memory.delete(
                    item_id,
                    version=version,
                    key=clean_key,
                    signal=signal,
                )
                return _operation_dict(operation)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def history(
        self,
        *,
        book_id: str,
        item_id: str,
        signal=None,
    ) -> tuple[Mapping[str, Any], ...]:
        await self._require_book(book_id)
        providers = self._providers(f"history:{book_id}", inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                rows = await memory.history(item_id, signal=signal)
                return tuple(dict(row) for row in rows)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def links(
        self,
        *,
        book_id: str,
        item_id: str,
        limit: int = 20,
        after: str | None = None,
        signal=None,
    ) -> dict[str, Any]:
        await self._require_book(book_id)
        providers = self._providers(f"links:{book_id}", inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                page = await memory.links(
                    item_id,
                    limit=max(1, min(32, int(limit))),
                    after=after,
                    signal=signal,
                )
                return {
                    "items": [
                        {
                            "key": item.key,
                            "from": _ref_dict(item.from_ref),
                            "to": _ref_dict(item.to_ref),
                            "relation": item.relation,
                            "note": item.note,
                            "valid": item.valid,
                        }
                        for item in page.items
                    ],
                    "next": page.next,
                    "epoch": page.epoch,
                }
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def revoke_source(
        self,
        *,
        book_id: str,
        source_id: str,
        operation_key: str,
        revision: str | None = None,
    ) -> dict[str, Any]:
        clean_key = self._trusted_key(operation_key)
        clean_book = _required_text(
            book_id,
            code="memory_book_invalid",
            maximum=512,
        )
        providers = self._providers(clean_key, inference=False)
        try:
            async with self._resource_memory(
                clean_book,
                providers=providers,
                allow_inference=False,
            ) as memory:
                receipt = await memory.revoke_source(
                    source_id,
                    revision=revision,
                    key=clean_key,
                )
                return {
                    "key": receipt.key,
                    "state": receipt.state,
                    "ids": list(receipt.ids),
                }
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def delete_book_scope(
        self,
        *,
        book_id: str,
        operation_key: str,
        signal=None,
    ) -> dict[str, Any]:
        """Delete every live component record after the owning book is gone."""

        clean_book = _required_text(
            book_id,
            code="memory_book_invalid",
            maximum=512,
        )
        clean_key = self._key("delete-book", operation_key)
        providers = self._providers(clean_key, inference=False)
        deleted: list[str] = []
        try:
            async with self._resource_memory(
                clean_book,
                providers=providers,
                allow_inference=False,
            ) as memory:
                for state in ("active", "pending", "disabled"):
                    after = None
                    while True:
                        page = await memory.list(
                            state=state,
                            limit=32,
                            after=after,
                            scan_limit=5_000,
                            signal=signal,
                        )
                        for record in page.items:
                            item_key = hashlib.sha256(
                                record.id.encode("utf-8")
                            ).hexdigest()[:24]
                            await memory.delete(
                                record.id,
                                version=record.version,
                                key=f"{clean_key}:{item_key}",
                                signal=signal,
                            )
                            deleted.append(record.id)
                        after = page.next
                        if after is None:
                            break
                return {
                    "key": clean_key,
                    "state": "complete",
                    "ids": deleted,
                }
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def get(
        self,
        *,
        book_id: str,
        item_id: str,
        include_inactive: bool = True,
    ) -> dict[str, Any] | None:
        await self._require_book(book_id)
        key = self._trusted_key(f"read:{book_id}")
        providers = self._providers(key, inference=False)
        try:
            async with self._resource_memory(
                book_id,
                providers=providers,
                allow_inference=False,
            ) as memory:
                record = await memory.get(
                    item_id,
                    include_inactive=include_inactive,
                )
                if record is None:
                    return None
                projected = memory_record_dict(record)
                return (
                    projected
                    if await self._source_is_current(book_id, projected)
                    else None
                )
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def retrieve(self, *, book_id: str, request: RetrievalRequest, signal=None):
        clean_book = await self._require_book(book_id)
        run_key = _required_text(
            request.run_id,
            code="memory_run_id_required",
            maximum=512,
        )
        resource = self._require_resource()
        budget = MemoryBudget(
            key=f"budget:retrieval:{run_key}",
            max_llm_calls=0,
            max_embedding_calls=100,
            max_input_chars=400_000,
            max_output_tokens=0,
            result_capacity_target_tokens=1,
        )
        providers = resource.providers(
            budget=budget,
            complete=self._unexpected_completion,
        )
        try:
            async with self._resource_memory(
                clean_book,
                providers=providers,
                allow_inference=False,
            ) as memory:
                hits = await memory.retrieve(
                    RetrievalRequest(
                        query=request.query,
                        limit=min(32, max(1, int(request.limit))),
                        scope=request.scope,
                        run_id=request.run_id,
                    ),
                    signal=signal,
                )
                visible = []
                for hit in hits:
                    source_id = str(hit.metadata.get("sourceId") or "")
                    source_revision = str(
                        hit.metadata.get("sourceRevision") or ""
                    )
                    head = await self._db.fetch_one(
                        "SELECT revision, deleted FROM memory_source_heads "
                        "WHERE book_id = ? AND source_id = ?",
                        [clean_book, source_id],
                    )
                    if head is not None and (
                        bool(head["deleted"])
                        or str(head["revision"]) != source_revision
                    ):
                        continue
                    visible.append(hit)
                return tuple(visible)
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def list_records(
        self,
        *,
        book_id: str,
        states: Sequence[str] = (),
        kinds: Sequence[str] = (),
        query: str = "",
        limit: int = 500,
    ) -> tuple[dict[str, Any], ...]:
        clean_book = await self._require_book(book_id)
        requested_states = tuple(dict.fromkeys(str(value) for value in states))
        if any(value not in {"active", "pending", "disabled"} for value in requested_states):
            raise MemoryOperationError("memory_state_invalid")
        maximum = max(1, min(500, int(limit)))
        filters = {"kind": list(dict.fromkeys(kinds))} if kinds else None
        providers = self._providers(f"list:{clean_book}", inference=False)
        records: list[dict[str, Any]] = []
        try:
            async with self._resource_memory(
                clean_book,
                providers=providers,
                allow_inference=False,
            ) as memory:
                for state in requested_states or (None,):
                    after = None
                    while len(records) < maximum:
                        page = await memory.list(
                            state=state,
                            limit=min(32, maximum - len(records)),
                            after=after,
                            filters=filters,
                            query=query,
                            scan_limit=5_000,
                        )
                        for item in page.items:
                            projected = memory_record_dict(item)
                            if await self._source_is_current(clean_book, projected):
                                records.append(projected)
                        after = page.next
                        if after is None:
                            break
            return tuple(records[:maximum])
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def build_context(
        self,
        *,
        book_id: str,
        operation_key: str,
        query: str,
        selected_ids: Sequence[str] = (),
        token_budget: int,
        recall_limit: int,
        signal=None,
    ):
        clean_book = await self._require_book(book_id)
        clean_key = self._key("context", operation_key)
        resource = self._require_resource()
        budget = MemoryBudget(
            key=f"budget:{clean_key}",
            max_llm_calls=0,
            max_embedding_calls=2,
            max_input_chars=20_000,
            max_output_tokens=0,
            result_capacity_target_tokens=1,
        )
        providers = resource.providers(
            budget=budget,
            complete=self._unexpected_completion,
        )
        try:
            async with self._resource_memory(
                clean_book,
                providers=providers,
                allow_inference=False,
            ) as memory:
                epoch = memory.epoch
                recalled = await memory.retrieve(
                    RetrievalRequest(
                        query=query,
                        limit=min(32, max(1, int(recall_limit))),
                    ),
                    signal=signal,
                ) if query.strip() else ()
                ordered_ids = list(dict.fromkeys((
                    *(str(value) for value in selected_ids),
                    *(hit.id for hit in recalled),
                )))
                if len(ordered_ids) > 32:
                    raise MemoryOperationError("memory_selection_too_large")
                current_ids = []
                for item_id in ordered_ids:
                    record = await memory.get(item_id)
                    if record is None:
                        continue
                    if await self._source_is_current(
                        clean_book, memory_record_dict(record)
                    ):
                        current_ids.append(item_id)
                return await assemble_memory_context(
                    memory,
                    tuple(current_ids),
                    token_budget,
                    name="writing.memory",
                    expected_epoch=epoch,
                    signal=signal,
                )
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def validate_evidence(
        self,
        *,
        book_id: str,
        receipts: Sequence[ContextEvidenceReceipt],
        signal=None,
    ) -> None:
        """Revalidate only memory receipts authorized for this book."""

        clean_book = await self._require_book(book_id)
        expected_source = "mem0/" + MemoryScope(
            LOCAL_USER_ID,
            clean_book,
        ).namespace
        memory_receipts: list[ContextEvidenceReceipt] = []
        for receipt in receipts:
            source = str(receipt.source or "")
            if not source.startswith("mem0/"):
                continue
            if source != expected_source:
                raise MemoryOperationError("memory_context_stale")
            source_id = str(receipt.metadata.get("sourceId") or "")
            source_revision = str(
                receipt.metadata.get("sourceRevision") or ""
            )
            if not source_id or not source_revision:
                raise MemoryOperationError("memory_context_stale")
            head = await self._db.fetch_one(
                "SELECT revision, deleted FROM memory_source_heads "
                "WHERE book_id = ? AND source_id = ?",
                [clean_book, source_id],
            )
            if head is not None and (
                bool(head["deleted"])
                or str(head["revision"]) != source_revision
            ):
                raise MemoryOperationError("memory_context_stale")
            memory_receipts.append(receipt)
        if not memory_receipts:
            return
        resource = self._require_resource()
        budget = MemoryBudget(
            key=f"budget:evidence:{clean_book}",
            max_llm_calls=0,
            max_embedding_calls=0,
            max_input_chars=1,
            max_output_tokens=0,
            result_capacity_target_tokens=1,
        )
        providers = resource.providers(
            budget=budget,
            complete=self._unexpected_completion,
        )
        try:
            async with self._resource_memory(
                clean_book,
                providers=providers,
                allow_inference=False,
            ) as memory:
                await memory.validate_evidence(
                    tuple(memory_receipts),
                    signal=signal,
                )
        except (MemoryError, MemoryResourceError) as error:
            raise self._mapped(error) from error

    async def get_many(
        self,
        *,
        book_id: str,
        item_ids: Sequence[str],
        include_inactive: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        if len(item_ids) > 32:
            raise MemoryOperationError("memory_selection_too_large")
        records = []
        for item_id in dict.fromkeys(str(value) for value in item_ids):
            record = await self.get(
                book_id=book_id,
                item_id=item_id,
                include_inactive=include_inactive,
            )
            if record is not None:
                records.append(record)
        return tuple(records)

    async def _source_is_current(
        self,
        book_id: str,
        record: Mapping[str, Any],
    ) -> bool:
        source = record.get("source")
        if not isinstance(source, Mapping):
            return False
        source_id = str(source.get("id") or "")
        revision = str(source.get("revision") or "")
        head = await self._db.fetch_one(
            "SELECT revision, deleted FROM memory_source_heads "
            "WHERE book_id = ? AND source_id = ?",
            [book_id, source_id],
        )
        return head is None or (
            not bool(head["deleted"])
            and str(head["revision"]) == revision
        )

    async def _require_book(self, book_id: str) -> str:
        clean = _required_text(
            book_id,
            code="memory_book_invalid",
            maximum=512,
        )
        row = await self._db.fetch_one("SELECT id FROM books WHERE id = ?", [clean])
        if row is None:
            raise MemoryOperationError("memory_book_not_found")
        return clean

    def _providers(self, key: str, *, inference: bool, book_id: str | None = None):
        resource = self._require_resource()
        budget = MemoryBudget(
            key=f"budget:{key}",
            max_llm_calls=1 if inference else 0,
            max_embedding_calls=16 if inference else 2,
            max_input_chars=40_000 if inference else 20_000,
            max_output_tokens=1_200 if inference else 0,
            result_capacity_target_tokens=1_200 if inference else 1,
        )
        async def complete_for_book(messages, result_capacity_target_tokens, signal):
            return await self._complete(
                messages, result_capacity_target_tokens, signal, book_id=book_id,
            )

        complete = complete_for_book if inference else self._unexpected_completion
        return resource.providers(budget=budget, complete=complete)

    def _resource_memory(self, book_id: str, *, providers, allow_inference: bool):
        return self._require_resource().memory(
            user_id=LOCAL_USER_ID,
            project_id=str(book_id).strip(),
            providers=providers,
            allow_inference=allow_inference,
        )

    def _require_resource(self):
        if self._resource is None:
            raise MemoryOperationError("memory_component_unavailable")
        return self._resource

    async def _complete(
        self,
        messages,
        result_capacity_target_tokens,
        signal,
        *,
        book_id: str | None = None,
    ) -> ModelCompletion:
        config = await self._strict_memory_model(book_id=book_id)
        service = ModelRequestService()
        return await service.complete(
            api_key=str(config["apiKey"]), runtime=service.runtime_from_settings(config),
            context=BackgroundModelContext("memory_component", result_capacity_target_tokens),
            messages=messages, signal=signal, db=self._db,
        )

    async def _strict_memory_model(self, *, book_id: str | None = None) -> dict[str, Any]:
        selected_id = str(
            await get_setting_value(self._db, MEMORY_MODEL_ID_KEY) or ""
        ).strip()
        if not selected_id and book_id:
            selected_id = str(await get_setting_value(
                self._db, f"writing_current_model:{book_id}",
            ) or "").strip()
        configs = await get_setting_value(self._db, "ai_model_configs")
        selected = next(
            (
                dict(item)
                for item in configs
                if isinstance(item, Mapping)
                and str(item.get("id") or "") == selected_id
            ),
            None,
        ) if selected_id and isinstance(configs, list) else None
        if (
            selected is None
            or not str(selected.get("apiKey") or "").strip()
            or not str(selected.get("name") or "").strip()
        ):
            raise MemoryOperationError("memory_model_unconfigured")
        return selected

    @staticmethod
    async def _unexpected_completion(*_args) -> ModelCompletion:
        raise MemoryOperationError("memory_inference_not_authorized")

    @staticmethod
    async def _receipt_record(memory, receipt) -> dict[str, Any]:
        if len(receipt.ids) != 1:
            raise MemoryOperationError("memory_receipt_invalid")
        record = await memory.get(receipt.ids[0], include_inactive=True)
        if record is None:
            raise MemoryOperationError("memory_receipt_invalid")
        return memory_record_dict(record)

    @staticmethod
    def _key(prefix: str, value: str) -> str:
        return MemoryApplicationService._trusted_key(
            f"{prefix}:{_required_text(value, code='memory_operation_key_invalid', maximum=400)}"
        )

    @staticmethod
    def _trusted_key(value: str) -> str:
        return _required_text(
            value,
            code="memory_operation_key_invalid",
            maximum=512,
        )

    @staticmethod
    def _mapped(error: Exception) -> MemoryOperationError:
        code = getattr(error, "code", None)
        allowed = {
            "memory_access_denied",
            "memory_budget_exhausted",
            "memory_cancelled",
            "memory_component_closed",
            "memory_context_stale",
            "memory_embedding_unconfigured",
            "memory_idempotency_conflict",
            "memory_not_found",
            "memory_operation_unresolved",
            "memory_provider_contract",
            "memory_provider_error",
            "memory_record_changed",
            "memory_source_revoked",
            "memory_review_candidate_state",
            "memory_review_mismatch",
            "memory_invalid_review",
            "memory_timeout",
            "memory_version_conflict",
            "memory_write_busy",
        }
        return MemoryOperationError(
            str(code) if code in allowed else "memory_operation_failed"
        )
