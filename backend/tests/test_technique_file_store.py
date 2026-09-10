from concurrent.futures import ThreadPoolExecutor

from infrastructure.persistence.writing.technique_document_parser import file_manifest

import pytest

from domains.writing.techniques import TechniqueError, TechniqueLimits, validate_scheme
from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore


ENTRY = "---\nname: 危险临近\ndescription: 危险逼近时，通过即时动作收窄叙述。\n---\n先写可观察的动作。\n"


def edited(store, files=None, operation="create", scope="library"):
    state = store.create_draft(operation_id=operation, storage_scope=scope)
    return store.apply_changes(state["techniqueId"], state["draftId"], expected_revision=0,
                               operation_id="write", changes=[{"action": "put", "path": p, "content": t}
                                                               for p, t in (files or {"SKILL.md": ENTRY}).items()])


def seal(store, state, operation="seal"):
    return store.seal_draft(state["techniqueId"], state["draftId"], expected_revision=state["draftRevision"],
                            expected_tree_digest=state["treeDigest"], operation_id=operation)


def test_entry_single_file_and_optional_unreferenced_author_files():
    manifest = file_manifest({"SKILL.md": ENTRY, "备用/笔记.txt": "未引用的笔记允许保留"})
    assert manifest["metadata"]["name"] == "危险临近"
    assert len(manifest["files"]) == 2


@pytest.mark.parametrize("path", ["../a.md", "/a.md", "a\\b.md", "a//b.md", "C:/a.md", "con.md", "a.md/../b.md", "a.exe", "a./b.md"])
def test_reject_unsafe_author_paths(path):
    with pytest.raises(TechniqueError):
        file_manifest({"SKILL.md": ENTRY, path: "text"})


@pytest.mark.parametrize("tail", ["[bad](../outside.md)", "[bad](/tmp/a.md)", "[bad](missing.md)", "[bad][ref]\n\n[ref]: missing.md"])
def test_validate_actual_markdown_references(tail):
    with pytest.raises(TechniqueError, match="引用"):
        file_manifest({"SKILL.md": ENTRY + tail})


def test_commonmark_references_code_external_and_cycles():
    file_manifest({"SKILL.md": ENTRY + "[细节](docs/a.md#动作)\n`[示例](missing.md)`\n[网页](https://example.org)",
                   "docs/a.md": "[入口](../SKILL.md)"})


@pytest.mark.parametrize("entry", ["# no metadata", ENTRY.replace("name:", "name: first\nname:"),
                                   ENTRY.replace("name: 危险临近", "name: 123"),
                                   ENTRY.replace("name: 危险临近", "name: !!python/object:bad {}"),
                                   ENTRY.replace("name: 危险临近", "name: 危险临近\ntags: nope")])
def test_invalid_frontmatter(entry):
    with pytest.raises(TechniqueError):
        file_manifest({"SKILL.md": entry})


def test_portable_collisions_and_resource_limits():
    for files in ({"A.md": "", "a.md": ""}, {"é.md": "", "e\u0301.md": ""}, {"a.md": "", "a.md/b.txt": ""}):
        with pytest.raises(TechniqueError):
            file_manifest(files, validate=False)
    with pytest.raises(TechniqueError) as error:
        file_manifest({"SKILL.md": ENTRY}, limits=TechniqueLimits(file_bytes=1))
    assert error.value.code == "file_exceeds_budget"


def test_reopen_retry_immutable_version_and_atomic_rename(tmp_path):
    store = TechniqueFileStore(tmp_path)
    state = edited(store, {"SKILL.md": ENTRY + "[细节](detail.md)", "detail.md": "细节"})
    original = seal(store, state)
    ref = original["sealedRef"]
    store = TechniqueFileStore(tmp_path)
    assert seal(store, state) == original
    published = store.publish(state["techniqueId"], ref, expected_published_head=None, operation_id="publish")
    assert store.publish(state["techniqueId"], ref, expected_published_head=None, operation_id="publish") == published
    draft = store.create_draft(operation_id="edit-again", technique_id=state["techniqueId"], from_version=ref)
    changes = [{"action": "move", "path": "detail.md", "target": "notes/detail.md"},
               {"action": "put", "path": "SKILL.md", "content": ENTRY + "[细节](notes/detail.md)"}]
    updated = store.apply_changes(state["techniqueId"], draft["draftId"], expected_revision=0, operation_id="rename", changes=changes)
    assert store.apply_changes(state["techniqueId"], draft["draftId"], expected_revision=0, operation_id="rename", changes=changes) == updated
    assert seal(store, updated)["sealedRef"] != ref
    assert store.read_version_file(ref, "detail.md")["content"] == "细节"
    assert store.read_draft_file(state["techniqueId"], draft["draftId"], 0, "detail.md")["content"] == "细节"
    with pytest.raises(TechniqueError):
        store.read_draft_file(state["techniqueId"], draft["draftId"], 999, "detail.md")


