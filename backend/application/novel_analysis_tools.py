"""Present frozen unit input and persist its model-authored analysis candidate."""

from __future__ import annotations

import json
from dataclasses import replace

from purra.artifacts import ArtifactOwnerRef
from purra.contracts import (
    MessageRole, ResponseValidationResult,
    ToolDataContract, ToolEffectState, ToolExecutionMode, ToolHandlerResult,
    ToolPolicy, ToolRiskLevel, ToolSchema,
)
from purra.json_values import thaw_json_mapping
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog

from domains.writing_technique_generation import TECHNIQUE_SUBMISSION_SCHEMA
from application.writing_technique_generation_tools import generation_registrations, project_unit_input, unit_observations

from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from domains.novel_analysis import (
    NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX,
    NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
    NovelAnalysisDomainContext,
)
from infrastructure.persistence.sqlite_artifact_repository import SqliteArtifactRepository


_STAGE_SUBMIT_TOOLS = {"distill_skill": "submitWritingTechnique", "aggregate_story": "submitAnalysisOverview"}


def analysis_submit_tool(payload):
    return _STAGE_SUBMIT_TOOLS.get(payload.get("stage"), "submitNovelAnalysisResult")


def analysis_unit_input_text(payload):
    from application.analysis_source_spans import annotate_source
    value = annotate_source(project_unit_input(thaw_json_mapping(payload)))
    contract = analysis_submission_contract(value.get("stage"))
    if contract is not None:
        value["submissionContract"] = contract
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def analysis_submission_contract(stage):
    schema = {"distill_skill": TECHNIQUE_SUBMISSION_SCHEMA, "aggregate_story": _OVERVIEW_RESULT_SCHEMA}.get(stage)
    if schema is None:
        return None
    required = {}

    def visit(node, path):
        if node.get("type") == "object":
            required[path] = list(node.get("required", []))
            for key, child in node["properties"].items():
                visit(child, f"{path}.{key}")
        elif node.get("type") == "array":
            visit(node["items"], path + "[]")

    visit(schema, "result")
    return {"tool": _STAGE_SUBMIT_TOOLS[stage], "completeReplacement": True, "requiredFields": required}


UNIT_RESULT_KIND = "novel_analysis_model_result"

_EVIDENCE_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "excerpt": {"type": "string", "minLength": 1, "maxLength": 160},
            "referenceKind": {"enum": ["quote", "chapter"], "description": "quote 表示逐字引文；chapter 仅用于不带 excerpt 的章节归纳。"},
            "sourceSpanId": {"type": "string", "minLength": 1, "description": "当前单元目录短编号（如 S001），只传此字段，不重抄引文"},
            "evidenceId": {"type": "string", "minLength": 1},
            "sectionId": {"type": "string", "minLength": 1},
            "segmentId": {"type": "string", "minLength": 1},
            "segmentStartCharacter": {"type": "integer", "minimum": 0},
            "segmentEndCharacter": {"type": "integer", "minimum": 1},
        },
        "anyOf": [{"required": ["excerpt"]}, {"required": ["sourceSpanId"]}, {"required": ["evidenceId"]}, {"required": ["referenceKind", "sectionId"], "properties": {"referenceKind": {"const": "chapter"}}}],
        "additionalProperties": False,
    },
}

_ANALYSIS_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": 48,
            "items": {
                "type": "object",
                "properties": {
                    "factKind": {"type": "string", "minLength": 1},
                    "claimNature": {"enum": ["fact", "summary", "inference"], "description": "内容性质，仅 fact/summary/inference；未决状态 unresolved 属于 lifecycleStatus，不能填在这里。"},
                    "subjectKey": {"type": "string", "minLength": 1},
                    "predicate": {"type": "string", "minLength": 1},
                    "value": {},
                    "lifecycleStatus": {"type": "string", "minLength": 1},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": [
                    "factKind", "subjectKey", "predicate", "value", "evidence",
                ],
                "additionalProperties": True,
            },
        },
        "craftCards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cardKind": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "title": {"type": "string", "minLength": 1},
                    "bodyMarkdown": {"type": "string", "minLength": 1},
                    "mergedObservationIds": {"type": "array", "items": {"type": "string"}},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": ["cardKind", "title", "bodyMarkdown", "evidence"],
                "additionalProperties": True,
            },
        },
        "storyOverview": {
            "type": "object",
            "properties": {
                "summaryMarkdown": {"type": "string", "minLength": 1},
                "evidence": _EVIDENCE_SCHEMA,
            },
            "required": ["summaryMarkdown", "evidence"],
            "additionalProperties": False,
        },
    },
    "required": ["facts", "craftCards"],
    "additionalProperties": False,
}


