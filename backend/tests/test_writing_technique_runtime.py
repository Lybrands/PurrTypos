import json
from pathlib import Path

import pytest

from application.writing_technique_access import WritingTechniqueAccess, TechniqueEvidenceValidator
from application.writing_technique_exchange import upload_technique
from application.writing_context_source import RepositoryWritingContextSource
from database.connection import DatabaseConnection
from domains.writing.contracts import WritingDomainContext
from domains.writing.techniques import TechniqueError


ENTRY = "---\nname: 信息控制\ndescription: 用于双方认知不同的对话场景\n---\n先写动作，再给判断。[需要细节时](细节.md)"


@pytest.fixture
async def access(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books(id,title) VALUES ('book','小说')")
    await db.execute("INSERT INTO ai_sessions(id,book_id,scope) VALUES (1,'book','setting')")
    await db.execute("INSERT INTO ai_sessions(id,book_id,scope) VALUES (2,'book','setting')")
    try:
        yield WritingTechniqueAccess(db)
    finally:
        await db.close()


async def test_local_upload_reserved_once_and_restored_without_library_publication(access):
    uploaded = await upload_technique(access.library, files={"SKILL.md": ENTRY, "细节.md": "辅助说明不会自动进入模型上下文。"},
        operation_id="upload", book_id="book", session_id="1")
    assert not await access.library.list_objects("technique")
    reserved = await access.reserve_input(operation_id="send", book_id="book", session_id="1", mode="manual", manual=[uploaded["ref"]])
    repeated = await access.reserve_input(operation_id="send", book_id="book", session_id="1", mode="manual", manual=[uploaded["ref"]])
    assert repeated == reserved
    reopened = WritingTechniqueAccess(access.db)
    snapshot = await reopened.load_input(reserved["inputId"], "book", "1")
    source = RepositoryWritingContextSource(None, None, None, None, technique_access=reopened)
    context = WritingDomainContext(book_id="book", writing_technique_snapshot=snapshot)
    result = await source.build_techniques(context, 4000)
    assert ENTRY in json.loads(result["content"])["entries"][0]["content"]
    assert "辅助说明不会自动进入模型上下文" not in result["content"]
    assert result["receipts"][0].metadata["path"] == "SKILL.md"
    with pytest.raises(TechniqueError, match="上下文"):
        await source.build_techniques(context, 1)
    with pytest.raises(TechniqueError):
        await reopened.load_input(reserved["inputId"], "book", "2")
    with pytest.raises(TechniqueError):
        await reopened.reserve_input(operation_id="other-session", book_id="book", session_id="2", mode="manual", manual=[uploaded["ref"]])
    assert not (await reopened.freeze(book_id="book"))["manual"]


async def test_tool_reads_are_bound_to_persisted_run_and_record_entry_receipts(access):
    from application.writing_technique_runs import WritingTechniqueRuns
    uploaded = await upload_technique(access.library, files={"SKILL.md": ENTRY, "细节.md": "细节正文"},
        operation_id="upload", book_id="book", session_id="1")
    ref = uploaded["ref"]
    snapshot = await access.freeze(book_id="book", manual=[ref], input_session_id="1")
    attrs = {"bookId": "book", "agentProfile": "writing", "writingTechniqueSnapshot": snapshot}
    await access.db.execute("INSERT INTO ai_agent_runs(id,status,binding_attributes_json) VALUES ('run','running',?)", [json.dumps(attrs)])
    runs = WritingTechniqueRuns(access.db)
    with pytest.raises(TechniqueError, match="先读取"):
        await runs.read("run", ref, ref, "细节.md", max_characters=9999)
    await runs.read("run", ref, ref, "SKILL.md", max_characters=9999)
    assert (await runs.read("run", ref, ref, "细节.md", max_characters=9999))["content"] == "细节正文"
    with pytest.raises(TechniqueError):
        await runs.read("another-run", ref, ref, "SKILL.md", max_characters=9999)
    await access.library.set_status("technique", ref["id"], "archived", operation_id="archive")
    with pytest.raises(TechniqueError):
        await TechniqueEvidenceValidator(access, snapshot, None).validate_evidence([])


async def test_usage_requires_actual_invocation_and_preserves_frozen_selection(access):
    from application.writing_technique_runs import WritingTechniqueRuns
    uploaded = await upload_technique(access.library, files={"SKILL.md": ENTRY, "细节.md": "细节正文"},
        operation_id="usage", book_id="book", session_id="1")
    ref = uploaded["ref"]
    snapshot = await access.freeze(book_id="book", manual=[ref], input_session_id="1")
    attrs = {"bookId": "book", "agentProfile": "writing", "writingTechniqueSnapshot": snapshot}
    await access.db.execute("INSERT INTO ai_agent_runs(id,status,binding_attributes_json) VALUES ('usage','running',?)", [json.dumps(attrs)])
    runs = WritingTechniqueRuns(access.db)
    await runs.mark_entries('usage', [ref])
    assert await runs.usage('usage') == []
    receipt = {"source": "writing_technique/v1", "metadata": {"ref": ref, "path": "SKILL.md", "sha256": "entry-hash", "selections": [ref]}}
    for _ in range(2):
        await access.db.execute("INSERT INTO ai_agent_run_events(run_id,event_type,payload_json) VALUES ('usage','stream.opened',?)", [json.dumps({"contextEvidence": [receipt]})])
    await access.db.execute("UPDATE ai_agent_runs SET status='done' WHERE id='usage'")
    await access.library.set_status('technique', ref['id'], 'archived', operation_id='archive-usage')
    assert await runs.usage('usage') == [{"ref": ref, "name": "信息控制", "source": "manual",
        "files": [{"ref": ref, "path": "SKILL.md", "sha256": "entry-hash"}]}]
