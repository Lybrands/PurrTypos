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

from domains.writing_distillation import DISTILLATION_STAGES, normalize_distillation, validate_skill

from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from domains.novel_analysis import (
    NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX,
    NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
    NovelAnalysisDomainContext,
)
from infrastructure.persistence.sqlite_artifact_repository import SqliteArtifactRepository


_STAGE_SUBMIT_TOOLS = {
    "distill_skill": "submitDistilledWritingSkill",
    "trial_skill": "submitWritingSkillTrials",
    "revise_skill": "submitRevisedWritingSkill",
    "assess_skill": "submitWritingSkillAssessment",
}


def analysis_submit_tool(payload):
    return _STAGE_SUBMIT_TOOLS.get(payload.get("stage"), "submitNovelAnalysisResult")


def analysis_unit_input_text(payload):
    value = thaw_json_mapping(payload)
    contract = analysis_submission_contract(value.get("stage"))
    if contract is not None:
        value["submissionContract"] = contract
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def analysis_submission_contract(stage):
    schema = DISTILLATION_STAGES.get(stage)
    if schema is None:
        return None
    required = {}

    def visit(node, path):
        if node.get("type") == "object":
            required[path] = list(node["required"])
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
            "sectionId": {"type": "string", "minLength": 1},
            "segmentId": {"type": "string", "minLength": 1},
            "segmentStartCharacter": {"type": "integer", "minimum": 0},
            "segmentEndCharacter": {"type": "integer", "minimum": 1},
        },
        "required": ["excerpt"],
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
                    "subjectKey": {"type": "string", "minLength": 1},
                    "predicate": {"type": "string", "minLength": 1},
                    "value": {},
                    "lifecycleStatus": {"type": "string", "minLength": 1},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": [
                    "factKind", "subjectKey", "predicate", "value", "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "craftCards": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "cardKind": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "title": {"type": "string", "minLength": 1},
                    "bodyMarkdown": {"type": "string", "minLength": 1},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": ["cardKind", "title", "bodyMarkdown", "evidence"],
                "additionalProperties": False,
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
        return {"distill_skill": "来源机制蒸馏", "trial_skill": "新场景迁移试写", "revise_skill": "方法复核与修订", "assess_skill": "适用边界与迁移评估"}.get(payload["stage"], "写作方法")
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

    async def scope(state, arguments, signal):
        if state.domain.get("interactionKind") != "unit" or not state.run_id:
            return "novel_analysis_unit_scope_required"
        return None

    async def submit_result(state, arguments, signal):
        from application.novel_analysis_executor import _normalize_candidates, _restore_evidence_scopes

        if not state.domain.get("analysisInputProvided"):
            return ToolHandlerResult(
                content="The host must provide the bound unit input before submission.",
                error_code="novel_analysis_input_not_provided",
            )
        value = thaw_json_mapping(arguments)["result"]
        try:
            stage = thaw_json_mapping(state.domain["unitInput"]).get("stage")
            if stage in DISTILLATION_STAGES:
                normalized = normalize_distillation(stage, value)
                payload = thaw_json_mapping(state.domain["unitInput"])
                if "writingSkill" in normalized:
                    validate_skill(normalized["writingSkill"], payload["observations"])
                if stage == "trial_skill":
                    expected = set(range(1, len(payload["writingSkill"]["procedure"]) + 1))
                    if any({item["step"] for item in trial["stepApplications"]} != expected for trial in normalized["trials"]):
                        raise ValueError("transfer test must exercise every step")
            else:
                if state.domain["unitInput"].get("includeStoryOverview") and not value.get("storyOverview"):
                    raise ValueError("this analysis unit requires a story overview")
                if len(value.get("facts") or ()) > 48 or len(value.get("craftCards") or ()) > 6:
                    raise ValueError("analysis result exceeds the per-unit item limit")
                bound = state.domain["unitInput"].get("sourceBinding") or {}
                normalized = _normalize_candidates(
                    value, default_section_id=bound.get("sectionId"),
                )
                unit_input = thaw_json_mapping(state.domain["unitInput"])
                dependencies = unit_input.get("sectionCandidates")
                if "normalizedCandidates" in unit_input:
                    dependencies = [unit_input["normalizedCandidates"]]
                if dependencies is not None:
                    normalized = _restore_evidence_scopes(normalized, dependencies, required=True)
        except (TypeError, ValueError) as error:
            return ToolHandlerResult(
                content=str(error), error_code="novel_analysis_structured_output_invalid",
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
    base_submit = registrations[0]
    registrations = (*registrations, *(replace(base_submit, schema=replace(
        base_submit.schema, name=name,
        parameters={"type": "object", "properties": {"result": DISTILLATION_STAGES[stage]},
                    "required": ["result"], "additionalProperties": False},
    )) for stage, name in _STAGE_SUBMIT_TOOLS.items()))

    def enabled(request):
        context = NovelAnalysisDomainContext.from_core_context(request.domain_context)
        if context.interaction_kind != "unit":
            return frozenset()
        return frozenset({analysis_submit_tool(context.unit_input)})

    return InMemoryToolCatalog(registrations, enabled)