_OVERVIEW_RESULT_SCHEMA = {
    "type": "object",
    "properties": {"storyOverview": _ANALYSIS_RESULT_SCHEMA["properties"]["storyOverview"]},
    "required": ["storyOverview"], "additionalProperties": False,
}


def _analysis_unit_display_target(state) -> str:
    payload = thaw_json_mapping(state.domain.get("unitInput") or {})
    evidence = payload.get("sourceEvidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    title = " ".join(str(evidence.get("title") or "").split())
    binding = payload.get("sourceBinding")
    binding = binding if isinstance(binding, dict) else {}
    start = binding.get("startCharacter")
    end = binding.get("endCharacter")
    if title and all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (start, end)
    ):
        return f"《{title[:48]}》第 {start}–{end} 字符"
    if title:
        return f"《{title[:48]}》"
    candidates = payload.get("sectionCandidates")
    if isinstance(candidates, (list, tuple)):
        return f"{len(candidates)} 个章节分析结果"
    if payload.get("normalizedCandidates") is not None:
        return "标准化分析结果"
    if payload.get("stage"):
        return {"distill_skill": "写作技法"}.get(payload["stage"], "写作方法")
    return "当前分析单元"


def _analysis_operation_display_params(state, arguments, tool_call):
    del arguments
    target = _analysis_unit_display_target(state)
    return {"displayNames": {
        "zh-CN": f"提交{target}的分析候选",
        "en": "Submit analysis candidate",
    }}


class SubmittedAnalysisResultValidator:
    def validate(self, *, content, messages):
        submitted_calls = {
            call.id for message in messages for call in message.tool_calls
            if call.name in {"submitNovelAnalysisResult", *_STAGE_SUBMIT_TOOLS.values()}
        }
        for message in messages:
            if message.role is not MessageRole.TOOL or message.tool_call_id not in submitted_calls:
                continue
            try:
                receipt = json.loads(message.content or "")
            except (TypeError, ValueError):
                continue
            if isinstance(receipt, dict) and str(receipt.get("artifactRef") or "").startswith(NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX):
                return ResponseValidationResult()
        return ResponseValidationResult(
            violation_code="novel_analysis_result_not_submitted",
            repair_guidance="Use the bound unit input in context and submit the complete result with this stage's submission tool before finishing.",
        )


async def load_unit_model_result(db, run_id):
    artifact = await SqliteArtifactRepository(db).find_for_owner(
        namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE, kind=UNIT_RESULT_KIND,
        owner_id=run_id, owner_ref=ArtifactOwnerRef(kind="analysis_unit_run", id=run_id),
    )
    if artifact is None:
        raise ValueError("novel analysis unit did not submit a result")
    stored = await NovelAnalysisArtifactStore(db).require(
        NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact.id,
    )
    return stored["result"]


