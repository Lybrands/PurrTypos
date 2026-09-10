"""Host-bound writing-technique discovery and complete file reads."""

import json
from dataclasses import replace

from purra.contracts import ContextEvidenceReceipt, ToolHandlerResult, ToolSchema
from purra.ports import ToolRegistration

from application.writing_technique_runs import WritingTechniqueRuns
from domains.writing.policies import WRITING_TOOL_POLICIES
from domains.writing.techniques import TechniqueError
from domains.writing.tools.display_names import WRITING_TOOL_DISPLAY_NAMES


def technique_registrations(db, skills):
    runs = WritingTechniqueRuns(db)

    async def search(state, arguments, signal=None):
        from purra.cancellation import raise_if_stopped
        raise_if_stopped(signal)
        try:
            snapshot, _, _ = await runs.validate(state.run_id)
            if snapshot["mode"] != "auto":
                raise TechniqueError("authorization_revoked", "手动模式不允许自动检索写作技法")
            query = str(arguments.get("query", "")).casefold()
            items = []
            for candidate in snapshot["candidates"]:
                try:
                    await runs.access.validate(snapshot, selected=[candidate])
                except TechniqueError:
                    continue
                metadata = candidate["metadata"]
                score = sum(token in (metadata["name"] + metadata["description"]).casefold() for token in query.split())
                items.append((score, {"ref": candidate["ref"], **metadata}))
            items.sort(key=lambda item: -item[0])
            return ToolHandlerResult(json.dumps({"candidates": [item for _, item in items[:8]]}, ensure_ascii=False))
        except TechniqueError as exc:
            return ToolHandlerResult(json.dumps({"error": str(exc)}, ensure_ascii=False), error_code=exc.code)

    async def read(state, arguments, signal=None):
        from purra.cancellation import raise_if_stopped
        raise_if_stopped(signal)
        try:
            selection = dict(arguments["selection"])
            member = dict(arguments.get("technique") or selection)
            if selection["kind"] == "scheme" and not arguments.get("technique"):
                snapshot, _, _ = await runs.validate(state.run_id)
                candidate = next((c for c in snapshot["manual"] + snapshot["candidates"] if c["ref"] == selection), None)
                if candidate is None:
                    raise TechniqueError("authorization_revoked", "方案不属于本轮选择或授权候选")
                await runs.access.validate(snapshot, selected=[candidate])
                return ToolHandlerResult(json.dumps({"selection": selection, "members": candidate["members"], "composition": candidate["composition"],
                    "instruction": "从各成员的 SKILL.md 入口开始读取；辅助文件按入口条件读取。"}, ensure_ascii=False))
            result = await runs.read(state.run_id, selection, member, str(arguments.get("path") or "SKILL.md"), max_characters=24000)
            receipt = ContextEvidenceReceipt(evidence_id=f"technique:{member['id']}:{member['versionId']}:{result['path']}",
                context_block="readWritingTechnique", source="writing_technique/v1", item_id=member["id"],
                metadata={"ref": member, "path": result["path"], "sha256": result["sha256"], "selections": [selection]})
            raise_if_stopped(signal)
            return ToolHandlerResult(json.dumps(result, ensure_ascii=False), context_evidence=(receipt,))
        except TechniqueError as exc:
            return ToolHandlerResult(json.dumps({"error": str(exc)}, ensure_ascii=False), error_code=exc.code)

    return {name: ToolRegistration(schema=ToolSchema(name=name, description=skills[name]["description"],
                parameters=skills[name]["parameters"], display_names=WRITING_TOOL_DISPLAY_NAMES[name]),
                policy=WRITING_TOOL_POLICIES[name], handler=handler)
            for name, handler in (("searchWritingTechniques", search), ("readWritingTechnique", read))}


def guard_technique_registration(registration, db):
    runs = WritingTechniqueRuns(db)

    async def check(state):
        if not state.run_id:
            return
        row = await db.fetch_one("SELECT binding_attributes_json FROM ai_agent_runs WHERE id=?", [state.run_id])
        attrs = json.loads(row["binding_attributes_json"] or "{}") if row else {}
        if attrs.get("writingTechniqueSnapshot"):
            await runs.validate(state.run_id)
        elif attrs.get("writingMethodBindingSnapshot", {}).get("catalog"):
            raise TechniqueError("authorization_revoked", "旧写作方法已退役，请重新选择写作技法后发起请求")

    async def handler(state, arguments, signal=None):
        await check(state)
        return await registration.handler(state, arguments, signal)

    async def call_handler(state, arguments, tool_call, signal=None):
        await check(state)
        return await registration.call_handler(state, arguments, tool_call, signal)

    return replace(registration, handler=handler, call_handler=call_handler if registration.call_handler else None)
