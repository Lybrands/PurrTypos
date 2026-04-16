"""
Writing-expert hard rules, JSON extraction, and structured-output normalisers.

Formerly part of subagent_config.py; registry / stages / pipeline validation removed.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


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

CHARACTER_LOOKUP_HARD_RULE = (
    "【硬性约束｜人物核对】涉及具体人物（出现在正文、节拍、建议或审校上下文）时，"
    "若无法从当前章正文 / 大纲 / 已读前文中**直接验证**人物是否为既有角色，"
    "必须先调用 listBookCharacters 获取本书真实人物 id 与姓名；"
    "如需人设详情（职业/性格/外貌/关系等）再用 getBookCharacters（优先传 characterIds，必要时用 names 做关键词筛选）。"
    "严禁凭空编造人物、给既有人物改名或合并同一人物为多人。\n"
    "若确有必要引入**新人物**（例如路人、反派、关键配角尚未登记），必须：\n"
    "1) 明确标注为「新建人物」并说明登场动机与作用；\n"
    "2) 核对 listBookCharacters，确保姓名与既有角色不重复、不冲突；\n"
    "3) 人设特征与本书世界观/已确立人物关系保持一致，不得与既有设定矛盾。\n"
    "人物定位信息不足时，先补充查询，不得盲调工具或盲写正文。"
)

BODY_DIALOGUE_QUOTE_RULE = (
    "【正文对白标点】叙事中人物对白须用中文弯双引号“与”成对包裹（勿用半角直引号 \" 与 ' 作对白起止）；"
    "对白内嵌套引语时内层可用中文单引号‘与’。"
)


# ---------------------------------------------------------------------------
# JSON extraction / parsing
# ---------------------------------------------------------------------------

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


def normalize_polished_result(raw: Any) -> dict:
    """Polish stage: finalText + changeSummary."""
    val = raw if isinstance(raw, dict) else {}
    return {
        "finalText": str(val.get("finalText") or val.get("content") or "")[:500000],
        "changeSummary": str(val.get("changeSummary") or "")[:2000],
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
