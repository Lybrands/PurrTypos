"""Progressive author-file tools on the existing source-analysis unit Run."""

import asyncio
import json

from purra.cancellation import raise_if_stopped
from purra.contracts import ToolDataContract, ToolEffectState, ToolExecutionMode, ToolHandlerResult, ToolPolicy, ToolRiskLevel, ToolSchema
from purra.json_values import thaw_json_mapping
from purra.ports import ToolRegistration

from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from application.analysis_observation_references import observation_index, resolve_observation_ids, restore_observation_references, project_observations
from application.analysis_evidence_references import evidence_index, model_evidence_index, reference_projection
from application.novel_analysis_source import NovelAnalysisSourceReader
from application.observation_grounding import evidence_context, quoted_text_outside_evidence
from application.technique_source_overlap import source_overlap
from application.writing_technique_service import WritingTechniqueService
from domains.novel_analysis import NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX, NOVEL_ANALYSIS_DOMAIN_NAMESPACE, canonical_digest
from domains.writing.techniques import TechniqueError
from domains.writing_technique_generation import TECHNIQUE_SUBMISSION_SCHEMA, validate_technique_result


def unit_observations(payload):
    groups = [payload] + list(payload.get("sectionCandidates") or [])
    if payload.get("normalizedCandidates"):
        groups.append(payload["normalizedCandidates"])
    result = {}
    for group in groups:
        for item in group.get("observations", group.get("craftCards", [])):
            key = item.get("contentDigest") or canonical_digest(item)
            result.setdefault(key, {**item, "contentDigest": key})
    return list(result.values())


def project_unit_input(payload):
    """Keep observation bodies durable and discoverable without eager injection."""
    result = dict(payload)
    observations = unit_observations(payload)
    result["observationCount"] = len(observations)
    if observations or payload.get("stage") == "distill_skill":
        result.pop("observations", None)
        result.pop("craftCards", None)
        for key in ("sectionCandidates", "normalizedCandidates"):
            if key in result:
                values = result[key] if isinstance(result[key], list) else [result[key]]
                projected = [{k: v for k, v in value.items() if k != "craftCards"} for value in values]
                result[key] = projected if isinstance(result[key], list) else projected[0]
        result["observationAccess"] = "listAnalysisObservations 返回可分页目录；readAnalysisObservations 按 ID 读取完整观察。"
    revision = payload.get("sourceRevisionId") or (payload.get("sourceBinding") or {}).get("sourceRevisionId")
    result = reference_projection(result, revision, payload=payload)
    if evidence_index(payload):
        result["evidenceAccess"] = "evidenceId 是当前冻结输入的短编号，可用 listAnalysisEvidence 查询；需要核查时调用 readAnalysisEvidence，提交时沿用引用，不抄写引文。"
    return result


async def observation_pages(db, run_id, *, field="craftCards"):
    kind = "analysis_fact_page" if field == "facts" else "analysis_observation_page"
    rows = await db.fetch_all("SELECT id FROM ai_agent_artifacts WHERE namespace=? AND kind=? AND owner_id=? ORDER BY id",
        [NOVEL_ANALYSIS_DOMAIN_NAMESPACE, kind, run_id])
    values = []
    for row in rows:
        artifact = await NovelAnalysisArtifactStore(db).require(NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + row["id"])
        values.extend(artifact[field])
    return values


