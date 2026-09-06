"""Durable binding authority and immutable source revisions; caches are rebuildable."""
async def init_novel_knowledge_schema(db):
    for statement in (
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_bindings (
            id TEXT PRIMARY KEY, book_id TEXT NOT NULL UNIQUE, root TEXT NOT NULL,
            generation INTEGER NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL,
            scope_json TEXT NOT NULL DEFAULT '{}', semantic_config TEXT,
            scan_json TEXT NOT NULL DEFAULT '{}', scanned_at TEXT, host_id TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_commands (
            id TEXT PRIMARY KEY, book_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result_json TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_documents (
            id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, generation INTEGER NOT NULL,
            path TEXT NOT NULL, source_id TEXT, title TEXT NOT NULL, revision TEXT,
            state TEXT NOT NULL, metadata_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL,
            UNIQUE(binding_id, generation, path))''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_revisions (
            document_id TEXT NOT NULL, revision TEXT NOT NULL, body TEXT NOT NULL,
            metadata_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(document_id, revision))''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_chunks (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, revision TEXT NOT NULL,
            locator_json TEXT NOT NULL, text TEXT NOT NULL)''',
        '''CREATE VIRTUAL TABLE IF NOT EXISTS novel_knowledge_fts USING fts5(
            chunk_id UNINDEXED, tokens, tokenize='unicode61')''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_links (
            document_id TEXT NOT NULL, links_json TEXT NOT NULL, PRIMARY KEY(document_id))''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_entity_bindings (
            document_id TEXT PRIMARY KEY, entity_key TEXT, owner TEXT NOT NULL, state TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS novel_knowledge_index_jobs (
            id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, generation INTEGER NOT NULL,
            config TEXT NOT NULL, chunk_id TEXT NOT NULL, state TEXT NOT NULL,
            calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER, error TEXT)''',
        '''CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document ON novel_knowledge_chunks(document_id, revision)''',
    ):
        await db.execute(statement)
