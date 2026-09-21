"""Retire exactly the six historical writing-method tables without rewriting history."""

RETIREMENT_ID = "writing-techniques-files-v1"
LEGACY_TABLES = {
    "book_writing_method_bindings": {"binding_type", "method_revision_id", "scheme_revision_id"},
    "writing_scheme_revision_members": {"scheme_revision_id", "method_revision_id"},
    "writing_scheme_revisions": {"scheme_id", "version_no", "members_digest"},
    "writing_schemes": {"draft_members_json", "current_published_revision_id"},
    "writing_method_revisions": {"method_id", "markdown_body", "version_no"},
    "writing_methods": {"method_type", "draft_markdown", "current_published_revision_id"},
}


async def retire_writing_method_schema(db):
    async with db.transaction():
        await db.execute("CREATE TABLE IF NOT EXISTS app_schema_migrations (id TEXT PRIMARY KEY NOT NULL, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        if await db.fetch_one("SELECT id FROM app_schema_migrations WHERE id=?", [RETIREMENT_ID]):
            return
        tables = {row["name"] for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        for name, expected in LEGACY_TABLES.items():
            if name in tables:
                columns = {row["name"] for row in await db.fetch_all(f'PRAGMA table_info("{name}")')}
                if not expected <= columns:
                    raise RuntimeError(f"旧写作技法表结构无法确认，已停止退役：{name}")
        for name in LEGACY_TABLES:
            if name in tables:
                await db.execute(f'DROP TABLE "{name}"')
        await db.execute("INSERT INTO app_schema_migrations(id) VALUES (?)", [RETIREMENT_ID])


async def prepare_import_schema(connection):
    """Prepare the isolated candidate before the live database is replaced."""
    from contextlib import asynccontextmanager
    from database.writing_technique_schema import init_writing_technique_schema

    class CandidateDatabase:
        @asynccontextmanager
        async def transaction(self):
            connection.execute("SAVEPOINT writing_technique_import")
            try:
                yield
                connection.execute("RELEASE writing_technique_import")
            except BaseException:
                connection.execute("ROLLBACK TO writing_technique_import")
                connection.execute("RELEASE writing_technique_import")
                raise

        async def execute(self, sql, parameters=()):
            return connection.execute(sql, parameters)

        async def fetch_all(self, sql, parameters=()):
            cursor = connection.execute(sql, parameters)
            return [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

        async def fetch_one(self, sql, parameters=()):
            rows = await self.fetch_all(sql, parameters)
            return rows[0] if rows else None

    db = CandidateDatabase()
    with connection:
        await retire_writing_method_schema(db)
        await init_writing_technique_schema(db)
        connection.execute("DELETE FROM writing_technique_catalog")