def generation_registrations(db):
    async def bound(state, *, generation=False):
        row = await db.fetch_one("SELECT status,binding_namespace,binding_aggregate_id,binding_attributes_json FROM ai_agent_runs WHERE id=?", [state.run_id])
        if not row or row["status"] != "running" or row["binding_namespace"] != "novel_source_analysis.unit":
            raise TechniqueError("authorization_revoked", "只允许当前来源分析单元操作其绑定材料")
        payload = thaw_json_mapping(state.domain.get("unitInput") or {})
        attrs = json.loads(row["binding_attributes_json"] or "{}")
        if generation and (payload.get("stage") != "distill_skill" or attrs.get("unitId") != "skill:draft"):
            raise TechniqueError("authorization_revoked", "当前单元没有技法草稿写入权限")
        return payload, attrs, row["binding_aggregate_id"]

    async def draft_scope(state):
        payload, attrs, revision_id = await bound(state, generation=True)
        ref = payload.get("techniqueDraft") or {}
        service = WritingTechniqueService(db)
        draft = await asyncio.to_thread(service.techniques.get_draft, ref.get("techniqueId"), ref.get("draftId"))
        if draft["owner"].get("taskId") != attrs.get("taskId") or draft["owner"].get("sourceRevisionId") != revision_id:
            raise TechniqueError("authorization_revoked", "技法草稿不属于当前分析任务")
        return service, draft, payload

    async def get_draft(state, arguments, call, signal):
        _, draft, _ = await draft_scope(state)
        return draft

    async def read_file(state, arguments, call, signal):
        service, draft, _ = await draft_scope(state)
        return await service.read_draft_file(draft["techniqueId"], draft["draftId"], arguments["draftRevision"], arguments["path"])

    async def change_files(state, arguments, call, signal):
        service, draft, payload = await draft_scope(state)
        overlap = source_overlap({change["path"]: change["content"] for change in arguments["changes"]
                                  if change.get("action") == "put"}, unit_observations(payload))
        if overlap:
            raise TechniqueError("tool_input_invalid", "技法正文存在较长连续原文复用，请保留写法并改用原创表达；"
                                 "以下仅为字面匹配位置，不是语义审查结果：" + json.dumps(overlap, ensure_ascii=False))
        return await service.apply_changes(draft["techniqueId"], draft["draftId"], expected_revision=arguments["expectedDraftRevision"],
            operation_id=f"{state.run_id}:{call.id}", changes=arguments["changes"])

    async def list_observations(state, arguments, call, signal):
        payload, _, _ = await bound(state)
        observations = list(observation_index(payload).items())
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 20)
        return {"total": len(observations), "offset": offset, "nextOffset": offset + limit if offset + limit < len(observations) else None,
                "items": [{"id": key, "title": item["title"], "cardKind": item["cardKind"]} for key, item in observations[offset:offset + limit]]}

    async def read_observations(state, arguments, call, signal):
        payload, _, revision_id = await bound(state)
        observations = {item["contentDigest"]: item for item in unit_observations(payload)}
        ids = resolve_observation_ids(arguments["ids"], payload)
        result = {"observations": [observations[key] for key in dict.fromkeys(ids)]}
        if payload.get("stage") != "distill_skill":
            return project_observations(reference_projection(result, revision_id, payload=payload), payload)
        if payload.get("stage") == "distill_skill":
            reader = NovelAnalysisSourceReader(db)
            sections = {}
            grounding = []
            for item in result["observations"]:
                contexts = []
                for evidence in item.get("evidence", []):
                    section_id = evidence["sectionId"]
                    if section_id not in sections:
                        sections[section_id] = await reader.read_section(
                            source_revision_id=revision_id, bound_section_ids=payload["sectionIds"],
                            section_id=section_id)
                    contexts.append(evidence_context(evidence, sections[section_id]["text"]))
                grounding.append({"observationId": item["contentDigest"], "contexts": contexts,
                                  "quotesOutsideEvidence": quoted_text_outside_evidence(item)})
            result["grounding"] = grounding
            result["validationScope"] = "literal_evidence_only"
        if len(json.dumps(result, ensure_ascii=False)) > 48000:
            raise TechniqueError("tool_input_invalid", "观察内容超过单次读取预算，请减少 ID 数量")
        return project_observations(result, payload)

    async def read_source(state, arguments, call, signal):
        payload, _, revision_id = await bound(state, generation=True)
        reader = NovelAnalysisSourceReader(db)
        section = await reader.read_section(source_revision_id=revision_id, bound_section_ids=payload["sectionIds"], section_id=arguments["sectionId"])
        start, end = arguments["startCharacter"], arguments["endCharacter"]
        if not 0 <= start < end or start >= len(section["text"]) or end - start > 24000:
            raise TechniqueError("tool_input_invalid", "来源读取范围无效或超过单次预算")
        end = min(end, len(section["text"]))
        return {"sectionId": section["id"], "sourceRevisionId": revision_id, "title": section["title"],
                "startCharacter": start, "endCharacter": end, "text": section["text"][start:end], "totalCharacters": len(section["text"])}

    async def find_source_evidence(state, arguments, call, signal):
        await bound(state)
        from application.analysis_source_spans import find_spans
        return await find_spans(db, state, arguments.get('query', ''), offset=arguments.get('offset', 0), limit=arguments.get('limit', 8))

    async def append_observations(state, arguments, call, signal):
        return await append_candidates(state, arguments["observations"], call, signal, field="craftCards")

    async def append_facts(state, arguments, call, signal):
        return await append_candidates(state, arguments["facts"], call, signal, field="facts")

    async def append_candidates(state, candidates, call, signal, *, field):
        payload, _, revision_id = await bound(state)
        binding = payload.get("sourceBinding") or {}
        if not binding.get("sectionId"):
            raise TechniqueError("authorization_revoked", "分批提取仅允许当前绑定的来源片段")
        from application.novel_analysis_executor import _normalize_candidates
        result = {"facts": [], "craftCards": []}
        reader = NovelAnalysisSourceReader(db)
        from application.novel_analysis_source import AnalysisEvidenceInputError
        evidence_errors = []
        accepted = []
        rejected = []
        for candidate_index, raw in enumerate(candidates):
            candidate_errors = []
            try:
                from application.novel_analysis_executor import bind_model_candidate_scope
                from application.analysis_candidate_input import clean_candidate
                raw = clean_candidate(raw, field)
                from application.analysis_source_spans import expand_spans
                expanded = await expand_spans(db, state, {"facts": [], "craftCards": [], field: [raw]})
                normalized = _normalize_candidates(bind_model_candidate_scope(
                    expanded, binding),
                    default_section_id=binding["sectionId"])
                card = normalized[field][0]
                from application.analysis_provenance import validate_policy, validate_reference
                validate_policy(card, craft=field == "craftCards")
            except (ValueError, TypeError, AttributeError) as exc:
                errors = [f"{field}[{candidate_index}]: {exc}"]
                evidence_errors.extend(errors)
                rejected.append({"index": candidate_index, "errors": errors})
                continue
            for evidence_index, evidence in enumerate(card["evidence"]):
                try:
                    receipt = await validate_reference(reader, revision_id, [binding["sectionId"]], {**evidence,
                        "segmentStartCharacter": evidence.get("segmentStartCharacter", binding.get("startCharacter")), "segmentEndCharacter": evidence.get("segmentEndCharacter", binding.get("endCharacter"))})
                    evidence.update(receipt)
                except AnalysisEvidenceInputError as exc:
                    candidate_errors.append(f"{field}[{candidate_index}].evidence[{evidence_index}]: {exc}")
            if candidate_errors:
                evidence_errors.extend(candidate_errors)
                rejected.append({"index": candidate_index, "errors": candidate_errors})
            else:
                accepted.append(card)
        if evidence_errors and not accepted:
            raise AnalysisEvidenceInputError(
                "\n".join(evidence_errors) + "。本批尚未保存。按条目错误修正；引文找不到时调用 findAnalysisSourceEvidence，提交返回的 sourceSpanId；类型错误按提示选择 quote 或 chapter。"
            )
        result[field] = accepted
        artifact = await NovelAnalysisArtifactStore(db, join_ambient_transaction=True).write(namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE, kind="analysis_fact_page" if field == "facts" else "analysis_observation_page",
            owner_id=state.run_id, owner_ref_kind="observation_page", owner_ref_id=call.id, run_id=state.run_id,
            semantic_key=call.id, payload=result)
        return {"artifactRef": NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact["artifactId"], "savedCount": len(result[field]),
            "rejected": rejected, "status": "partial" if rejected else "saved",
            "nextAction": "仅修正并重新提交 rejected 中的条目；其余已保存，无需重复提交。" if rejected else "已保存，无需重复提交。"}

    async def submit(state, arguments, call, signal):
        service, draft, payload = await draft_scope(state)
        value = restore_observation_references(arguments["result"], payload)
        candidate = None
        if value["status"] == "generated":
            validate_technique_result({**value, "candidate": {"techniqueId": draft["techniqueId"],
                "draftId": draft["draftId"], "versionId": value["expectedTreeDigest"]}}, unit_observations(payload))
            if draft["state"] == "sealed":
                if draft["draftRevision"] != value["expectedDraftRevision"] or draft["treeDigest"] != value["expectedTreeDigest"]:
                    raise TechniqueError("draft_conflict", "提交的草稿版本与已封存内容不一致")
            else:
                draft = await service.seal("technique", draft["techniqueId"], draft["draftId"], expected_revision=value["expectedDraftRevision"],
                    expected_tree_digest=value["expectedTreeDigest"], operation_id=f"{state.run_id}:{call.id}")
            candidate = {"techniqueId": draft["techniqueId"], "draftId": draft["draftId"], "versionId": draft["sealedRef"]["versionId"]}
        result = validate_technique_result({"status": value["status"], "candidate": candidate,
            "evidenceRefs": value["evidenceRefs"], "scopeNotes": value["scopeNotes"], "reason": value["reason"]}, unit_observations(payload))
        from application.novel_analysis_tools import UNIT_RESULT_KIND
        artifact = await NovelAnalysisArtifactStore(db, join_ambient_transaction=True).write(namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE, kind=UNIT_RESULT_KIND,
            owner_id=state.run_id, owner_ref_kind="analysis_unit_run", owner_ref_id=state.run_id, run_id=state.run_id,
            semantic_key="result", payload={"result": {"techniqueResult": result}})
        return {"artifactRef": NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact["artifactId"]}

    async def list_evidence(state, arguments, call, signal):
        payload, _, _ = await bound(state)
        items = list(model_evidence_index(payload).items())
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 20)
        return {"total": len(items), "nextOffset": offset + limit if offset + limit < len(items) else None,
            "items": [{"evidenceId": key, "sectionId": value["sectionId"], "excerpt": value["excerpt"]}
                for key, value in items[offset:offset + limit]]}

    async def read_evidence(state, arguments, call, signal):
        payload, _, _ = await bound(state)
        index = {**evidence_index(payload), **model_evidence_index(payload)}
        invalid = [key for key in arguments["ids"] if key not in index]
        if invalid:
            raise TechniqueError("tool_input_invalid", "无效证据编号：" + ", ".join(invalid)
                + "。请用 listAnalysisEvidence 查询有效编号后重试，不要猜测编号。")
        return {"evidence": [{"evidenceId": key, **index[key]} for key in arguments["ids"]]}

    def obj(properties, required=None):
        return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}

    string = {"type": "string"}
    revision = {"type": "integer", "minimum": 0}
    from application.novel_analysis_tools import _ANALYSIS_RESULT_SCHEMA
    specs = [
        ("listAnalysisEvidence", "分页查询当前冻结输入的证据短编号及原文。", obj({"offset": revision,
            "limit": {"type": "integer", "minimum": 1, "maximum": 24}}, []), list_evidence, False),
        ("readAnalysisEvidence", "按当前输入的证据 ID 批量读取来源引文。", obj({"ids": {"type": "array", "items": string, "minItems": 1, "maxItems": 24}}), read_evidence, False),
        ("getTechniqueDraft", "查看当前技法草稿目录、状态与代次，不读取全部文件正文。", obj({}), get_draft, False),
        ("readTechniqueDraftFile", "读取本任务技法草稿指定代次的完整文件。", obj({"draftRevision": revision, "path": string}), read_file, False),
        ("applyTechniqueDraftChanges", "按预期代次原子保存一批文件修改；支持多次调用完成技法，重命名时可同时修正引用。", obj({"expectedDraftRevision": revision, "changes": {"type": "array", "minItems": 1, "maxItems": 128,
            "items": obj({"action": {"type": "string", "enum": ["put", "delete", "move"]}, "path": string, "content": string, "target": string}, ["action", "path"])}}), change_files, True),
        ("submitWritingTechnique", "提交完整技法或材料不足结果；宿主校验文件并封存版本，保存来源关联，不自动发布或创建方案。", obj({"result": TECHNIQUE_SUBMISSION_SCHEMA}), submit, True),
        ("listAnalysisObservations", "分页查看本单元全部来源观察目录，未显示条目不等于不存在。", obj({"offset": revision, "limit": {"type": "integer", "minimum": 1, "maximum": 32}}, []), list_observations, False),
        ("readAnalysisObservations", "按目录 ID 读取本单元来源观察及证据。", obj({"ids": {"type": "array", "items": string, "minItems": 1, "maxItems": 24}}), read_observations, False),
        ("readTechniqueSource", "按冻结章节及字符范围补读来源原文，最多读取 24000 字符。", obj({"sectionId": string, "startCharacter": revision, "endCharacter": {"type": "integer", "minimum": 1}}), read_source, False),
        ("findAnalysisSourceEvidence", "查询当前片段原文短编号；省略 query 分页查看目录。只提交 sourceSpanId，无需抄写引文。", obj({
            "query": {"type": "string", "maxLength": 100}, "offset": revision,
            "limit": {"type": "integer", "minimum": 1, "maximum": 8}}, []), find_source_evidence, False),
        ("appendAnalysisFacts", "分批保存当前片段的创作资料，已保存内容无需在最终提交时重复。", obj({"facts": {
            "type": "array", "minItems": 1, "items": _ANALYSIS_RESULT_SCHEMA["properties"]["facts"]["items"]}}), append_facts, True),
        ("appendAnalysisObservations", "分批保存当前片段的观察；最终提交会合并所有已保存批次。", obj({"observations": {
            "type": "array", "minItems": 1, "maxItems": 32, "items": _ANALYSIS_RESULT_SCHEMA["properties"]["craftCards"]["items"]}}), append_observations, True),
    ]
    registrations = []
    for name, description, schema, operation, writes in specs:
        def adapt(operation, writes):
            async def call_handler(state, arguments, tool_call, signal=None):
                raise_if_stopped(signal)
                try:
                    if writes:
                        async with db.transaction(cancellation_linearizable=True):
                            raise_if_stopped(signal)
                            result = await operation(state, thaw_json_mapping(arguments), tool_call, signal)
                    else:
                        result = await operation(state, thaw_json_mapping(arguments), tool_call, signal)
                    return ToolHandlerResult(json.dumps(result, ensure_ascii=False), effect_state=ToolEffectState.COMMITTED if writes else ToolEffectState.NOT_STARTED)
                except (TechniqueError, PermissionError, ValueError) as exc:
                    code = getattr(exc, "code", "invalid_reference" if isinstance(exc, PermissionError) else "tool_execution_failed")
                    if code == "invalid_entry":
                        code = "tool_input_invalid"
                    return ToolHandlerResult(json.dumps({"error": str(exc)}, ensure_ascii=False), error_code=code,
                        **({"effect_state": ToolEffectState.NOT_STARTED} if code == "tool_input_invalid" else {}))
            async def handler(state, arguments, signal=None):
                if writes:
                    return ToolHandlerResult("持久写入需要宿主工具调用身份", error_code="operation_conflict")
                return await call_handler(state, arguments, None, signal)
            return handler, call_handler
        handler, call_handler = adapt(operation, writes)
        titles = {"findAnalysisSourceEvidence": "选择原文依据", "listAnalysisEvidence": "浏览来源依据", "readAnalysisEvidence": "读取来源依据", "getTechniqueDraft": "查看技法草稿", "readTechniqueDraftFile": "读取技法文件", "applyTechniqueDraftChanges": "保存技法文件",
            "submitWritingTechnique": "提交写作技法", "listAnalysisObservations": "浏览来源观察", "readAnalysisObservations": "读取来源观察",
            "readTechniqueSource": "补读来源原文", "appendAnalysisObservations": "保存来源观察", "appendAnalysisFacts": "保存创作资料"}
        def display(state, arguments, tool_call, title=titles[name]):
            path = str(arguments.get("path") or "")
            return {"displayNames": {"zh-CN": title + (f" · {path[:100]}" if path else "")}}
        registrations.append(ToolRegistration(schema=ToolSchema(name=name, description=description, parameters=schema, display_names={"zh-CN": titles[name]}),
            handler=handler, call_handler=call_handler, policy=ToolPolicy(ToolExecutionMode.PROPOSE if writes else ToolExecutionMode.READ, description,
                ToolRiskLevel.WRITE if writes else ToolRiskLevel.READ), host_managed_durability=writes, cancellation_linearizable=writes,
            max_argument_chars=300000, operation_display_params=display, data_contract=ToolDataContract(model_owned_paths=tuple(schema["properties"])) ))
    return tuple(registrations)
