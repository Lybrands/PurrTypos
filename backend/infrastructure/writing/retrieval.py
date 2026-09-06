"""Live Writing retrieval through PurrA's public contract.

Run IDs locate host authorization; neither model arguments nor mutable
ExecutionState.domain can select a book or enable method recommendations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from purra.cancellation import raise_if_stopped
from purra.ports import CancellationSignal
from purra.retrieval import RetrievalError, RetrievalHit, RetrievalRequest

from infrastructure.persistence.writing.sqlite_writing_method_repository import (
    SqliteWritingMethodRepository,
)

if TYPE_CHECKING:
    from application.memory_operations import MemoryApplicationService
    from database.connection import DatabaseConnection


MEMORY_RETRIEVAL_LIMIT = 12
METHOD_RETRIEVAL_LIMIT = 8
RETRIEVAL_QUERY_CHAR_LIMIT = 4_000


async def _authorized_book(
    db: DatabaseConnection,
    request: RetrievalRequest,
    signal: CancellationSignal | None,
    *,
    limit: int,
    methods: bool = False,
) -> str:
    raise_if_stopped(signal)
    if request.scope:
        raise RetrievalError(
            "Writing retrieval scope is bound by the host Run",
            code="retrieval_access_denied",
        )
    if request.limit > limit or len(request.query) > RETRIEVAL_QUERY_CHAR_LIMIT:
        raise RetrievalError("Retrieval budget exceeded", code="retrieval_access_denied")
    if request.run_id is None:
        raise RetrievalError("Run required", code="retrieval_scope_unavailable")
    row = await db.fetch_one(
        "SELECT binding_namespace, binding_attributes_json, status "
        "FROM ai_agent_runs WHERE id = ?", [request.run_id],
    )
    raise_if_stopped(signal)
    if row is None or row["status"] != "running":
        raise RetrievalError("Active Run required", code="retrieval_scope_unavailable")
    try:
        attributes = json.loads(row.get("binding_attributes_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        attributes = {}
    if not isinstance(attributes, dict):
        attributes = {}
    book_id = attributes.get("bookId")
    if (
        row["binding_namespace"] not in {"writing.chat.request", "writing.context"}
        or attributes.get("agentProfile") != "writing"
        or attributes.get("domainNamespace") != "purrtypos.writing"
        or not isinstance(book_id, str)
        or not book_id.strip()
    ):
        raise RetrievalError("Writing scope required", code="retrieval_scope_unavailable")
    if methods and attributes.get("writingMethodRecommendationRequested") is not True:
        raise RetrievalError(
            "Method recommendations were not requested", code="retrieval_access_denied",
        )
    return book_id


@dataclass(frozen=True, slots=True)
class WritingMemoryRetriever:
    db: DatabaseConnection
    memory: MemoryApplicationService

    async def retrieve(
        self, request: RetrievalRequest, signal: CancellationSignal | None = None,
    ) -> tuple[RetrievalHit, ...]:
        book_id = await _authorized_book(
            self.db, request, signal, limit=MEMORY_RETRIEVAL_LIMIT,
        )
        try:
            hits = await self.memory.retrieve(
                book_id=book_id,
                request=request,
                signal=signal,
            )
        except Exception as error:
            code = getattr(error, "code", "memory_retrieval_failed")
            raise RetrievalError("Memory retrieval failed", code=code) from error
        raise_if_stopped(signal)
        return tuple(hits)


@dataclass(frozen=True, slots=True)
class WritingMethodRetriever:
    db: DatabaseConnection

    async def retrieve(
        self, request: RetrievalRequest, signal: CancellationSignal | None = None,
    ) -> tuple[RetrievalHit, ...]:
        await _authorized_book(
            self.db, request, signal, limit=METHOD_RETRIEVAL_LIMIT, methods=True,
        )
        rows = await SqliteWritingMethodRepository(self.db).search_published_methods(
            request.query, limit=request.limit,
        )
        raise_if_stopped(signal)
        return tuple(RetrievalHit(
            id=row["id"],
            content=f"{row['name']}\n{row.get('description') or ''}",
            source="writing_method_catalog",
            version=row["version_no"],
            metadata={
                "evidenceId": f"writing-method-catalog:{row['id']}",
                "revisionId": row["id"],
                "methodId": row["method_id"],
                "methodType": row["method_type"],
                "tags": row.get("tags") or [],
                "contentDigest": row["content_digest"],
                "bindingChanged": False,
            },
        ) for row in rows)
