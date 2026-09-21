async def init_creation_material_schema(db):
    await db.execute('''CREATE TABLE IF NOT EXISTS creation_material_books (
        book_id TEXT PRIMARY KEY, directory TEXT NOT NULL, migration_revision TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active')''')
    await db.execute('''CREATE TABLE IF NOT EXISTS creation_material_files (
        material_id TEXT PRIMARY KEY, book_id TEXT NOT NULL, kind TEXT NOT NULL,
        entity_id TEXT NOT NULL, path TEXT NOT NULL, revision TEXT NOT NULL,
        UNIQUE(book_id,kind,entity_id))''')
    await db.execute('''CREATE TABLE IF NOT EXISTS creation_material_commits (
        id TEXT PRIMARY KEY)''')

    columns = await db.fetch_all('PRAGMA table_info(creation_material_books)')
    if not any(c['name'] == 'state' for c in columns):
        await db.execute("ALTER TABLE creation_material_books ADD COLUMN state TEXT NOT NULL DEFAULT 'active'")
    await db.execute('''CREATE TABLE IF NOT EXISTS creation_material_trash (
        material_id TEXT PRIMARY KEY, book_id TEXT NOT NULL, mapping_json TEXT NOT NULL,
        row_json TEXT NOT NULL, operation_id TEXT NOT NULL)''')
