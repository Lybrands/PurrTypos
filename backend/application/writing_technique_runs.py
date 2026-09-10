"""Resolve execution authority from persisted host Run bindings."""

import json

from application.writing_technique_access import WritingTechniqueAccess
from domains.writing.techniques import TechniqueError, canonical_bytes


class WritingTechniqueRuns:
    def __init__(self, db):
        self.db = db
        self.access = WritingTechniqueAccess(db)

    async def snapshot(self, run_id):
        run = await self.db.fetch_one("SELECT status,binding_attributes_json FROM ai_agent_runs WHERE id=?", [run_id])
        attrs = json.loads(run["binding_attributes_json"] or "{}") if run else {}
        snapshot = attrs.get("writingTechniqueSnapshot")
        if not run or run["status"] != "running" or attrs.get("agentProfile") != "writing" or not snapshot or snapshot["bookId"] != attrs.get("bookId"):
            raise TechniqueError("authorization_revoked", "当前运行没有写作技法访问权限")
        return snapshot

    async def state(self, run_id):
        row = await self.db.fetch_one("SELECT * FROM writing_technique_run_state WHERE run_id=?", [run_id])
        return {"automaticRefs": json.loads(row["automatic_refs_json"]), "entryRefs": json.loads(row["entry_refs_json"]), "entryBudgetTokens": row["entry_budget_tokens"]} if row else {"automaticRefs": [], "entryRefs": [], "entryBudgetTokens": 0}

    async def usage(self, run_id):
        """Project files delivered to model invocations, excluding prepared entries."""
        run = await self.db.fetch_one("SELECT binding_attributes_json FROM ai_agent_runs WHERE id=?", [run_id])
        attrs = json.loads(run["binding_attributes_json"] or "{}") if run else {}
        snapshot = attrs.get("writingTechniqueSnapshot") or {}
        if attrs.get("agentProfile") != "writing" or not snapshot:
            return []
        manual = snapshot.get("manual", [])
        candidates = [*manual, *snapshot.get("candidates", [])]
        rows = await self.db.fetch_all("SELECT payload_json FROM ai_agent_run_events WHERE run_id=? AND event_type='stream.opened' ORDER BY id", [run_id])
        usage = {}
        for row in rows:
            for receipt in json.loads(row["payload_json"]).get("contextEvidence", []):
                if receipt.get("source") != "writing_technique/v1":
                    continue
                metadata = receipt.get("metadata") or {}
                for ref in metadata.get("selections", []):
                    candidate = next((item for item in candidates if item["ref"] == ref), None)
                    if not candidate or metadata.get("ref") not in candidate["members"]:
                        continue
                    key = canonical_bytes(ref).decode()
                    item = usage.setdefault(key, {"ref": ref, "name": candidate["metadata"]["name"],
                        "source": "manual" if any(item["ref"] == ref for item in manual) else "auto", "files": []})
                    file = {"ref": metadata["ref"], "path": metadata.get("path"), "sha256": metadata.get("sha256")}
                    if file not in item["files"]:
                        item["files"].append(file)
        return list(usage.values())

    async def record_budget(self, run_id, tokens):
        await self.db.execute("INSERT INTO writing_technique_run_state(run_id,entry_budget_tokens) VALUES (?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET entry_budget_tokens=excluded.entry_budget_tokens", [run_id, tokens])

    async def validate(self, run_id):
        snapshot, state = await self.snapshot(run_id), await self.state(run_id)
        resolution = self.access.resolve(snapshot, state["automaticRefs"])
        await self.access.validate(snapshot, selected=resolution["selected"])
        return snapshot, state, resolution

    async def mark_entries(self, run_id, refs):
        async with self.db.transaction():
            snapshot = await self.snapshot(run_id)
            for candidate in snapshot["manual"]:
                if candidate.get("inputSessionId"):
                    await self.db.execute("INSERT OR IGNORE INTO writing_technique_run_inputs(run_id,input_id) VALUES (?,?)", [run_id, candidate["ref"]["id"]])
            state = await self.state(run_id)
            entries = state["entryRefs"] + [ref for ref in refs if ref not in state["entryRefs"]]
            await self.db.execute("INSERT INTO writing_technique_run_state(run_id,entry_refs_json) VALUES (?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET entry_refs_json=excluded.entry_refs_json", [run_id, canonical_bytes(entries).decode()])

    async def read(self, run_id, selection, member, path, *, max_characters):
        async with self.db.transaction():
            snapshot, state, resolution = await self.validate(run_id)
            automatic = state["automaticRefs"]
            if not any(c["ref"] == selection for c in resolution["selected"]):
                automatic = [*automatic, selection]
                resolution = self.access.resolve(snapshot, automatic)
                if not any(c["ref"] == selection for c in resolution["selected"]):
                    raise TechniqueError("invalid_reference", "该自动候选与已选版本冲突，已整体跳过")
            candidate = next(c for c in resolution["selected"] if c["ref"] == selection)
            if member not in candidate["members"]:
                raise TechniqueError("authorization_revoked", "该技法不是已选方案的成员")
            if automatic != state["automaticRefs"]:
                from purra.context_budget import estimate_json_tokens
                required = sum(c["entryBytes"] // 2 + estimate_json_tokens(c["composition"]) + 1024 for c in resolution["selected"])
                if required > state["entryBudgetTokens"]:
                    raise TechniqueError("file_exceeds_budget", "当前上下文无法完整容纳该技法或方案的入口，请减少选择")
            file = await self.access.read(snapshot, member, path, automatic_refs=automatic,
                entry_refs=state["entryRefs"], max_characters=max_characters)
            entries = state["entryRefs"]
            if path == "SKILL.md" and member not in entries:
                entries = [*entries, member]
            await self.db.execute("INSERT INTO writing_technique_run_state(run_id,automatic_refs_json,entry_refs_json) VALUES (?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET automatic_refs_json=excluded.automatic_refs_json,entry_refs_json=excluded.entry_refs_json",
                [run_id, canonical_bytes(automatic).decode(), canonical_bytes(entries).decode()])
            return {**file, "ref": member, "selection": selection, "composition": candidate["composition"]}
