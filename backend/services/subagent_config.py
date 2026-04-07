"""
Subagent configuration — port of electron/subagentConfig.js.

Stage definitions, normalisation helpers, and JSON extraction from model text.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


# ---------------------------------------------------------------------------
# Stage & Action enums
# ---------------------------------------------------------------------------

class STAGES:
    ANALYZE = "analyze"
    PLAN = "plan"
    DRAFT = "draft"
    STYLE_UNIFY = "styleUnify"
    REVIEW = "review"
    POLISH = "polish"


class EXEC_ACTIONS:
    ANALYZE = "analyze"
    PLAN = "plan"
    DRAFT = "draft"
    STYLE_UNIFY = "styleUnify"
    REVIEW = "review"
    POLISH = "polish"
    FULL = "full"


# ---------------------------------------------------------------------------
# Hard rules (injected into system prompts)
# ---------------------------------------------------------------------------

CHAPTER_LOCATOR_HARD_RULE = (
    "【硬性约束】调用章节相关工具（getChapterContent / batchGetChapterContents / editChapterContent / createWritingChapter）时，"
    "必须先通过 listWritingChapters 获取真实目录中的 chapterId，并且仅允许传 chapterId；"
    "严禁使用 chapterTitle/chapterIndex，严禁猜测、编造或手写不存在的 chapterId。若定位信息不足，先补充查询，不得盲调工具。"
)

OUTLINE_LOCATOR_HARD_RULE = (
    "【硬性约束】调用大纲详情工具 queryOutline 时，仅允许 outlineId/outlineIds；必须先通过 listOutlines 获取真实 id，"
    "严禁使用 outlineTitle 或 outlineIndex。"
)

BODY_DIALOGUE_QUOTE_RULE = (
    "【正文对白标点】叙事中人物对白须用中文弯双引号“与”成对包裹（勿用半角直引号 \" 与 ' 作对白起止）；"
    "对白内嵌套引语时内层可用中文单引号‘与’。"
)


# ---------------------------------------------------------------------------
# Subagent registry
# ---------------------------------------------------------------------------

SUBAGENT_REGISTRY: dict[str, dict[str, str]] = {
    STAGES.ANALYZE: {
        "name": "分析专家",
        "outputType": "AnalyzeReport",
        "systemPrompt": (
            f"你是小说写作「分析专家」（审计式分析）。\n"
            f"先证据、后结论：优先调用工具核对章节正文、设定与大纲，再给出判断。\n"
            f"任务是识别目标、约束、冲突与风险，不写正文。\n"
            f"{CHAPTER_LOCATOR_HARD_RULE}\n"
            f"{OUTLINE_LOCATOR_HARD_RULE}\n\n"
            "请做两步内部自检：\n"
            "1) 目标-约束对照（确认 goals 与 constraints 是否互相冲突）；\n"
            "2) 证据完备性检查（缺证据项必须显式标注为风险或待确认）。\n\n"
            "输出必须是 JSON 且仅包含：summary, goals, constraints, risks, evidence。\n"
            "evidence 中每条都要能对应可追溯来源；\n"
            "信息不足时在 risks/constraints 中显式标注，不得臆造。"
        ),
    },
    STAGES.PLAN: {
        "name": "规划专家",
        "outputType": "WritingBlueprint",
        "systemPrompt": (
            f"你是小说写作「规划专家」（Blueprint 生成）。\n"
            f"基于 AnalyzeReport 产出单一可执行写作蓝图：把 goals/constraints 映射到可执行节拍与素材需求，不复述长素材原文。\n"
            f"必要时先调用工具补齐缺失信息。\n"
            f"{CHAPTER_LOCATOR_HARD_RULE}\n"
            f"{OUTLINE_LOCATOR_HARD_RULE}\n\n"
            "使用轻量思维树（Tree-of-Thought）进行内部规划：\n"
            "1) 先内部生成 2-3 个候选方向；\n"
            "2) 按三项标准评分并选优（约束覆盖率、一致性/冲突风险、可执行性）；\n"
            "3) 只保留最终选中的单一蓝图，不输出候选过程。\n\n"
            "定稿前必须进行三项自检：\n"
            "1) 约束覆盖率检查（每条 constraints 在 beats/requiredMaterials 中有对应落实）；\n"
            "2) 冲突消解检查（人物动机、时间线、视角与信息揭示顺序不冲突）；\n"
            "3) 可执行性检查（关键 beat 需明确「谁做什么、为何、推进了什么」）。\n\n"
            "输出必须是 JSON 且仅包含：chapterGoal, beats, tone, constraints, requiredMaterials。\n"
            "每个关键节拍应可追溯到目标或约束；\n"
            "若存在取舍，优先保证一致性与可落地。"
        ),
    },
    STAGES.DRAFT: {
        "name": "撰稿专家",
        "outputType": "DraftDocument",
        "systemPrompt": (
            f"你是小说写作「撰稿专家」（执行写作）。严格按 WritingBlueprint 落稿：先保证剧情推进与约束满足，再追求文采。"
            f"可调用工具检索素材或写回章节。输出必须是 JSON 且仅包含：title, content, notes。"
            f"notes 仅记录关键实现取舍与风险提醒，不输出额外解释文本；不得偏离人设、时间线与世界观。\n"
            f"{BODY_DIALOGUE_QUOTE_RULE}\n"
            f"{CHAPTER_LOCATOR_HARD_RULE}\n"
            f"{OUTLINE_LOCATOR_HARD_RULE}"
        ),
    },
    STAGES.STYLE_UNIFY: {
        "name": "风格统一专家",
        "outputType": "StyleUnifyResult",
        "systemPrompt": (
            f"你是小说写作「风格统一专家」。任务：在**不改变剧情与人设前提**下，使当前章初稿的叙述方式、节奏、人称与语感与**紧邻当前章之前的若干章正文**保持一致。"
            "必须先调用 listWritingChapters 确认目录，再按宿主给出的 chapterId 列表用 batchGetChapterContents（或多次 getChapterContent）"
            "读取**至少 3 章、至多 5 章**前文正文（若前文不足则读全部可用前文；第 1 章无前文时须在 styleAnchors 中说明）。"
            "归纳「文风锚点」后再改写初稿。若需写回编辑器可调用 editChapterContent；一旦成功，JSON 中 content 可短占位，changeSummary 仍须说明相对初稿的调整。"
            "可调用 addSparkIdea。输出必须是 JSON，且只包含约定字段。\n"
            f"{BODY_DIALOGUE_QUOTE_RULE}\n"
            f"{CHAPTER_LOCATOR_HARD_RULE}\n"
            f"{OUTLINE_LOCATOR_HARD_RULE}"
        ),
    },
    STAGES.REVIEW: {
        "name": "审校专家",
        "outputType": "ReviewIssues",
        "systemPrompt": (
            f"你是小说写作「审校专家」（静态审校）。\n"
            "只定位问题并给建议，不重写全文。\n"
            "按问题分类与严重度输出（如 continuity, motivation, pacing, clarity, style），建议需具体可执行。\n"
            "允许调用工具核对设定一致性。\n"
            f"{CHAPTER_LOCATOR_HARD_RULE}\n"
            f"{OUTLINE_LOCATOR_HARD_RULE}\n\n"
            "请执行两轮内部审校：\n"
            "1) 第一轮做类型化问题扫描；\n"
            "2) 第二轮做反证去误报（证据不足或可合理解释的问题不输出）。\n"
            "最终只保留高置信问题。\n\n"
            "输出必须是 JSON，且只包含约定字段（issues 数组或等价数组结构）；\n"
            "每条问题需包含位置线索、严重度、建议与必要上下文；"
            "若在 suggestion/context 中给出替换句示例，对白部分须用中文弯双引号“与”。"
        ),
    },
    STAGES.POLISH: {
        "name": "润色专家",
        "outputType": "PolishedResult",
        "systemPrompt": (
            f"你是小说写作「润色专家」（Patch 式修复）。基于审校问题与局部上下文逐点修复，避免大范围无关改写；"
            "优先修复高严重度问题，并保持剧情/人设不变形。若需写回当前章节，必须调用 editChapterContent；"
            "调用成功后，输出 JSON 时 finalText 可短占位，但 changeSummary 必须清晰说明改动点与影响范围。"
            "可调用 addSparkIdea 等辅助工具。最终输出必须是 JSON，且只包含约定字段。\n"
            f"{BODY_DIALOGUE_QUOTE_RULE}\n"
            f"{CHAPTER_LOCATOR_HARD_RULE}\n"
            f"{OUTLINE_LOCATOR_HARD_RULE}"
        ),
    },
}


# ---------------------------------------------------------------------------
# JSON extraction / parsing
# ---------------------------------------------------------------------------

def _parse_json_safe(text: str, fallback: Any = None) -> Any:
    try:
        return json.loads(str(text or ""))
    except Exception:
        return fallback


def extract_structured_json_from_model_text(text: str) -> dict | list | None:
    """Extract JSON (object or array) from model output that may be wrapped in markdown fences."""
    s = str(text or "").strip()
    if not s:
        return None

    try:
        return json.loads(s)
    except Exception:
        pass

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        inner = (fence.group(1) or "").strip()
        try:
            return json.loads(inner)
        except Exception:
            pass

    obj_start = s.find("{")
    obj_end = s.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        try:
            return json.loads(s[obj_start: obj_end + 1])
        except Exception:
            pass

    arr_start = s.find("[")
    arr_end = s.rfind("]")
    if arr_start >= 0 and arr_end > arr_start:
        try:
            return json.loads(s[arr_start: arr_end + 1])
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalize_analyze_report(raw: Any, fallback_user_text: str = "") -> dict:
    val = raw if isinstance(raw, dict) else {}
    return {
        "summary": str(val.get("summary") or fallback_user_text or "")[:1200],
        "goals": [str(x) for x in val.get("goals", [])][:12] if isinstance(val.get("goals"), list) else [],
        "constraints": [str(x) for x in val.get("constraints", [])][:12] if isinstance(val.get("constraints"), list) else [],
        "risks": [str(x) for x in val.get("risks", [])][:12] if isinstance(val.get("risks"), list) else [],
        "evidence": [
            {
                "source": str((x or {}).get("source", "") if isinstance(x, dict) else ""),
                "snippet": str((x or {}).get("snippet", "") if isinstance(x, dict) else "")[:400],
            }
            for x in (val.get("evidence") or [])
        ][:12] if isinstance(val.get("evidence"), list) else [],
    }


def _normalize_beat_entry(x: Any) -> dict | str | None:
    """保留结构化节拍（dict），供摘要渲染；纯字符串节拍仍支持。"""
    if isinstance(x, dict):
        out: dict[str, Any] = {}
        for k, v in x.items():
            key = str(k).strip()[:64]
            if not key:
                continue
            if isinstance(v, list):
                items = [str(i).strip()[:600] for i in v[:30]]
                items = [i for i in items if i]
                if items:
                    out[key] = items
            elif isinstance(v, dict):
                s = json.dumps(v, ensure_ascii=False)
                if s:
                    out[key] = s[:1200]
            else:
                sv = str(v).strip()
                if not sv:
                    continue
                lim = (
                    8000
                    if key.lower()
                    in ("content", "body", "text", "description", "summary", "plot")
                    else 2000
                )
                out[key] = sv[:lim]
        return out if out else None
    s = str(x).strip()
    return s[:3000] if s else None


def normalize_writing_blueprint(raw: Any) -> dict:
    val = raw if isinstance(raw, dict) else {}
    beats_out: list[dict | str] = []
    raw_beats = val.get("beats")
    if isinstance(raw_beats, list):
        for item in raw_beats[:20]:
            nb = _normalize_beat_entry(item)
            if nb is not None:
                beats_out.append(nb)
    return {
        "chapterGoal": str(val.get("chapterGoal") or "")[:1000],
        "beats": beats_out,
        "tone": str(val.get("tone") or "")[:200],
        "constraints": [str(x) for x in val.get("constraints", [])][:20] if isinstance(val.get("constraints"), list) else [],
        "requiredMaterials": [
            {
                "type": str((x or {}).get("type", "") if isinstance(x, dict) else ""),
                "ref": str((x or {}).get("ref", "") if isinstance(x, dict) else ""),
                "note": str((x or {}).get("note", "") if isinstance(x, dict) else "")[:300],
            }
            for x in (val.get("requiredMaterials") or [])
        ][:40] if isinstance(val.get("requiredMaterials"), list) else [],
    }


def normalize_draft_document(raw: Any) -> dict:
    val = raw if isinstance(raw, dict) else {}
    return {
        "title": str(val.get("title") or ""),
        "content": str(val.get("content") or ""),
        "notes": [str(x) for x in val.get("notes", [])][:12] if isinstance(val.get("notes"), list) else [],
    }


def normalize_style_unify_result(raw: Any) -> dict:
    val = raw if isinstance(raw, dict) else {}

    prior = []
    if isinstance(val.get("priorChaptersRead"), list):
        for x in val["priorChaptersRead"][:8]:
            if not isinstance(x, dict):
                x = {}
            ci = x.get("chapterIndex")
            idx = int(ci) if isinstance(ci, (int, float)) and math.isfinite(ci) else 0
            prior.append({
                "chapterIndex": idx,
                "title": str(x.get("title") or "")[:120],
            })

    return {
        "styleAnchors": str(val.get("styleAnchors") or "")[:4000],
        "content": str(val.get("content") or "")[:500000],
        "changeSummary": str(val.get("changeSummary") or "")[:2000],
        "priorChaptersRead": prior,
    }


def normalize_review_issues(raw: Any) -> list[dict]:
    lst: list = []
    if isinstance(raw, list):
        lst = raw
    elif isinstance(raw, dict) and isinstance(raw.get("issues"), list):
        lst = raw["issues"]

    def _safe_int(v: Any) -> int:
        try:
            n = float(v)
            return int(n) if math.isfinite(n) else 0
        except Exception:
            return 0

    return [
        {
            "segmentIndex": _safe_int((x or {}).get("segmentIndex") if isinstance(x, dict) else 0),
            "span": str((x or {}).get("span", "") if isinstance(x, dict) else "")[:200],
            "issueType": str((x or {}).get("issueType", "general") if isinstance(x, dict) else "general")[:80],
            "severity": str((x or {}).get("severity", "medium") if isinstance(x, dict) else "medium")[:20],
            "suggestion": str((x or {}).get("suggestion", "") if isinstance(x, dict) else "")[:600],
            "context": str((x or {}).get("context", "") if isinstance(x, dict) else "")[:500],
        }
        for x in lst
    ][:120]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_subagent_config() -> bool:
    from services.subagent_pipeline import build_tool_permissions_for_stage

    for key in [STAGES.ANALYZE, STAGES.PLAN, STAGES.DRAFT, STAGES.STYLE_UNIFY, STAGES.REVIEW, STAGES.POLISH]:
        if key not in SUBAGENT_REGISTRY:
            raise RuntimeError(f"subagent registry 缺少阶段: {key}")
        tools = build_tool_permissions_for_stage(key)
        if not tools:
            raise RuntimeError(f"subagent 工具权限为空: {key}")
    return True
