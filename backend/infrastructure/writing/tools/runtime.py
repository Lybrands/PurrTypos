"""Instance-bound runtime dependencies for concrete Writing handlers."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING

from domains.writing.tools.contracts import ToolHandler, ToolResult

if TYPE_CHECKING:
    from database.connection import DatabaseConnection
    from application.memory_operations import MemoryApplicationService
    from domains.writing.tools.source_repository import (
        WritingSourceRepository,
    )


logger = logging.getLogger(__name__)


WritingToolOperation = Callable[
    [
        "WritingToolDependencies",
        dict,
        dict,
        Callable[[dict], None] | None,
    ],
    Awaitable[ToolResult],
]


@dataclass(frozen=True, slots=True)
class WritingToolDependencies:
    """Resources owned by one concrete Writing tool-catalog instance."""

    db: "DatabaseConnection"
    sources: "WritingSourceRepository"
    memories: "MemoryApplicationService"


def bind_writing_tool_handlers(
    operations: Mapping[str, WritingToolOperation],
    dependencies: WritingToolDependencies,
    *,
    atomic_operation_names: frozenset[str] = frozenset(),
) -> Mapping[str, ToolHandler]:
    """Return an immutable handler map bound to one dependency instance."""

    return MappingProxyType({
        name: (
            partial(_run_atomically, operation, dependencies)
            if name in atomic_operation_names
            else partial(operation, dependencies)
        )
        for name, operation in operations.items()
    })


async def _run_atomically(
    operation: WritingToolOperation,
    dependencies: WritingToolDependencies,
    ctx: dict,
    args: dict,
    send_chunk: Callable[[dict], None] | None,
):
    """Make a confirmed Writing mutation return a durable commit receipt.

    Concrete CRUD helpers and the Memory Repository may open nested
    transactions.  The catalog-owned outer transaction is the only durable
    boundary, so cancellation before it rolls back and cancellation during
    COMMIT cannot turn an applied tool call into a false canceled result.
    """

    context_snapshot = deepcopy(ctx)
    buffered_chunks: list[dict] = []

    def _buffer_chunk(chunk: dict) -> None:
        buffered_chunks.append(deepcopy(chunk))

    try:
        async with dependencies.db.transaction(cancellation_linearizable=True):
            result = await operation(
                dependencies,
                ctx,
                args,
                _buffer_chunk if send_chunk is not None else None,
            )
            if _tool_result_has_error(result):
                raise _AtomicOperationFailed(result)
    except _AtomicOperationFailed as failure:
        _restore_context(ctx, context_snapshot)
        return failure.result
    except BaseException:
        _restore_context(ctx, context_snapshot)
        raise
    if send_chunk is not None:
        for chunk in buffered_chunks:
            try:
                send_chunk(chunk)
            except Exception:
                # The transaction already committed. UI progress delivery is
                # best-effort and cannot rewrite a durable tool receipt.
                logger.debug(
                    "committed Writing tool notification was not delivered",
                    exc_info=True,
                )
                break
    return result


class _AtomicOperationFailed(Exception):
    def __init__(self, result: ToolResult):
        super().__init__("Writing tool operation returned an error result")
        self.result = result


def _tool_result_has_error(result: ToolResult) -> bool:
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and bool(str(payload.get("error") or "").strip())
    )


def _restore_context(ctx: dict, snapshot: dict) -> None:
    if ctx == snapshot:
        return
    ctx.clear()
    ctx.update(snapshot)


__all__ = [
    "WritingToolDependencies",
    "WritingToolOperation",
    "bind_writing_tool_handlers",
]
