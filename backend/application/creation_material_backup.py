"""Validate that a backup includes every authoritative material revision."""
import sqlite3
from pathlib import Path
from infrastructure.obsidian.reader import read_stable
from infrastructure.obsidian.material_transaction import revision
from infrastructure.obsidian.material_document import decode


def validate_material_backup(database_path, root):
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='creation_material_books'").fetchone():
            return
        for book in conn.execute('SELECT * FROM creation_material_books'):
            directory = book['directory']
            if not directory or any(c not in '0123456789abcdef' for c in directory):
                raise ValueError('material directory invalid')
            base = (Path(root) / directory / '资料').resolve()
            for mapping in conn.execute('SELECT * FROM creation_material_files WHERE book_id=?', [book['book_id']]):
                raw = read_stable(base, mapping['path'])
                decode(raw, mapping['path'], material_id=mapping['material_id'], kind=mapping['kind'])
                if revision(raw) != mapping['revision']:
                    raise ValueError('material revision changed; refresh materials before backup')
    finally:
        conn.close()