def build_novel_analysis_tool_catalog(db):
    artifacts = NovelAnalysisArtifactStore(db)

    async def read_source(state, arguments, signal):
        from application.novel_analysis_source import NovelAnalysisSourceReader
        from purra.cancellation import raise_if_stopped
        raise_if_stopped(signal)
        if state.domain.get("interactionKind") != "follow_up":
            return ToolHandlerResult("当前交互不允许读取来源", error_code="invalid_reference")
        reader = NovelAnalysisSourceReader(db)
        revision = state.domain["sourceRevisionId"]
        bound = state.domain["sectionIds"]
        section_id = arguments.get("sectionId")
        try:
            if section_id:
                row = await reader.read_section(source_revision_id=revision,
                    bound_section_ids=bound, section_id=section_id)
                offset = arguments.get("offset", 0)
                if not isinstance(offset, int) or offset < 0 or offset > len(row["text"]):
                    return ToolHandlerResult("原文位置无效", error_code="tool_input_invalid")
                content = row.pop("text")
                row.update(text=content[offset:offset + 8000], offset=offset,
                    nextOffset=offset + 8000 if offset + 8000 < len(content) else None)
                result = row
            else:
                rows = await reader.list_bound_sections(revision, bound)
                result = [{"sectionId": row["id"], "title": row["title"],
                    "characterCount": len(row["text_content"])} for row in rows]
        except (PermissionError, ValueError, LookupError) as error:
            return ToolHandlerResult(str(error), error_code="invalid_reference")
        raise_if_stopped(signal)
        return ToolHandlerResult(json.dumps(result, ensure_ascii=False))

    async def saved_progress(state, arguments, signal):
        from application.novel_analysis_progress import saved_results, read_saved_result
        from exceptions import AppError
        from purra.cancellation import raise_if_stopped
        raise_if_stopped(signal)
        if state.domain.get("interactionKind") != "follow_up":
            return ToolHandlerResult("仅允许当前对话读取成果", error_code="invalid_reference")
        revision, command = state.domain["sourceRevisionId"], state.domain["commandId"]
        try:
            if "resultNumber" in arguments:
                result = await read_saved_result(db, revision, command, arguments["resultNumber"], arguments.get("offset", 0))
            else:
                rows = await saved_results(db, revision, command)
                offset = arguments.get("offset", 0)
                result = {"items": [{"resultNumber": row["result_number"], "kind": row["kind"]} for row in rows[offset:offset + 20]],
                    "total": len(rows), "nextOffset": offset + 20 if offset + 20 < len(rows) else None}
            raise_if_stopped(signal)
            return ToolHandlerResult(json.dumps(result, ensure_ascii=False))
        except AppError as error:
            return ToolHandlerResult(str(error), error_code="tool_input_invalid")

    async def read_technique(state, arguments, signal):
        from application.writing_technique_service import WritingTechniqueService
        from domains.writing.techniques import TechniqueError
        from purra.cancellation import raise_if_stopped
        from purra.evidence import ContextEvidenceReceipt
        raise_if_stopped(signal)
        if state.domain.get("interactionKind") != "follow_up" or not state.domain.get("analysisArtifactRef"):
            return ToolHandlerResult("当前没有可追问的分析", error_code="invalid_reference")
        artifact = await artifacts.require(state.domain["analysisArtifactRef"])
        if artifact.get("sourceRevisionId") != state.domain.get("sourceRevisionId") or artifact.get("analysisSchemaVersion") != 3:
            return ToolHandlerResult("分析不属于当前来源或格式已退役", error_code="invalid_reference")
        candidate = (artifact.get("techniqueResult") or {}).get("candidate")
        if not candidate:
            return ToolHandlerResult("本次分析没有生成写作技法", error_code="invalid_reference")
        ref = {"kind": "technique", "id": candidate["techniqueId"], "versionId": candidate["versionId"]}
        try:
            file = await WritingTechniqueService(db).read_version_file(ref, arguments.get("path") or "SKILL.md")
            if len(file["content"]) > 24000:
                raise TechniqueError("file_exceeds_budget", "文件超过单次读取预算")
        except TechniqueError as error:
            return ToolHandlerResult(str(error), error_code=error.code)
        raise_if_stopped(signal)
        receipt = ContextEvidenceReceipt(evidence_id=f"analysis-technique:{ref['id']}:{ref['versionId']}:{file['path']}",
            context_block="readAnalysisTechniqueFile", source="writing_technique/v1", item_id=ref["id"],
            metadata={"ref": ref, "path": file["path"], "sha256": file["sha256"], "selections": [ref]})
        return ToolHandlerResult(json.dumps({**file, "ref": ref}, ensure_ascii=False), context_evidence=(receipt,))

    async def scope(state, arguments, signal):
        if state.domain.get("interactionKind") != "unit" or not state.run_id:
            return "novel_analysis_unit_scope_required"
        return None

    async def submit_result(state, arguments, signal):
        from application.novel_analysis_executor import _normalize_candidates, _restore_evidence_scopes, bind_model_candidate_scope, NovelAnalysisTaskUnitExecutor

        if not state.domain.get("analysisInputProvided"):
            return ToolHandlerResult(
                content="The host must provide the bound unit input before submission.",
                error_code="novel_analysis_input_not_provided",
            )
        value = thaw_json_mapping(arguments)["result"]
        try:
            from application.analysis_candidate_input import clean_result
            value = clean_result(value)
            from application.analysis_evidence_references import restore_references
            value = restore_references(value, thaw_json_mapping(state.domain["unitInput"]))
            from application.analysis_observation_references import restore_observation_references
            value = restore_observation_references(value, thaw_json_mapping(state.domain["unitInput"]))
            if state.domain["unitInput"].get("includeStoryOverview") and not value.get("storyOverview"):
                raise ValueError("this analysis unit requires a story overview")
            if len(value.get("facts") or ()) > 48:
                raise ValueError("analysis result exceeds the per-unit fact limit")
            bound = state.domain["unitInput"].get("sourceBinding") or {}
            from application.analysis_source_spans import expand_spans
            value = await expand_spans(db, state, value)
            unit_input = thaw_json_mapping(state.domain["unitInput"])
            dependencies = unit_input.get("sectionCandidates")
            if "normalizedCandidates" in unit_input:
                dependencies = [unit_input["normalizedCandidates"]]
            overview_only = unit_input.get("stage") == "aggregate_story"
            if overview_only:
                if set(value) != {"storyOverview"}:
                    raise ValueError("overview submission accepts only storyOverview")
                value = {**value, "facts": [], "craftCards": []}
            if dependencies is not None and not overview_only:
                from application.novel_analysis_executor import _preserve_observations
                value = _preserve_observations(value, dependencies)
            if bound.get("sectionId"):
                from application.writing_technique_generation_tools import observation_pages
                from application.novel_analysis_executor import _combine_candidates
                pages = await observation_pages(db, state.run_id)
                fact_pages = await observation_pages(db, state.run_id, field="facts")
                combined = _combine_candidates([{"facts": fact_pages, "craftCards": pages}, value])
                value = {**value, "facts": combined["facts"], "craftCards": combined["craftCards"]}
            value = bind_model_candidate_scope(value, bound)
            normalized = _normalize_candidates(value, default_section_id=bound.get("sectionId"))
            if dependencies is not None:
                dependency_evidence = (
                    evidence
                    for dependency in dependencies
                    for candidate in (
                        *(dependency.get("facts") or ()),
                        *(dependency.get("craftCards") or ()),
                        *((dependency.get("storyOverview"),) if isinstance(dependency.get("storyOverview"), dict) else ()),
                    )
                    for evidence in candidate.get("evidence") or ()
                )
                # Full-section inputs have no segment scope to restore.  Requiring
                # one here rejects otherwise valid overview evidence; segmented
                # inputs still retain their strict scope-preservation check.
                requires_scope = any(
                    evidence.get("segmentStartCharacter") is not None
                    and evidence.get("segmentEndCharacter") is not None
                    for evidence in dependency_evidence
                )
                normalized = _restore_evidence_scopes(normalized, dependencies, required=requires_scope)
            normalized = await NovelAnalysisTaskUnitExecutor(db)._validate_candidates(
                normalized, revision_id=state.domain["sourceRevisionId"],
                section_ids=[bound["sectionId"]] if bound.get("sectionId") else state.domain["sectionIds"])

        except (TypeError, ValueError) as error:
            return ToolHandlerResult(
                content=str(error), error_code="tool_input_invalid", effect_state=ToolEffectState.NOT_STARTED,
            )
        artifact = await artifacts.write(
            namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE, kind=UNIT_RESULT_KIND,
            owner_id=state.run_id, owner_ref_kind="analysis_unit_run",
            owner_ref_id=state.run_id, run_id=state.run_id,
            semantic_key="result", payload={"result": normalized},
        )
        return ToolHandlerResult(
            content=json.dumps({"artifactRef": NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact["artifactId"]}),
            effect_state=ToolEffectState.COMMITTED,
        )

    registrations = (
        ToolRegistration(
            schema=ToolSchema(
                name="submitNovelAnalysisResult",
                description="Validate and save the analysis candidate for this unit only. Does not publish or modify any book.",
                parameters={
                    "type": "object",
                    "properties": {"result": _ANALYSIS_RESULT_SCHEMA},
                    "required": ["result"],
                    "additionalProperties": False,
                },
                display_names={"zh-CN": "提交分析候选", "en": "Submit analysis candidate"},
            ),
            handler=submit_result,
            policy=ToolPolicy(ToolExecutionMode.PROPOSE, "提交分析候选", ToolRiskLevel.WRITE),
            scope_validator=scope, host_managed_durability=True,
            cancellation_linearizable=True, max_argument_chars=100_000,
            data_contract=ToolDataContract(model_owned_paths=("result",)),
            operation_display_params=_analysis_operation_display_params,
        ),
    )
    overview = replace(registrations[0], schema=replace(
        registrations[0].schema, name="submitAnalysisOverview",
        description="只提交来源概览；事实和观察由宿主原样保留，无需重新提交或合并。",
        parameters={"type": "object", "properties": {"result": _OVERVIEW_RESULT_SCHEMA},
                    "required": ["result"], "additionalProperties": False},
        display_names={"zh-CN": "提交来源概览", "en": "Submit source overview"},
    ))
    registrations = (*registrations, overview, *generation_registrations(db), ToolRegistration(
        schema=ToolSchema(name="readAnalysisTechniqueFile", description="只读当前分析所生成技法的固定版本。先读取 SKILL.md 入口，再按入口条件读取包内辅助文件。不能修改技法或读取其他技法。",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False},
            display_names={"zh-CN": "读取分析中的写作技法"}),
        handler=read_technique, policy=ToolPolicy(ToolExecutionMode.READ, "读取分析中的写作技法"),
        operation_display_params=lambda state, arguments, call: {"displayNames": {"zh-CN": f"读取写作技法文件 {str(arguments.get('path') or 'SKILL.md')[:96]}"}}))

    registrations = (*registrations, *(
        ToolRegistration(schema=ToolSchema(name=name, description=description,
            parameters=parameters, display_names={"zh-CN": description}),
            handler=read_source, policy=ToolPolicy(ToolExecutionMode.READ, description),
            operation_display_params=lambda state, arguments, call, label=description: {"displayNames": {"zh-CN": label}})
        for name, description, parameters in (
            ("listAnalysisSourceSections", "查看当前来源章节目录", {"type": "object", "properties": {}, "additionalProperties": False}),
            ("readAnalysisSourceSection", "读取当前来源原文片段", {"type": "object", "properties": {
                "sectionId": {"type": "string", "minLength": 1}, "offset": {"type": "integer", "minimum": 0}},
                "required": ["sectionId"], "additionalProperties": False}),
        )
    ))

    registrations = (*registrations, *(
        ToolRegistration(schema=ToolSchema(name=name, description=title, parameters=schema,
            display_names={"zh-CN": title}), handler=saved_progress, policy=ToolPolicy(ToolExecutionMode.READ, title),
            operation_display_params=lambda state, arguments, call, label=title: {"displayNames": {"zh-CN": label}})
        for name, title, schema in (
            ("listAnalysisSavedResults", "查看当前对话已保存的分析成果", {"type": "object", "properties": {
                "offset": {"type": "integer", "minimum": 0}}, "additionalProperties": False}),
            ("readAnalysisSavedResult", "读取已保存的分析成果", {"type": "object", "properties": {
                "resultNumber": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0}},
                "required": ["resultNumber"], "additionalProperties": False}),
        )
    ))

    def enabled(request):
        context = NovelAnalysisDomainContext.from_core_context(request.domain_context)
        if context.interaction_kind == "follow_up":
            return frozenset({"listAnalysisSourceSections", "readAnalysisSourceSection", "listAnalysisSavedResults", "readAnalysisSavedResult"}
                | ({"readAnalysisTechniqueFile"} if context.analysis_artifact_ref else set()))
        if context.interaction_kind != "unit":
            return frozenset()
        if context.unit_input.get("stage") == "distill_skill":
            return frozenset({"getTechniqueDraft", "readTechniqueDraftFile", "applyTechniqueDraftChanges", "submitWritingTechnique",
                              "listAnalysisObservations", "readAnalysisObservations", "readAnalysisEvidence", "listAnalysisEvidence", "readTechniqueSource"})
        names = {analysis_submit_tool(context.unit_input)}
        from application.analysis_evidence_references import evidence_index
        if evidence_index(thaw_json_mapping(context.unit_input)):
            names.update({"readAnalysisEvidence", "listAnalysisEvidence"})
        if unit_observations(thaw_json_mapping(context.unit_input)):
            names.update({"listAnalysisObservations", "readAnalysisObservations", "readAnalysisEvidence", "listAnalysisEvidence"})
        if context.unit_input.get("sourceBinding"):
            names.update({"appendAnalysisObservations", "appendAnalysisFacts", "findAnalysisSourceEvidence"})
        return frozenset(names)

    return InMemoryToolCatalog(registrations, enabled)
