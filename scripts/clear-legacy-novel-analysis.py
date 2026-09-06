"""Explicit one-off cleanup of v1 analysis; never run automatically at startup."""
import argparse
import os
from pathlib import Path
import sqlite3
from datetime import datetime


def cleanup(path, apply=False):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=5000')
    tables = {r[0]: {x[1] for x in c.execute(f'PRAGMA table_info("{r[0]}")')} for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    analysis = [r[0] for r in c.execute('SELECT id FROM novel_source_analyses WHERE schema_version < 2')]
    tasks = [r[0] for r in c.execute("SELECT id FROM ai_agent_long_tasks WHERE namespace='purrtypos.novel_analysis' AND COALESCE(json_extract(metadata_json,'$.analysisSchemaVersion'),1)<2")]
    marks = lambda values: ','.join('?' for _ in values)
    related = [r[0] for r in c.execute(f'SELECT run_id FROM ai_agent_long_task_runs WHERE task_id IN ({marks(tasks)})', tasks)]
    runs = set(related) | {r[0] for r in c.execute("SELECT id FROM ai_agent_runs WHERE binding_namespace IN ('novel_source_analysis','novel_source_analysis.unit') AND COALESCE(json_extract(binding_attributes_json,'$.novelAnalysisBinding.analysisSchemaVersion'),1)<2")}
    # Unit runs are formally linked to their long task. Include their descendants.
    while True:
        children = {r[0] for r in c.execute(f'SELECT id FROM ai_agent_runs WHERE parent_run_id IN ({marks(runs)})', list(runs))}
        if children <= runs:
            break
        runs |= children
    runs = sorted(runs)
    artifacts = [r[0] for r in c.execute(f"SELECT id FROM ai_agent_artifacts WHERE namespace='purrtypos.novel_analysis' AND created_by_run_id IN ({marks(runs)})", runs)]
    methods = [r[0] for r in c.execute(f"SELECT id FROM writing_methods WHERE source_type='analysis_candidate' AND json_extract(source_ref_json,'$.analysisId') IN ({marks(analysis)})", analysis)]
    schemes = [r[0] for r in c.execute(f"SELECT id FROM writing_schemes WHERE source_type='analysis_candidate' AND json_extract(source_ref_json,'$.analysisId') IN ({marks(analysis)})", analysis)]
    print({'analyses':len(analysis),'runs':len(runs),'tasks':len(tasks),'artifacts':len(artifacts),'methods':len(methods),'schemes':len(schemes)})
    if c.execute(f"SELECT 1 FROM ai_agent_runs WHERE id IN ({marks(runs)}) AND status IN ('pending','queued','running','paused') LIMIT 1", runs).fetchone():
        raise RuntimeError('Finish or cancel active source analysis before cleanup')
    if c.execute(f'SELECT 1 FROM continuation_canon_snapshots WHERE source_analysis_id IN ({marks(analysis)}) LIMIT 1', analysis).fetchone():
        raise RuntimeError('Legacy analysis is referenced by a continuation canon snapshot')
    # Never unlink a user book or a published mixed scheme implicitly.
    method_revisions = [r[0] for r in c.execute(f'SELECT id FROM writing_method_revisions WHERE method_id IN ({marks(methods)})', methods)]
    scheme_revisions = [r[0] for r in c.execute(f'SELECT id FROM writing_scheme_revisions WHERE scheme_id IN ({marks(schemes)})', schemes)]
    revisions = method_revisions + scheme_revisions
    if c.execute(f'SELECT 1 FROM book_writing_method_bindings WHERE revision_id IN ({marks(revisions)}) LIMIT 1', revisions).fetchone():
        raise RuntimeError('A book still binds an analysis-derived method')
    if c.execute(f'SELECT 1 FROM writing_scheme_revision_members WHERE method_revision_id IN ({marks(method_revisions)}) AND scheme_revision_id NOT IN ({marks(scheme_revisions)}) LIMIT 1', method_revisions + scheme_revisions).fetchone():
        raise RuntimeError('A separate scheme still references an analysis-derived method')
    if not apply:
        c.close()
        return
    backup = path.with_name(path.name + '.before-distillation-v2-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    with sqlite3.connect(backup) as destination:
        os.chmod(backup, 0o600)
        c.backup(destination)
    print('backup:',backup)
    with c:
        c.execute('BEGIN IMMEDIATE')
        for table, columns in tables.items():
            if table.startswith('ai_agent_'):
                for column, ids in [('run_id', runs),('task_id', tasks),('artifact_id', artifacts)]:
                    if column in columns and ids:
                        c.execute(f'DELETE FROM "{table}" WHERE "{column}" IN ({marks(ids)})',ids)
        for table, ids in [('ai_agent_artifacts',artifacts),('ai_agent_long_tasks',tasks),('ai_agent_runs',runs)]:
            c.execute(f'DELETE FROM {table} WHERE id IN ({marks(ids)})',ids)
        for table in ['novel_source_analysis_evidence','novel_source_analysis_facts','novel_source_craft_cards']:
            c.execute(f'DELETE FROM {table} WHERE analysis_id IN ({marks(analysis)})',analysis)
        for table, column, ids in [('writing_scheme_revision_members','scheme_revision_id',scheme_revisions),('writing_scheme_revisions','scheme_id',schemes),('writing_method_revisions','method_id',methods),('writing_schemes','id',schemes),('writing_methods','id',methods),('novel_source_analyses','id',analysis)]:
            if table in tables:
                c.execute(f'DELETE FROM {table} WHERE {column} IN ({marks(ids)})',ids)
    print('integrity:',c.execute('PRAGMA integrity_check').fetchone()[0])
    c.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    cleanup(args.db,args.apply)
