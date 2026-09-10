"""Validate file heads and durable references in an isolated backup snapshot."""

import json
import sqlite3
from pathlib import Path

from domains.writing.techniques import canonical_bytes, digest
from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore
from infrastructure.persistence.writing.scheme_file_store import SchemeFileStore


def validate_technique_backup(database_path: Path, library_root: Path):
    techniques = TechniqueFileStore(library_root)
    schemes = SchemeFileStore(library_root)
    verified = set()

    def verify(ref):
        key = (ref.get('kind'), ref.get('id'), ref.get('versionId'))
        if key in verified:
            return
        verified.add(key)
        if key[0] == 'technique':
            techniques.get_version_manifest(ref, verify_files=True)
        elif key[0] == 'scheme':
            for member in schemes.read_scheme(ref, verify_members=False)['members']:
                verify(member)
        else:
            raise ValueError('invalid technique reference in backup')

    for kind, store in [('technique', techniques), ('scheme', schemes)]:
        for record in store.list_records(include_archived=True, include_candidates=True):
            if record['draftHead'] not in record['draftIds']:
                raise ValueError('missing current technique draft')
            for draft_id in record['draftIds']:
                draft = store.get_draft(record['id'], draft_id)
                if draft.get('sealedRef'):
                    verify(draft['sealedRef'])
                if kind == 'technique':
                    state_path = store._draft_path(record['id'], draft_id)
                    raw = store._json(state_path)
                    for generation in raw['history'].values():
                        base = store._path('techniques', record['id'], 'drafts', draft_id, 'generations', generation)
                        manifest = store._json(base / 'manifest.json')
                        store._read_files(base / 'files', manifest)
                    if raw['manifest']['versionId'] != draft['treeDigest']:
                        raise ValueError('technique draft digest mismatch')
                elif digest(canonical_bytes(draft['content'])) != draft['treeDigest']:
                    raise ValueError('scheme draft digest mismatch')
            for version in set(record.get('publishedVersions', [])) | ({record['publishedHead']} if record.get('publishedHead') else set()):
                verify({'kind': kind, 'id': record['id'], 'versionId': version})

    def walk(value):
        if isinstance(value, dict):
            if value.get('kind') in {'technique', 'scheme'} and 'id' in value and 'versionId' in value:
                verify(value)
            elif all(key in value for key in ('techniqueId', 'draftId', 'versionId')):
                verify({'kind': 'technique', 'id': value['techniqueId'], 'versionId': value['versionId']})
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    connection = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'writing_technique_grants' in tables:
            for kind, id_, version in connection.execute('SELECT kind,object_id,version_id FROM writing_technique_grants'):
                verify({'kind': kind, 'id': id_, 'versionId': version})
        for table, columns in {
            'continuation_operations': ['manifest_json'],
            'writing_technique_selections': ['refs_json'],
            'writing_technique_inputs': ['ref_json'],
            'writing_technique_request_inputs': ['snapshot_json'],
            'writing_technique_run_state': ['automatic_refs_json', 'entry_refs_json'],
            'novel_source_analyses': ['summary_json'],
            'ai_agent_runs': ['binding_attributes_json'],
            'ai_agent_artifact_batches': ['items_json'],
        }.items():
            if table in tables:
                for row in connection.execute(f'SELECT {",".join(columns)} FROM {table}'):
                    for raw in row:
                        walk(json.loads(raw or '{}'))
    finally:
        connection.close()
