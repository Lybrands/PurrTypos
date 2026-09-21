"""技能库：内置/已安装技能的发布、安装删除、autoUse 过滤与 Agent 工具门控。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.writing.context_snapshot import _skill_entries
from agents.writing.context_tools import _skill_file, _skills_list
from application.builtin_skills import BUILTIN_PACKAGE_ROOT, ensure_builtin_skills
from application.writing_technique_exchange import import_skill
from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from domains.writing.techniques import TechniqueError


@pytest.fixture
async def db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books(id,title) VALUES ('pilot','测试作品')")
    yield db
    await db.close()


async def _record(db, skill_id: str) -> dict:
    return await WritingTechniqueService(db).get_object("skill", skill_id)


async def _install(db, *, files: dict, skill_id=None) -> dict:
    """镜像路由安装流程：导入 → 封存 → 发布。"""
    service = WritingTechniqueService(db)
    draft = await import_skill(service, files=files, operation_id=f"install:{sorted(files)}:{len(files)}",
                               skill_id=skill_id)
    sealed = await service.seal("skill", draft["techniqueId"], draft["draftId"],
        expected_revision=draft["draftRevision"], expected_tree_digest=draft["treeDigest"],
        operation_id=draft["techniqueId"] + ":seal")
    await service.publish("skill", draft["techniqueId"], ref=sealed["sealedRef"],
        expected_published_head=None, operation_id=draft["techniqueId"] + ":publish")
    return sealed["sealedRef"]


class _State:
    def __init__(self, skills):
        self.run_id = ""
        self.domain = {
            "writingReadScope": {"bookId": "pilot"},
            "writingContextSelection": {},
            "writingContextSnapshot": {"skills": skills},
        }


def _payload(result) -> dict:
    return json.loads(result.content)


@pytest.mark.asyncio
async def test_seed_publishes_skill_and_is_idempotent(db):
    first = await ensure_builtin_skills(db)
    assert first == ["builtin-obsidian-materials", "builtin-writing-skill-creator"]

    record = await _record(db, "builtin-obsidian-materials")
    assert record["kind"] == "skill"
    assert record["origin"] == "builtin"
    assert record["status"] == "active"
    assert record["publishedHead"]

    # skill creator 同为内置技能，但未声明 autoUse —— 仅展示，不进 Agent 快照。
    creator = await _record(db, "builtin-writing-skill-creator")
    assert creator["origin"] == "builtin"
    assert (creator.get("metadata") or {}).get("autoUse") is None

    service = WritingTechniqueService(db)
    entry = await service.read_version_file(
        {"kind": "skill", "id": "builtin-obsidian-materials", "versionId": record["publishedHead"]},
        "SKILL.md",
    )
    assert "materialLink" in entry["content"]
    assert (record.get("metadata") or {}).get("autoUse") is True

    second = await ensure_builtin_skills(db)
    assert second == []
    refreshed = await _record(db, "builtin-obsidian-materials")
    assert refreshed["publishedHead"] == record["publishedHead"]
    assert len(refreshed["draftIds"]) == len(record["draftIds"])


@pytest.mark.asyncio
async def test_builtin_reseed_publishes_new_version(db, tmp_path, monkeypatch):
    await ensure_builtin_skills(db)
    before = await _record(db, "builtin-obsidian-materials")

    upgraded = Path(tmp_path) / "obsidian-materials"
    (upgraded / "references").mkdir(parents=True)
    source = BUILTIN_PACKAGE_ROOT / "obsidian-materials"
    for path in source.rglob("*"):
        if path.is_file():
            target = upgraded / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    entry = upgraded / "SKILL.md"
    entry.write_text(entry.read_text(encoding="utf-8") + "\n<!-- 升级内容 -->\n", encoding="utf-8")
    import application.builtin_skills as builtin_module
    monkeypatch.setattr(builtin_module, "BUILTIN_PACKAGE_ROOT", Path(tmp_path))

    published = await ensure_builtin_skills(db)
    assert published == ["builtin-obsidian-materials"]
    after = await _record(db, "builtin-obsidian-materials")
    assert after["publishedHead"] != before["publishedHead"]
    assert before["publishedHead"] in after["publishedVersions"]


@pytest.mark.asyncio
async def test_builtin_rejects_user_mutation(db):
    await ensure_builtin_skills(db)
    service = WritingTechniqueService(db)
    record = await _record(db, "builtin-obsidian-materials")

    with pytest.raises(TechniqueError):
        await service.delete_object("skill", "builtin-obsidian-materials",
                                    operation_id="op-del", revision_token="0" * 64)
    with pytest.raises(TechniqueError):
        await service.set_status("skill", "builtin-obsidian-materials", "archived", operation_id="op-arc")
    with pytest.raises(TechniqueError):
        await service.create_draft(kind="skill", technique_id="builtin-obsidian-materials", operation_id="op-draft")
    still = await _record(db, "builtin-obsidian-materials")
    assert still["status"] == "active"
    assert still["publishedHead"] == record["publishedHead"]


@pytest.mark.asyncio
async def test_install_and_remove_installed_skill(db):
    files = {
        "SKILL.md": "---\nname: mermaid-charts\ndescription: 时序图速查。\ntags: []\n---\n# Mermaid\n图表示例。",
        "references/types.md": "# 类型\n\n```mermaid\ngraph TD\n  A-->B\n```\n",
    }
    ref = await _install(db, files=files)
    record = await _record(db, ref["id"])
    assert record.get("origin") is None
    assert (record.get("metadata") or {}).get("autoUse") is None

    service = WritingTechniqueService(db)
    listing = await service.list_objects("skill")
    assert any(item["id"] == ref["id"] for item in listing)
    content = await service.read_version_file(ref, "references/types.md")
    assert "mermaid" in content["content"]

    await service.set_status("skill", ref["id"], "archived", operation_id="op-archive")
    preview = await service.deletion_preview("skill", ref["id"])
    assert preview["canDelete"] is True
    await service.delete_object("skill", ref["id"], operation_id="op-delete",
                                revision_token=preview["revisionToken"])
    with pytest.raises(TechniqueError):
        await _record(db, ref["id"])


@pytest.mark.asyncio
async def test_snapshot_skills_only_include_auto_use(db):
    await ensure_builtin_skills(db)
    quiet = {
        "SKILL.md": "---\nname: quiet-skill\ndescription: 未声明自动使用。\n---\n正文。",
    }
    quiet_ref = await _install(db, files=quiet)

    entries = await _skill_entries(db)
    ids = {entry["ref"]["id"] for entry in entries}
    assert "builtin-obsidian-materials" in ids
    assert quiet_ref["id"] not in ids
    # 未声明 autoUse 的内置技能（skill creator）同样不进 Agent 快照。
    assert "builtin-writing-skill-creator" not in ids
    builtin = next(e for e in entries if e["ref"]["id"] == "builtin-obsidian-materials")
    assert builtin["metadata"]["autoUse"] is True
    assert builtin["entryBytes"] > 0


@pytest.mark.asyncio
async def test_skill_tools_entry_first_and_frozen_membership(db):
    await ensure_builtin_skills(db)
    entries = await _skill_entries(db)
    builtin = next(e for e in entries if e["ref"]["id"] == "builtin-obsidian-materials")
    quiet_ref = await _install(db, files={
        "SKILL.md": "---\nname: quiet-skill\ndescription: 未声明自动使用。\n---\n正文。",
    })

    state = _State(entries)
    listing = _payload(await _skills_list(db, state, {}))
    assert listing["items"][0]["ref"] == builtin["ref"]
    assert listing["items"][0]["metadata"]["name"] == "obsidian-materials"

    # 辅助文件必须先读 SKILL.md。
    blocked = _payload(await _skill_file(db, _State(entries), {
        "ref": builtin["ref"], "path": "references/ofm-syntax.md"}))
    assert blocked["success"] is False
    assert "SKILL.md" in blocked["error"]

    entry = _payload(await _skill_file(db, state, {"ref": builtin["ref"], "path": "SKILL.md"}))
    assert entry["file"]["path"] == "SKILL.md"
    followup = _payload(await _skill_file(db, state, {"ref": builtin["ref"], "path": "references/ofm-syntax.md"}))
    assert "Callouts" in followup["file"]["content"]

    # 未声明 autoUse 的技能不在冻结集合，读取被拒。
    quiet = _payload(await _skill_file(db, _State(entries), {"ref": quiet_ref, "path": "SKILL.md"}))
    assert quiet["success"] is False

    # 冻结集合外的版本同样被拒。
    stale = _payload(await _skill_file(db, _State(entries), {
        "ref": {"kind": "skill", "id": builtin["ref"]["id"], "versionId": "0" * 64},
        "path": "SKILL.md"}))
    assert stale["success"] is False

    # 超过 maxTextLength 拒绝并提示实际大小。
    oversized = _payload(await _skill_file(db, _State(entries), {
        "ref": builtin["ref"], "path": "SKILL.md", "maxTextLength": 16}))
    assert oversized["success"] is False
    assert "maxTextLength" in oversized["error"]
