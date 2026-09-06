from copy import deepcopy
from domains.novel_analysis import canonical_digest


def skill(observations):
    return {
        "name": "行动驱动的信息释放", "purpose": "用行动后果控制信息释放。",
        "applicability": ["存在明确问题与行动阻力的场景"],
        "limitations": ["不能从短片段推断长篇结构，不适用于纯说明文本"],
        "procedure": [{"action": "先明确行动目标，再用一次行动改变信息", "rationale": "让线索影响选择", "check": "删去线索后选择是否改变"}],
        "pitfalls": ["避免只有隐瞒而没有进展"], "checklist": ["行动是否带来可观察的信息变化"],
        "tags": ["悬念", "信息释放"], "evidenceIds": [x["contentDigest"] for x in observations],
    }


def trials():
    return {"trials": [
        {"brief": "修理工排查失灵的电梯", "baseline": "修理工来到电梯旁检查线路。", "application": "修理工切断照明，楼层指示灯却仍在闪烁。他改查备用电源。", "stepApplications": [{"step": 1, "observation": "指示灯改变检查方向"}]},
        {"brief": "厨师在比赛前排查错送的食材", "baseline": "厨师逐一核对食材。", "application": "厨师拆开冷藏箱，里面的标签全是明天的日期。他转身寻找送货单。", "stepApplications": [{"step": 1, "observation": "标签触发新的行动"}]},
    ]}


def assessment(passed=True):
    return {"checks": [{"dimension": key, "passed": passed, "reason": "测试夹具：步骤及边界已检查"} for key in ["evidence", "abstraction", "execution", "transfer", "boundaries"]]}


def report(observations, passed=True):
    value = skill(observations)
    return {"writingSkill": value, "revisionNotes": ["明确了失败情境"], "initialTrials": trials(), "transferTrials": trials(), "testedSkillDigest": canonical_digest(value), "assessment": assessment(passed)}


def stage_result(payload):
    stage = payload.get("stage")
    if stage == "distill_skill":
        return {"writingSkill": skill(payload["observations"]), "revisionNotes": []}
    if stage == "revise_skill":
        value = deepcopy(payload["draftSkill"])
        value["limitations"].append("试写只验证两个场景")
        return {"writingSkill": value, "revisionNotes": ["补充迁移边界"]}
    if stage == "trial_skill":
        return trials()
    if stage == "assess_skill":
        return assessment()
    raise AssertionError(stage)
