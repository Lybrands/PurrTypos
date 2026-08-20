from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_maintenance_repository import (
    SqliteArtifactMaintenanceRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from purra.testing import (
    assert_artifact_store_conforms,
    assert_long_task_repository_conforms,
)


@pytest_asyncio.fixture
async def durable_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sqlite_artifact_ports_conform(durable_db):
    await assert_artifact_store_conforms(
        artifacts=SqliteArtifactRepository(durable_db),
        claims=SqliteArtifactClaimRepository(durable_db),
        maintenance=SqliteArtifactMaintenanceRepository(durable_db),
    )


@pytest.mark.asyncio
async def test_sqlite_long_task_port_conforms(durable_db):
    await assert_long_task_repository_conforms(
        SqliteLongTaskRepository(durable_db)
    )
