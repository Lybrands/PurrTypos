"""Generation delivery is a sealed file reference or a supported absence."""

from domains.writing.techniques import TechniqueError


TECHNIQUE_SUBMISSION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["generated", "insufficient_material"]},
        "expectedDraftRevision": {"type": "integer", "minimum": 0},
        "expectedTreeDigest": {"type": "string"},
        "evidenceRefs": {"type": "array", "description": "所采用观察的准确 ID：使用 listAnalysisObservations.items[].id，即 readAnalysisObservations.observations[].observationId。不要填写小节 ID、摘录或拼接引用。", "items": {"type": "string"}},
        "scopeNotes": {"type": "array", "description": "简述来源覆盖范围及影响使用的证据局限；无补充说明时填空数组，不重复技法正文或生成过程。", "items": {"type": "string", "maxLength": 3000}},
        "reason": {"type": "string", "description": "generated 时填空字符串；insufficient_material 时说明材料为何不足以支持可用写法。", "maxLength": 3000},
    },
    "required": ["status", "expectedDraftRevision", "expectedTreeDigest", "evidenceRefs", "scopeNotes", "reason"],
    "additionalProperties": False,
}


def validate_technique_result(result, observations):
    if not isinstance(result, dict) or result.get("status") not in {"generated", "insufficient_material"}:
        raise TechniqueError("invalid_entry", "技法结果状态无效")
    refs = result.get("evidenceRefs")
    available = {item["contentDigest"] for item in observations}
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(refs) != len(set(refs)) or not set(refs) <= available:
        raise TechniqueError("invalid_reference", "技法引用了未校验的来源观察")
    if not isinstance(result.get("scopeNotes"), list) or any(not isinstance(note, str) for note in result["scopeNotes"]):
        raise TechniqueError("invalid_entry", "来源范围说明必须是文本列表")
    if result["status"] == "generated":
        unsupported = {item["contentDigest"] for item in observations
            if item.get("supportStatus") == "unsupported" or (item.get("sourceObservation") or {}).get("status") == "unsupported"}
        if set(refs) & unsupported:
            raise TechniqueError("invalid_reference", "已标记为不支持的观察不能作为技法依据")
        candidate = result.get("candidate")
        if not refs or not isinstance(candidate, dict) or not all(candidate.get(key) for key in ("techniqueId", "draftId", "versionId")):
            raise TechniqueError("invalid_entry", "已生成技法需要完整版本引用和来源依据")
    elif result.get("candidate") is not None or not isinstance(result.get("reason"), str) or not result["reason"].strip():
        raise TechniqueError("invalid_entry", "材料不足时不应有候选技法，并需说明原因")
    return result
