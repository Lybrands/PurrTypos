"""Bounded, inert author-file exchange for browser and desktop clients."""

from __future__ import annotations

from infrastructure.persistence.writing.technique_document_parser import file_manifest

import io
import stat
import zipfile

from domains.writing.techniques import TechniqueError, author_path


def preview_technique_upload(raw: bytes, filename: str, limits) -> dict:
    if len(raw) > limits.package_bytes * 2:
        raise TechniqueError("file_exceeds_budget", "上传内容超过限制")
    files = {}
    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > limits.file_count + limits.file_count * limits.directory_depth:
                    raise TechniqueError("file_exceeds_budget", "压缩文件条目过多")
                total = 0
                for info in entries:
                    if info.is_dir():
                        continue
                    path = author_path(info.filename, limits)
                    if path in files or stat.S_ISLNK(info.external_attr >> 16):
                        raise TechniqueError("invalid_reference", "压缩文件包含重复路径或符号链接")
                    total += info.file_size
                    if info.file_size > limits.file_bytes or total > limits.package_bytes or len(files) >= limits.file_count:
                        raise TechniqueError("file_exceeds_budget", "解压后的技法超过限制")
                    files[path] = archive.read(info).decode("utf-8")
        except (zipfile.BadZipFile, UnicodeError, RuntimeError) as exc:
            raise TechniqueError("invalid_entry", "请上传可读取的 UTF-8 技法文件或 ZIP") from exc
        entries = [p for p in files if p.split("/")[-1] == "SKILL.md"]
        if len(entries) != 1:
            raise TechniqueError("invalid_entry", "目录或 ZIP 必须包含唯一的 SKILL.md")
        prefix = entries[0][:-len("SKILL.md")]
        if any(not p.startswith(prefix) for p in files):
            raise TechniqueError("invalid_reference", "压缩文件包含技法目录之外的文件")
        files = {path[len(prefix):]: text for path, text in files.items()}
    elif filename.lower().endswith(".md"):
        try:
            files = {"SKILL.md": raw.decode("utf-8")}
        except UnicodeError as exc:
            raise TechniqueError("invalid_entry", "入口文件必须为 UTF-8 文本") from exc
    else:
        raise TechniqueError("invalid_entry", "请上传 Markdown 入口或 ZIP")
    manifest = file_manifest(files, validate=False, limits=limits)
    return {"files": files, "manifest": manifest, "entryRenamed": not filename.lower().endswith(".zip") and filename != "SKILL.md"}


def export_technique(store, ref: dict) -> bytes:
    with store.barrier():
        files = store._version_files(ref)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content.encode("utf-8"))
    return buffer.getvalue()


async def import_technique(service, *, files: dict, operation_id: str,
                           technique_id: str | None = None, storage_scope="library", owner=None):
    file_manifest(files, validate=False, limits=service.techniques.limits)
    draft = await service.create_draft(operation_id=operation_id, technique_id=technique_id,
                                      storage_scope=storage_scope, owner=owner)
    return await service.apply_changes(draft["techniqueId"], draft["draftId"], expected_revision=0,
        operation_id=operation_id + ":files", changes=[{"action": "put", "path": p, "content": t} for p, t in files.items()])


async def upload_technique(service, *, files: dict, operation_id: str, book_id: str, session_id: str):
    from domains.writing.techniques import canonical_bytes
    session = await service.db.fetch_one("SELECT book_id FROM ai_sessions WHERE id=?", [session_id])
    if not session or str(session["book_id"]) != book_id:
        raise TechniqueError("invalid_reference", "上传技法的会话不属于当前小说")
    file_manifest(files, limits=service.techniques.limits)
    draft = await import_technique(service, files=files, operation_id=operation_id,
        storage_scope="run_input", owner={"bookId": book_id, "sessionId": session_id})
    sealed = await service.seal("technique", draft["techniqueId"], draft["draftId"], expected_revision=draft["draftRevision"],
        expected_tree_digest=draft["treeDigest"], operation_id=operation_id + ":seal")
    await service.db.execute("INSERT OR IGNORE INTO writing_technique_inputs(id,session_id,ref_json) VALUES (?,?,?)",
        [draft["techniqueId"], session_id, canonical_bytes(sealed["sealedRef"]).decode()])
    return {"ref": sealed["sealedRef"], **file_manifest(files)["metadata"]}


SCHEME_BUNDLE_FORMAT = 'purrtypos.writing-scheme/v1'
SCHEME_BUNDLE_BYTES = 16 * 1024 * 1024


def preview_scheme_bundle(raw, limits):
    import json
    from domains.writing.techniques import validate_scheme
    if len(raw) > SCHEME_BUNDLE_BYTES:
        raise TechniqueError('file_exceeds_budget', '方案导入内容超过限制')
    try:
        bundle = json.loads(raw)
        if bundle.get('format') != SCHEME_BUNDLE_FORMAT:
            raise ValueError('unsupported bundle')
        scheme = validate_scheme(bundle['scheme'], limits)
        items = bundle['techniques']
        if not isinstance(items, list) or len(items) != len(scheme['members']):
            raise ValueError('incomplete scheme members')
        expected = {(r['id'], r['versionId']) for r in scheme['members']}
        actual = set()
        for item in items:
            ref = item['ref']
            key = (ref['id'], ref['versionId'])
            if ref.get('kind') != 'technique' or key not in expected or key in actual:
                raise ValueError('unexpected member')
            manifest = file_manifest(item['files'], limits=limits)
            if manifest['versionId'] != ref['versionId']:
                raise ValueError('member version mismatch')
            actual.add(key)
        return {'format': SCHEME_BUNDLE_FORMAT, 'scheme': scheme, 'techniques': items}
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        if isinstance(exc, TechniqueError):
            raise
        raise TechniqueError('invalid_reference', '方案内容、成员文件或版本校验失败') from exc


def export_scheme_bundle(service, ref):
    from domains.writing.techniques import canonical_bytes
    scheme = service.schemes.read_scheme(ref, verify_members=False)
    items = []
    for member in scheme['members']:
        with service.techniques.barrier():
            files = service.techniques._version_files(member)
        items.append({'ref': member, 'files': files})
    raw = canonical_bytes({'format': SCHEME_BUNDLE_FORMAT, 'scheme': scheme, 'techniques': items})
    preview_scheme_bundle(raw, service.techniques.limits)
    return raw


async def import_scheme_bundle(service, *, bundle, operation_id):
    from domains.writing.techniques import canonical_bytes
    bundle = preview_scheme_bundle(canonical_bytes(bundle), service.techniques.limits)
    refs = {}
    for index, item in enumerate(bundle['techniques']):
        draft = await import_technique(service, files=item['files'], operation_id=f'{operation_id}:member:{index}')
        draft = await service.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'],
            expected_tree_digest=draft['treeDigest'], operation_id=f'{operation_id}:seal:{index}')
        refs[item['ref']['id']] = draft['sealedRef']
    content = {**bundle['scheme'], 'members': [refs[ref['id']] for ref in bundle['scheme']['members']]}
    return await service.create_draft(kind='scheme', content=content, operation_id=operation_id)
