import importlib.util
from pathlib import Path
import sqlite3
import pytest
from database.connection import DatabaseConnection

@pytest.mark.asyncio
async def test_explicit_cleanup_preserves_sources_and_unrelated_runs(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO novel_source_works(id,title,source_type) VALUES('source','保留原文','file')")
    await db.execute("INSERT INTO novel_source_analyses(id,source_revision_id,version_no,coverage_end_ordinal,schema_version,content_digest) VALUES('old','r',1,0,1,'x'),('new','r',2,0,2,'y')")
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace) VALUES('old-run','done','novel_source_analysis'),('book-run','done','writing')")
    await db.close()
    path = tmp_path / 'purrtypos.db'
    if not path.exists():
        path = next(tmp_path.glob('*.db'))
    module_path = Path(__file__).resolve().parents[2] / 'scripts/clear-legacy-novel-analysis.py'
    spec = importlib.util.spec_from_file_location('cleanup',module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.cleanup(path, False)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT COUNT(*) FROM novel_source_analyses').fetchone()[0] == 2
    module.cleanup(path, True)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT id FROM novel_source_analyses').fetchall() == [('new',)]
        assert c.execute('SELECT id FROM ai_agent_runs').fetchall() == [('book-run',)]
        assert c.execute('SELECT id FROM novel_source_works').fetchall() == [('source',)]
    assert list(tmp_path.glob('*.before-distillation-v2-*'))