def test_idempotency_conflicts_and_parallel_editor_fencing(tmp_path):
    store = TechniqueFileStore(tmp_path)
    state = edited(store)
    assert store.create_draft(operation_id="create")["draftRevision"] == 0
    with pytest.raises(TechniqueError) as error:
        store.create_draft(operation_id="create", owner={"taskId": "other"})
    assert error.value.code == "operation_conflict"

    def update(index):
        try:
            return TechniqueFileStore(tmp_path).apply_changes(state["techniqueId"], state["draftId"], expected_revision=1,
                operation_id=f"edit-{index}", changes=[{"action": "put", "path": "SKILL.md", "content": ENTRY + str(index)}])
        except TechniqueError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, range(2)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count("draft_conflict") == 1


def test_interrupted_head_commit_recovers_without_partial_files(tmp_path, monkeypatch):
    store = TechniqueFileStore(tmp_path)
    state = edited(store)
    write = store._write_json

    def fail_head(path, value):
        if path.name == "state.json":
            raise OSError("simulated power loss before atomic head replacement")
        return write(path, value)

    monkeypatch.setattr(store, "_write_json", fail_head)
    with pytest.raises(OSError):
        seal(store, state)
    reopened = TechniqueFileStore(tmp_path)
    assert reopened.get_draft(state["techniqueId"], state["draftId"])["state"] == "editing"
    result = seal(reopened, state)
    assert reopened.read_version_file(result["sealedRef"], "SKILL.md")["content"] == ENTRY
    assert len(list((tmp_path / "techniques" / state["techniqueId"] / "versions").iterdir())) == 1


def test_cancel_wins_and_sealed_snapshot_stays_unpublished(tmp_path):
    store = TechniqueFileStore(tmp_path)
    state = edited(store)
    store.cancel_draft(state["techniqueId"], state["draftId"], expected_revision=1, operation_id="cancel")
    with pytest.raises(TechniqueError) as error:
        seal(store, state)
    assert error.value.code == "draft_cancelled"
    candidate = edited(store, operation="analysis", scope="analysis_candidate")
    result = seal(store, candidate)
    assert len(store.list_records()) == 1
    with pytest.raises(TechniqueError):
        store.publish(candidate["techniqueId"], result["sealedRef"], expected_published_head=None, operation_id="publish")


def test_symlink_and_tampered_version_rejected(tmp_path):
    store = TechniqueFileStore(tmp_path / "library")
    state = edited(store)
    ref = seal(store, state)["sealedRef"]
    path = store.root / "techniques" / ref["id"] / "versions" / ref["versionId"] / "files" / "SKILL.md"
    path.write_text("tampered")
    with pytest.raises(TechniqueError):
        store.read_version_file(ref, "SKILL.md")
    path.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text(ENTRY)
    path.symlink_to(outside)
    with pytest.raises(TechniqueError, match="符号链接"):
        store.read_version_file(ref, "SKILL.md")


def test_scheme_rejects_nested_duplicate_and_unpinned_members():
    ref = {"kind": "technique", "id": "one", "versionId": "a" * 64}
    scheme = {"schemaVersion": 1, "name": "组合", "description": "用于对话", "composition": "", "members": [ref]}
    assert validate_scheme(scheme) == scheme
    for members in ([], [ref, ref], [dict(ref, kind="scheme")], [dict(ref, versionId="latest")]):
        with pytest.raises(TechniqueError):
            validate_scheme(dict(scheme, members=members))
