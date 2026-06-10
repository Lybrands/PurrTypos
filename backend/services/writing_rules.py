"""
Writing-expert hard rules, JSON extraction, and structured-output normalisers.

Formerly part of subagent_config.py; registry / stages / pipeline validation removed.

—— 2026 重构记录 ——
6 个 ``normalize_*`` 函数原本各自重复手写 ``str(val.get(...) or "")[:N]`` /
``isinstance(x, list)`` / ``isinstance(x, dict)`` 模板。本轮把这套模板
抽成 ``_truncated_str / _str_list / _dict_list / _safe_int / _coerce_dict``
五个 helper，行为**完全不变**（由 ``tests/test_writing_rules.py`` 用例保证），
只是去重 + 让"字段截断长度"这种业务约束直接显形为 helper 调用的参数。
未来想加 schema 校验或 logging，只需改 helper。
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
    """从模型输出里抽 JSON，依次尝试：纯 JSON → ```json fence``` → 第一个 {...} → 第一个 [...]。"""
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
# Helpers —— 6 个 normalize 函数共享的"截断 / 类型兜底"原语
# ---------------------------------------------------------------------------

def _truncated_str(val: Any, max_len: int, default: str = "") -> str:
    """``str(val or default)[:max_len]`` 的语义化版本。

    注意：保留原 ``or`` 语义 —— ``val`` 为 falsy（None / "" / 0 / []）时落到 ``default``。
    历史上 normalize 函数全是字符串字段，这种 falsy 兜底没有副作用；引入到数字字段
    时请改用 ``_safe_int``。
    """
    s = str(val or default or "")
    return s[:max_len]


def _str_list(val: Any, max_each: int, max_count: int) -> list[str]:
    """list[str] 字段：非 list → []；按 ``max_count`` 截断、每项 ``str(x)[:max_each]``。"""
    if not isinstance(val, list):
        return []
    return [str(x)[:max_each] for x in val[:max_count]]


def _coerce_dict(x: Any) -> dict:
    """list 中遇到非 dict 元素时统一兜底为空 dict（保持原行为：字段全部回落 default）。"""
    return x if isinstance(x, dict) else {}


def _dict_list(
    val: Any, mapper: Callable[[Any], dict], max_count: int,
) -> list[dict]:
    """list[dict] 字段：非 list → []；按 ``max_count`` 截断；每项喂 ``mapper`` 产出归一化 dict。

    ``mapper`` 自己负责 isinstance 兜底（推荐配合 ``_coerce_dict``）。
    """
    if not isinstance(val, list):
        return []
    return [mapper(x) for x in val[:max_count]]


def _safe_int(val: Any) -> int:
    """数字字段：转 float → int，nan/inf/异常一律 0。"""
    try:
        n = float(val)
        return int(n) if math.isfinite(n) else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Normalisation —— 每个函数现在只负责"声明字段 → 调 helper"
# ---------------------------------------------------------------------------

def normalize_analyze_report(raw: Any, fallback_user_text: str = "") -> dict:
    val = _coerce_dict(raw)

    def _evidence(x: Any) -> dict:
        e = _coerce_dict(x)
        return {
            "source": str(e.get("source", "")),
            "snippet": _truncated_str(e.get("snippet", ""), 400),
        }

    return {
        "summary": _truncated_str(val.get("summary"), 1200, default=fallback_user_text),
        "goals": _str_list(val.get("goals"), max_each=10_000, max_count=12),
        "constraints": _str_list(val.get("constraints"), max_each=10_000, max_count=12),
        "risks": _str_list(val.get("risks"), max_each=10_000, max_count=12),
        "evidence": _dict_list(val.get("evidence"), _evidence, max_count=12),
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
                # 节拍正文类字段（content/body/text/description/summary/plot）放宽到 8000；
                # 其余字段（title/note/...）截 2000，防止模型把整段塞到非正文键里
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
    val = _coerce_dict(raw)

    beats_out: list[dict | str] = []
    raw_beats = val.get("beats")
    if isinstance(raw_beats, list):
        for item in raw_beats[:20]:
            nb = _normalize_beat_entry(item)
            if nb is not None:
                beats_out.append(nb)

    def _material(x: Any) -> dict:
        m = _coerce_dict(x)
        return {
            "type": str(m.get("type", "")),
            "ref": str(m.get("ref", "")),
            "note": _truncated_str(m.get("note", ""), 300),
        }

    return {
        "chapterGoal": _truncated_str(val.get("chapterGoal"), 1000),
        "beats": beats_out,
        "tone": _truncated_str(val.get("tone"), 200),
        "constraints": _str_list(val.get("constraints"), max_each=10_000, max_count=20),
        "requiredMaterials": _dict_list(val.get("requiredMaterials"), _material, max_count=40),
    }


def normalize_draft_document(raw: Any) -> dict:
    val = _coerce_dict(raw)
    return {
        "title": str(val.get("title") or ""),
        "content": str(val.get("content") or ""),
        "notes": _str_list(val.get("notes"), max_each=10_000, max_count=12),
    }


def normalize_style_unify_result(raw: Any) -> dict:
    val = _coerce_dict(raw)

    def _prior_chapter(x: Any) -> dict:
        e = _coerce_dict(x)
        return {
            "chapterIndex": _safe_int(e.get("chapterIndex")),
            "title": _truncated_str(e.get("title"), 120),
        }

    return {
        "styleAnchors": _truncated_str(val.get("styleAnchors"), 4000),
        "content": _truncated_str(val.get("content"), 500_000),
        "changeSummary": _truncated_str(val.get("changeSummary"), 2000),
        "priorChaptersRead": _dict_list(val.get("priorChaptersRead"), _prior_chapter, max_count=8),
    }


def normalize_polished_result(raw: Any) -> dict:
    """Polish stage: ``finalText`` 优先，缺失则回落到 ``content``。"""
    val = _coerce_dict(raw)
    final_raw = val.get("finalText") or val.get("content") or ""
    return {
        "finalText": _truncated_str(final_raw, 500_000),
        "changeSummary": _truncated_str(val.get("changeSummary"), 2000),
    }


def normalize_review_issues(raw: Any) -> list[dict]:
    """Review stage：接受 ``[...]`` 或 ``{"issues": [...]}`` 两种形态。"""
    if isinstance(raw, list):
        lst: list = raw
    elif isinstance(raw, dict) and isinstance(raw.get("issues"), list):
        lst = raw["issues"]
    else:
        if raw is not None:
            logger.debug("normalize_review_issues: unexpected input type=%s", type(raw).__name__)
        return []

    def _issue(x: Any) -> dict:
        e = _coerce_dict(x)
        return {
            "segmentIndex": _safe_int(e.get("segmentIndex")),
            "span": _truncated_str(e.get("span", ""), 200),
            "issueType": _truncated_str(e.get("issueType"), 80, default="general"),
            "severity": _truncated_str(e.get("severity"), 20, default="medium"),
            "suggestion": _truncated_str(e.get("suggestion", ""), 600),
            "context": _truncated_str(e.get("context", ""), 500),
        }

    return _dict_list(lst, _issue, max_count=120)
