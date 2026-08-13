"""Writing product lifecycle that associates request receipts with Core Runs."""

from __future__ import annotations

from purra.contracts import AgentRunResult

from infrastructure.persistence.writing_chat_request_store import (
    SqliteWritingChatRequestStore,
    WritingChatRequestReceipt,
)


class WritingChatRequestStartCanceled(RuntimeError):
    pass


class WritingChatRequestLifecycle:
    def __init__(
        self,
        store: SqliteWritingChatRequestStore,
        request_id: str,
    ) -> None:
        self._store = store
        self._request_id = str(request_id)

    async def validate(self) -> None:
        receipt = await self._store.get(self._request_id)
        if receipt is None or receipt.status != "starting":
            raise ValueError("Writing chat request is not startable")

    async def before_submit(self) -> None:
        if await self._store.before_submit(self._request_id):
            return
        await self._store.finish_before_run(
            self._request_id,
            "request_not_startable",
        )
        raise WritingChatRequestStartCanceled(
            "Writing chat request was canceled before Run creation"
        )

    async def on_run_started(self, run_id: str) -> None:
        await self._store.bind_run(self._request_id, run_id)

    async def on_run_finished(self, result: AgentRunResult) -> None:
        del result

    async def on_start_failed(
        self,
        code: str = "request_start_failed",
    ) -> WritingChatRequestReceipt:
        return await self._store.finish_before_run(self._request_id, code)


__all__ = [
    "WritingChatRequestLifecycle",
    "WritingChatRequestStartCanceled",
]
