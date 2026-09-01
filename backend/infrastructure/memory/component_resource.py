"""Lifespan-owned storage and scoped adapters for ``purra-mem0``."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Sequence

from purra.contracts import AgentMessage, ModelCompletion
from purra_mem0 import (
    Mem0Memory,
    MemoryBudget,
    MemoryProviders,
    MemoryScope,
    create_managed_client,
)


class MemoryResourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MemoryResourceConfiguration:
    data_dir: Path
    embedding_dimensions: int

    def __post_init__(self) -> None:
        if type(self.embedding_dimensions) is not int or not (
            1 <= self.embedding_dimensions <= 65_536
        ):
            raise ValueError("embedding_dimensions must be between 1 and 65536")

    @property
    def root(self) -> Path:
        return self.data_dir.resolve() / "memory-component-v1"


class MemoryComponentResource:
    """Own one Mem0 SDK store and lend immutable authorized scopes."""

    def __init__(
        self,
        configuration: MemoryResourceConfiguration,
        *,
        client_factory: Callable = create_managed_client,
        embedding_gateway=None,
    ) -> None:
        if not isinstance(configuration, MemoryResourceConfiguration):
            raise TypeError("configuration must be MemoryResourceConfiguration")
        self.configuration = configuration
        self._client_factory = client_factory
        self.embedding_gateway = embedding_gateway
        self._client = None
        self._memories: set[Mem0Memory] = set()
        self._storage_lock = asyncio.Lock()
        self._no_borrowers = asyncio.Event()
        self._no_borrowers.set()
        self._restart_required = False
        self._closing = False
        self._closed = False

    @property
    def initialized(self) -> bool:
        return self._client is not None

    def initialize(self) -> None:
        if self._restart_required:
            raise MemoryResourceError("memory_component_restart_required")
        if self._closed or self._closing:
            raise MemoryResourceError("memory_component_closed")
        if self._client is not None:
            return

        root = self.configuration.root
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "manifest.json"
        expected_manifest = {
            "format": 1,
            "embeddingDimensions": self.configuration.embedding_dimensions,
        }
        if manifest_path.exists():
            try:
                actual_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise MemoryResourceError("memory_storage_manifest_invalid") from error
            if actual_manifest != expected_manifest:
                raise MemoryResourceError("memory_embedding_dimensions_changed")

        os.environ["MEM0_TELEMETRY"] = "false"
        os.environ["MEM0_DIR"] = str(root)
        try:
            client = self._client_factory(
                config={
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {
                            "collection_name": "purrtypos_memory_v1",
                            "path": str(root / "vectors"),
                            "embedding_model_dims": (
                                self.configuration.embedding_dimensions
                            ),
                        },
                    },
                    "history_db_path": str(root / "history.sqlite3"),
                },
                embedding_dims=self.configuration.embedding_dimensions,
            )
        except Exception as error:
            raise MemoryResourceError("memory_storage_unavailable") from error
        if not manifest_path.exists():
            pending = root / "manifest.json.pending"
            pending.write_text(
                json.dumps(expected_manifest, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            pending.replace(manifest_path)
        self._client = client

    def providers(
        self,
        *,
        budget: MemoryBudget,
        complete: Callable[
            [Sequence[AgentMessage], int, object],
            Awaitable[ModelCompletion],
        ],
    ) -> MemoryProviders:
        if self._restart_required:
            raise MemoryResourceError("memory_component_restart_required")
        if self._closing or self._closed:
            raise MemoryResourceError("memory_component_closed")
        if self.embedding_gateway is None:
            raise MemoryResourceError("memory_embedding_unconfigured")
        return MemoryProviders(
            budget=budget,
            complete=complete,
            embed=self.embedding_gateway.embed,
        )

    @asynccontextmanager
    async def memory(
        self,
        *,
        user_id: str,
        project_id: str,
        providers: MemoryProviders,
        agent_id: str | None = None,
        allow_inference: bool = False,
    ) -> AsyncIterator[Mem0Memory]:
        async with self._storage_lock:
            if self._closing or self._closed:
                raise MemoryResourceError("memory_component_closed")
            self.initialize()
            memory = Mem0Memory(
                client=self._client,
                scope=MemoryScope(user_id, project_id, agent_id),
                journal_path=str(self.configuration.root / "journal.sqlite3"),
                allow_inference=allow_inference,
                providers=providers,
            )
            self._memories.add(memory)
            self._no_borrowers.clear()
        try:
            yield memory
        finally:
            if memory in self._memories:
                await memory.drain()
                memory.close()
                self._memories.discard(memory)
                if not self._memories:
                    self._no_borrowers.set()

    @asynccontextmanager
    async def storage_maintenance(self) -> AsyncIterator[Path]:
        """Drain and close local stores while a complete snapshot is handled."""

        async with self._storage_lock:
            if self._closing or self._closed:
                raise MemoryResourceError("memory_component_closed")
            await self._no_borrowers.wait()
            self._close_client()
            yield self.configuration.root

    def require_restart(self) -> None:
        """Fence the old lifespan resource after paired storage replacement."""

        self._restart_required = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        try:
            async with self._storage_lock:
                await self._drain_memories()
                self._close_client()
        finally:
            try:
                if self.embedding_gateway is not None:
                    await self.embedding_gateway.close()
            finally:
                self._closed = True

    async def _drain_memories(self) -> None:
        memories = tuple(self._memories)
        if not memories:
            return
        await asyncio.gather(*(memory.drain() for memory in memories))
        for memory in memories:
            memory.close()
            self._memories.discard(memory)
        self._no_borrowers.set()

    def _close_client(self) -> None:
        if self._client is None:
            return
        sdk = self._client.sdk
        sdk.db.close()
        sdk.vector_store.client.close()
        self._client = None
