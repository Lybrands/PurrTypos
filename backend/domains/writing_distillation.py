"""Executable writing skills and auditable transfer checks, separate from source analysis."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from domains.novel_analysis import canonical_digest


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def text(limit=4000):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def items(item, minimum=1, maximum=12):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}


SKILL_SCHEMA = obj({
    "name": text(120),
    "purpose": text(1000),
    "applicability": items(text(800)),
    "limitations": items(text(800)),
    "procedure": items(obj({"action": text(), "rationale": text(), "check": text()})),
    "pitfalls": items(text(1000)),
    "checklist": items(text(1000)),
    "tags": items(text(60), maximum=12),
    "evidenceIds": items(text(100), maximum=32),
})
SKILL_RESULT_SCHEMA = obj({"writingSkill": SKILL_SCHEMA, "revisionNotes": items(text(), minimum=0)})
TRIAL_SCHEMA = obj({"trials": items(obj({
    "brief": text(1500),
    "baseline": text(4000),
    "application": text(4000),
    "stepApplications": items(obj({"step": {"type": "integer", "minimum": 1}, "observation": text(1000)})),
}), minimum=2, maximum=2)})
ASSESSMENT_SCHEMA = obj({"checks": items(obj({
    "dimension": {"type": "string", "enum": ["evidence", "abstraction", "execution", "transfer", "boundaries"]},
    "passed": {"type": "boolean"},
    "reason": text(3000),
}), minimum=5, maximum=5)})
DISTILLATION_STAGES = {
    "distill_skill": SKILL_RESULT_SCHEMA,
    "trial_skill": TRIAL_SCHEMA,
    "revise_skill": SKILL_RESULT_SCHEMA,
    "assess_skill": ASSESSMENT_SCHEMA,
}


def _validate(value, schema, path="result"):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, Mapping) or set(value) != set(schema["properties"]):
            raise ValueError(f"{path}: fields do not match the distillation contract")
        for key, child in schema["properties"].items():
            _validate(value[key], child, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, (list, tuple)) or not schema["minItems"] <= len(value) <= schema["maxItems"]:
            raise ValueError(f"{path}: invalid item count")
        for item in value:
            _validate(item, schema["items"], path)
    elif kind == "string":
        if not isinstance(value, str) or not value.strip() or len(value) > schema.get("maxLength", 100000):
            raise ValueError(f"{path}: nonempty bounded text required")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path}: unsupported value")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path}: boolean required")
    elif kind == "integer":
        if type(value) is not int or value < schema["minimum"]:
            raise ValueError(f"{path}: invalid step number")


def normalize_distillation(stage, value):
    _validate(value, DISTILLATION_STAGES[stage])
    result = deepcopy(dict(value))
    if stage == "assess_skill":
        if {item["dimension"] for item in result["checks"]} != {"evidence", "abstraction", "execution", "transfer", "boundaries"}:
            raise ValueError("assessment must cover each quality dimension exactly once")
    return result


def validate_skill(skill, observations):
    _validate(skill, SKILL_SCHEMA, "writingSkill")
    available = {item["contentDigest"]: item for item in observations}
    if len(set(skill["evidenceIds"])) != len(skill["evidenceIds"]) or not set(skill["evidenceIds"]) <= set(available):
        raise ValueError("skill evidence must reference validated source observations")
    body = render_skill(skill)
    for observation in available.values():
        for evidence in observation.get("evidence", ()):
            excerpt = str(evidence.get("excerpt") or "").strip()
            if len(excerpt) >= 8 and excerpt in body:
                raise ValueError("writing skill contains a source excerpt")
    return deepcopy(dict(skill))


def render_skill(skill):
    def bullets(values):
        return "\n".join(f"- {value}" for value in values)
    steps = "\n\n".join(
        f"### {index}. {step['action']}\n\n{step['rationale']}\n\n检查：{step['check']}"
        for index, step in enumerate(skill["procedure"], 1)
    )
    return (
        f"# {skill['name']}\n\n{skill['purpose']}\n\n"
        f"## 适用情境\n{bullets(skill['applicability'])}\n\n"
        f"## 适用边界\n{bullets(skill['limitations'])}\n\n"
        f"## 执行方法\n\n{steps}\n\n"
        f"## 常见失败\n{bullets(skill['pitfalls'])}\n\n"
        f"## 完成检查\n{bullets(skill['checklist'])}"
    )


def assessment_passed(assessment):
    normalize_distillation("assess_skill", assessment)
    return all(item["passed"] for item in assessment["checks"])


def validate_report(report, observations):
    """Revalidate at publication; a changed skill cannot reuse an earlier trial receipt."""
    skill = validate_skill(report["writingSkill"], observations)
    for key in ("initialTrials", "transferTrials"):
        normalize_distillation("trial_skill", report[key])
    normalize_distillation("assess_skill", report["assessment"])
    if report["testedSkillDigest"] != canonical_digest(skill):
        raise ValueError("skill changed after its transfer test")
    for trial in report["transferTrials"]["trials"]:
        if {item["step"] for item in trial["stepApplications"]} != set(range(1, len(skill["procedure"]) + 1)):
            raise ValueError("transfer test must exercise every skill step")
    return skill


DISTILL_INSTRUCTION = """从已校验的来源观察蒸馏一个完整、可执行、可迁移的写作方法。不要预设分类或凑齐维度。故事概览由来源分析保存，本单元只交付写作方法及修订说明。
以来源反复支持的机制为主轴，把相互依赖的创作决策组织成连贯 procedure；每步说明 action、rationale、check。
name/purpose/applicability/limitations/pitfalls/checklist/tags 都要具体可用。来源范围不足以支持长篇或普遍结论时写明限制。
证据只通过 evidenceIds 引用 observations 的 contentDigest。正文不得包含来源作品名、人物、地点、专有设定、情节或引文。
不要把阅读效果等同于作者意图，不把单次现象写成普遍规律。revisionNotes 首次可为空。提交 writingSkill 与 revisionNotes。"""
TRIAL_INSTRUCTION = """只依据给定 writingSkill，用两个彼此不同的新场景执行迁移测试。你没有来源正文，不得猜测或模仿原作。
每个 trial 提交同一 brief 下的 baseline（自然的普通写法，不能故意写差）、application（逐步应用方法后的写法）、stepApplications。
brief 的目标、人物和事件条件在两种写法之间保持不变，长度尽量相当；每种写法约 200–400 字。
第一个场景测试主要适用情境，第二个测试适用边界附近的另一情境。逐步记录可观察的应用结果，不预设更好，不输出私有推理。
stepApplications 用从 1 开始的 step 编号覆盖每个步骤；缺乏可执行性也要如实记录。"""
REVISE_INSTRUCTION = """审核 draftSkill 在 initialTrials 中的具体表现，并依据 observations 修订为一个完整写作方法。
检查证据是否支持机制、是否越过原文覆盖范围、步骤是否可执行、试写是否产生预期效果、是否夹带原作内容。
只保留有证据支持且能执行的机制；消除重复、冲突、空泛建议，写清适用边界。revisionNotes 记录可公开的缺陷与修改，不输出私有推理。
正文完全抽象化，证据只用 evidenceIds 引用 observations 的 contentDigest。提交修订后的 writingSkill 和 revisionNotes。"""
ASSESS_INSTRUCTION = """独立评估给定 writingSkill、原文 observations 和最终 transferTrials，不得修改技能或试写，不预设通过。
五个检查维度必须各一次：evidence（原文是否支持机制及范围）、abstraction（无原作实体情节引文）、execution（具体可执行且一致）、transfer（两个新场景的应用是否达到方法所述目标）、boundaries（限制及失效情境是否诚实明确）。
每项 passed 为布尔值，reason 引用具体步骤、试写表现或证据，不使用空泛分数。任何实质缺陷都判失败，保留人工审核的必要性。"""
