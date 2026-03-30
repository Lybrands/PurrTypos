"""
Subagent multi-stage pipeline — port of electron/subagentPipeline.js.

Orchestrates the writing-expert stages (analyze → plan → draft →
style_unify → review → polish) and streams the final presenter
reply to the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from services.agent_tool_definitions import to_openai_tools
from services.ai_provider import create_chat_no_stream, create_chat_stream
from services.subagent_config import (
    BODY_DIALOGUE_QUOTE_RULE,
    EXEC_ACTIONS,
    STAGES,
    SUBAGENT_REGISTRY,
    extract_structured_json_from_model_text,
    normalize_analyze_report,
    normalize_draft_document,
    normalize_review_issues,
    normalize_style_unify_result,
    normalize_writing_blueprint,
    validate_subagent_config,
)
from services.tool_executor import run_tools as executor_run_tools
from services.tool_router import ensure_skills_loaded, get_api_skill_items
from utils.streaming import text_from_chat_delta, text_from_stream_choice0
from utils.tool_call_utils import normalize_tool_calls_list
from utils.writing_chapters import get_writable_chapters_for_agent

logger = logging.getLogger(__name__)

try:
    validate_subagent_config()
except Exception as exc:
    logger.error("[subagent-config] invalid: %s", exc)


# ---------------------------------------------------------------------------
# Stage tool permissions (pinned tools per stage)
# ---------------------------------------------------------------------------

STAGE_PINNED_TOOLS: dict[str, list[str]] = {
    STAGES.ANALYZE: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "listOutlines", "queryOutline",
    ],
    STAGES.PLAN: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "listOutlines", "queryOutline",
    ],
    STAGES.DRAFT: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "createWritingChapter",
    ],
    STAGES.STYLE_UNIFY: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents",
    ],
    STAGES.REVIEW: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents",
    ],
    STAGES.POLISH: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "createWritingChapter", "editChapterContent",
    ],
}

CHAPTER_CATALOG_BODY_TOOL_NAMES = frozenset([
    "getChapterContent", "editChapterContent",
    "batchGetChapterContents", "createWritingChapter",
])


def build_tool_permissions_for_stage(stage: str) -> list[str]:
    return list(STAGE_PINNED_TOOLS.get(stage, []))


# ---------------------------------------------------------------------------
# Tool schema helpers
# ---------------------------------------------------------------------------

def filter_tool_schemas_by_names(
    tool_schemas: list[dict], allowed_names: set[str],
) -> list[dict]:
    if not tool_schemas or not allowed_names:
        return []
    return [
        t for t in tool_schemas
        if (t.get("function") or {}).get("name") in allowed_names
    ]


def _ensure_stage_candidate_tools(
    candidate_tools: list[dict], allowed_set: set[str],
) -> list[dict]:
    ensure_skills_loaded()
    all_api = to_openai_tools(get_api_skill_items())
    all_by_name = {(t.get("function") or {}).get("name"): t for t in all_api}
    present: set[str] = set()
    out: list[dict] = []
    for t in (candidate_tools or []):
        n = (t.get("function") or {}).get("name")
        if n and n in allowed_set and n not in present:
            out.append(t)
            present.add(n)
    for nm in allowed_set:
        if nm not in present and nm in all_by_name:
            out.append(all_by_name[nm])
            present.add(nm)
    return out


def _ensure_pinned_tools_for_stage(
    stage: str, tool_schemas: list[dict], allowed_set: set[str],
) -> list[dict]:
    if not stage or not tool_schemas:
        return tool_schemas or []
    pinned = [n for n in (STAGE_PINNED_TOOLS.get(stage) or []) if n in allowed_set]
    if not pinned:
        return tool_schemas
    ensure_skills_loaded()
    all_api = to_openai_tools(get_api_skill_items())
    by_name = {(t.get("function") or {}).get("name"): t for t in all_api}
    present = {(t.get("function") or {}).get("name") for t in tool_schemas}
    out = list(tool_schemas)
    for nm in pinned:
        if nm not in present and nm in by_name:
            out.append(by_name[nm])
            present.add(nm)
    return out


def _tool_result_looks_like_json_error(content: str) -> bool:
    if not isinstance(content, str):
        return False
    s = content.strip()
    if not s.startswith("{"):
        return False
    try:
        o = json.loads(s)
        return isinstance(o, dict) and isinstance(o.get("error"), str) and len(o["error"]) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Context appendix
# ---------------------------------------------------------------------------

def _build_subagent_tooling_context_appendix(tool_ctx: dict) -> str:
    if not tool_ctx or tool_ctx.get("bookId") is None:
        return ""
    wc_raw = tool_ctx.get("writingChapters") or []
    wc = get_writable_chapters_for_agent(wc_raw)
    ao = tool_ctx.get("availableOutlines") or []
    acc_ch = tool_ctx.get("associatedChapterIds") or []
    acc_ol = tool_ctx.get("associatedOutlineIds") or []

    lines: list[str] = []
    lines.append("【会话同步 — 工具与界面上下文】")
    lines.append(
        "宿主已为当前会话绑定书籍与章节；**请勿在工具参数中手写 bookId**（一律由工具层使用当前会话书籍）。"
        "章节工具仅允许 chapterId；大纲查询仅允许 outlineId/outlineIds；禁止使用序号或标题作为定位参数。"
    )
    lines.append("若需操作非当前章节，必须先 listWritingChapters 读取真实 chapterId，再传 chapterId。")

    ch_desc = (
        f"《{re.sub(chr(10), ' ', str(tool_ctx.get('currentChapterTitle') or ''))}》"
        if tool_ctx.get("chapterId") else "（未选章节）"
    )
    lines.append(f"当前写作章节：{ch_desc}")

    if acc_ch and wc:
        bits = []
        for cid in acc_ch:
            c = next((x for x in wc if str(x.get("id")) == str(cid)), None)
            title = re.sub(r"\r?\n", " ", str(c.get("title") or "")).strip() if c else ""
            bits.append(f"《{title}》" if title else "（未匹配章节）")
        lines.append(f"用户在本轮关联的写作章节：{'、'.join(bits)}")
    if acc_ol and ao:
        bits = []
        for oid in acc_ol:
            o = next((x for x in ao if str(x.get("id")) == str(oid)), None)
            title = re.sub(r"\r?\n", " ", str(o.get("title") or "")).strip() if o else ""
            bits.append(f"《{title}》" if title else "（未匹配大纲）")
        lines.append(f"用户在本轮关联的大纲：{'、'.join(bits)}")

    WC_CAP = 100
    if wc:
        lines.append(f"写作章节目录（仅含可写正文的章节，每条给出 chapterId）。共 {len(wc)} 条：")
        for idx, c in enumerate(wc[:WC_CAP]):
            lines.append(f"- chapterId={str(c.get('id', '')).strip()} {str(c.get('title', '')).replace(chr(10), ' ')[:120]}")
        if len(wc) > WC_CAP:
            lines.append(f"… 余 {len(wc) - WC_CAP} 条略")

    AO_CAP = 60
    if ao:
        lines.append(f"大纲列表（每项含 outlineId；queryOutline 仅传 outlineId/outlineIds，勿传纯数字序号）。共 {len(ao)} 条：")
        for idx, o in enumerate(ao[:AO_CAP]):
            typ = str(o.get("type", "")) if o.get("type") is not None else ""
            oid = str(o.get("id", "")).strip()
            t = re.sub(r"\r?\n", " ", str(o.get("title") or ""))[:80]
            lines.append(f"- [{idx + 1}] outlineId={oid} {t}" + (f"\t{typ}" if typ else ""))
        if len(ao) > AO_CAP:
            lines.append(f"… 余 {len(ao) - AO_CAP} 条略")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Draft/text helpers
# ---------------------------------------------------------------------------

def split_draft_into_segments(text: str, max_chars: int = 1600) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    paras = [p.strip() for p in re.split(r"\n{2,}", source) if p.strip()]
    if not paras:
        return [source]
    out: list[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
            continue
        if len(buf + "\n\n" + p) > max_chars:
            out.append(buf)
            buf = p
        else:
            buf += "\n\n" + p
    if buf:
        out.append(buf)
    return out


def extract_context_around_span(text: str, span: str, radius: int = 240) -> str:
    src = str(text or "")
    s = str(span or "").strip()
    if not src or not s:
        return ""
    idx = src.find(s)
    if idx < 0:
        return s
    start = max(0, idx - radius)
    end = min(len(src), idx + len(s) + radius)
    return src[start:end]


def _resolve_draft_plain_text(draft_doc: dict | None, user_text: str, action: str) -> str:
    from_model = str((draft_doc or {}).get("content") or "").strip()
    if from_model:
        return from_model
    need_body = action in (
        EXEC_ACTIONS.REVIEW, EXEC_ACTIONS.POLISH,
        EXEC_ACTIONS.FULL, EXEC_ACTIONS.STYLE_UNIFY,
    )
    u = str(user_text or "").strip()
    if need_body and len(u) >= 40:
        return u
    return ""


def _looks_like_body_text(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    if re.fullmatch(r"【?\s*已写入编辑器\s*】?", s):
        return False
    if re.fullmatch(r"【?\s*正文以已写入编辑器为准\s*】?", s):
        return False
    if len(s) >= 80:
        return True
    return bool(re.search(r"[\n。！？；]", s) and len(s) >= 20)


def _normalize_pipeline_flags(agent_actions: list[str] | None) -> dict:
    lst = [str(a) for a in agent_actions] if isinstance(agent_actions, list) and agent_actions else [EXEC_ACTIONS.FULL]
    keys = set(lst)
    if EXEC_ACTIONS.FULL in keys:
        return {
            "needPlan": True, "needDraft": True, "needStyleUnify": True,
            "needReview": True, "needPolish": True, "onlyDraft": False,
            "resolveAction": EXEC_ACTIONS.FULL,
            "pipelineActionLabel": "full",
        }
    need_plan = any(k in keys for k in ("plan", "draft", "styleUnify", "review", "polish"))
    need_draft = any(k in keys for k in ("draft", "styleUnify", "review", "polish"))
    need_style = any(k in keys for k in ("styleUnify", "review", "polish"))
    need_review = any(k in keys for k in ("review", "polish"))
    need_polish = "polish" in keys
    only_draft = len(keys) == 1 and "draft" in keys
    resolve_action = EXEC_ACTIONS.ANALYZE
    if need_polish:
        resolve_action = EXEC_ACTIONS.POLISH
    elif need_review:
        resolve_action = EXEC_ACTIONS.REVIEW
    elif need_style:
        resolve_action = EXEC_ACTIONS.STYLE_UNIFY
    elif need_draft:
        resolve_action = EXEC_ACTIONS.DRAFT
    elif need_plan:
        resolve_action = EXEC_ACTIONS.PLAN
    return {
        "needPlan": need_plan, "needDraft": need_draft, "needStyleUnify": need_style,
        "needReview": need_review, "needPolish": need_polish, "onlyDraft": only_draft,
        "resolveAction": resolve_action,
        "pipelineActionLabel": "+".join(sorted(keys)),
    }


def _build_style_unify_prior_chapter_hint(tool_ctx: dict) -> str:
    if not tool_ctx or tool_ctx.get("bookId") is None:
        return "【宿主提示】未绑定书籍时仍应用工具列出章节并自行选取前文。"
    wc_raw = tool_ctx.get("writingChapters") or []
    wc = get_writable_chapters_for_agent(wc_raw)
    cur_id = tool_ctx.get("chapterId")
    if not cur_id or not wc:
        return "【宿主提示】未选可写章节或目录为空：请先 listWritingChapters；若无前文则在 styleAnchors 中说明。"
    idx = next((i for i, c in enumerate(wc) if str(c.get("id")) == str(cur_id)), -1)
    if idx < 0:
        return "【宿主提示】当前章节不在可写目录中：请核对 chapterId。"
    if idx == 0:
        return "【宿主提示】当前章为目录中第 1 章，无前文：styleAnchors 须注明「无前文可参照」，仅做语气与蓝图内约束下的统一。"
    n_prior = idx
    n_read = min(5, n_prior) if n_prior >= 3 else n_prior
    start = idx - n_read
    lines = [f"【宿主推算】当前章在写作目录中为第 {idx + 1} 章。请必读以下 {n_read} 章正文以萃取文风（紧邻当前章向前的连续章）："]
    for j in range(start, idx):
        t = re.sub(r"\r?\n", " ", str(wc[j].get("title") or ""))[:120]
        lines.append(f"- chapterId={str(wc[j].get('id', '')).strip()} 《{t}》")
    lines.append("请优先使用 batchGetChapterContents 一次传入多个 chapterId；或多次 getChapterContent。归纳 styleAnchors 后再改写下方「待统一初稿」。")
    lines.append("batchGetChapterContents.chapterIds 参数类型必须是 string[]，且每项都来自上方 chapterId。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Presenter prompts
# ---------------------------------------------------------------------------

MAIN_AGENT_PRESENTER_EXTRA = (
    "你是主稿专家：用户看到的回复正文只能由你通过流式输出给出。"
    "后台各专业写作专家已完成本会话中的结构化步骤，并在用户消息中提供了「内部产出」（含结构化字段与/或正文）。"
    "请严格依据用户问题与这些产出，用自然、清晰的中文作答；结构复杂时可用小标题或列表。"
    "不要向用户提及写作专家、管线、阶段、JSON 或「内部产出」等实现细节；不要整段照抄 JSON。"
    "若产出中已有可交付的正文（如初稿、润色定稿），应在回答中完整呈现该正文，并可酌情加简短辅说明。\n"
    f"{BODY_DIALOGUE_QUOTE_RULE}"
)

MAIN_AGENT_TRANSITION_SYSTEM = (
    "你是主稿专家：用户只能看到你输出的文字。各子步骤在后台静默完成并已汇总到上下文；子步骤的原始 JSON/工具细节对用户不可见。"
    "请用一两句自然、简短的中文概括刚为用户完成了什么、接下来要做什么；不要输出 JSON、不要复述内部字段名、不要长篇。"
)

TRANSITION_MAX_TOKENS = 320


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------

async def _pipe_model_stream_to_chunks(
    *,
    send_chunk: Callable,
    signal: asyncio.Event | None,
    key: str,
    api_provider: str,
    stream_messages: list[dict],
    request_params: dict,
    model_name: str,
    param_overrides: dict | None = None,
    emit_done_when_finished: bool = False,
) -> None:
    params = {**request_params, "model": model_name, **(param_overrides or {})}
    params.pop("tools", None)

    result = await create_chat_stream(key, stream_messages, params, api_provider, signal)
    stream = result.get("stream")
    if not stream:
        return

    finished = False
    try:
        async for chunk in stream:
            if signal and signal.is_set():
                break
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            choice0 = choices[0] if choices else None
            if not choice0:
                continue
            content_delta = text_from_stream_choice0(choice0)
            if content_delta:
                send_chunk({"delta": content_delta})
            finish_reason = choice0.get("finish_reason")
            if finish_reason in ("stop", "length"):
                finished = True
                if emit_done_when_finished:
                    send_chunk({"done": True, "model": model_name})
                return
    except Exception:
        if not (signal and signal.is_set()):
            raise

    if emit_done_when_finished and not finished:
        send_chunk({"done": True, "model": model_name, "aborted": bool(signal and signal.is_set())})


async def _stream_stage_transition(
    *,
    send_chunk: Callable,
    signal: asyncio.Event | None,
    key: str,
    api_provider: str,
    request_params: dict,
    messages: list[dict],
    user_text: str,
    completed_stage_name: str,
    next_line: str,
    model_name: str,
) -> None:
    if signal and signal.is_set():
        return
    send_chunk({"subagentBridging": True})
    base_system = next((str(m.get("content") or "") for m in messages if m and m.get("role") == "system"), "").strip()
    system_content = "\n\n".join(filter(None, [base_system, MAIN_AGENT_TRANSITION_SYSTEM]))
    excerpt = user_text[:600] + "…" if len(user_text) > 600 else user_text
    user_content = (
        f"【用户诉求摘要】\n{excerpt}"
        f"\n【刚完成的子步骤】{completed_stage_name}"
        + (f"\n【下一步提示（请用自然语气转述，勿照抄本行）】{next_line}" if next_line else "")
        + "\n请只写简短过渡说明（建议不超过 120 字）。"
    )
    cap = min(TRANSITION_MAX_TOKENS, request_params.get("max_tokens", 1024) if isinstance(request_params.get("max_tokens"), int) and request_params.get("max_tokens", 0) > 0 else 1024)
    try:
        await _pipe_model_stream_to_chunks(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            stream_messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            request_params=request_params,
            model_name=model_name,
            param_overrides={"max_tokens": cap},
            emit_done_when_finished=False,
        )
        if not (signal and signal.is_set()):
            send_chunk({"delta": "\n\n"})
    finally:
        send_chunk({"subagentBridging": False})


async def _stream_main_agent_presenter(
    *,
    send_chunk: Callable,
    signal: asyncio.Event | None,
    key: str,
    api_provider: str,
    request_params: dict,
    messages: list[dict],
    user_text: str,
    pipeline_action_label: str,
    analyze_report: dict | None,
    blueprint: dict | None,
    draft_plain: str,
    style_unify_report: dict | None,
    style_skip_full_text: bool,
    review_issues: list[dict],
    polish_final_text: str,
    polish_change_summary: str,
    polish_skip_full_text: bool,
    model_name: str,
) -> None:
    base_system = next((str(m.get("content") or "") for m in messages if m and m.get("role") == "system"), "").strip()
    polish_hint = (
        "润色结果已通过工具写入当前章节：回复中不要重复粘贴润色后的全文，用一两句说明已保存即可，可结合下方「修订摘要」简述改动。"
        if polish_skip_full_text else ""
    )
    style_hint = (
        "风格统一后的正文已通过工具写入当前章节：回复中不要重复粘贴该正文，用一两句说明已保存即可，可结合「文风锚点」或「风格修订摘要」简述。"
        if style_skip_full_text else ""
    )
    system_content = "\n\n".join(filter(None, [
        base_system, MAIN_AGENT_PRESENTER_EXTRA, polish_hint, style_hint,
        f"本轮管线动作：{pipeline_action_label or ''}。",
    ]))

    parts = [f"【用户问题】\n{user_text}", "\n【内部产出 — 仅供你组织给用户的最终回复】\n"]
    if analyze_report and isinstance(analyze_report, dict) and analyze_report:
        parts.append("\n## 分析报告（结构化）\n")
        parts.append(json.dumps(analyze_report, ensure_ascii=False, indent=2))
    if blueprint and isinstance(blueprint, dict) and blueprint:
        parts.append("\n## 写作蓝图（结构化）\n")
        parts.append(json.dumps(blueprint, ensure_ascii=False, indent=2))
    sur = style_unify_report if isinstance(style_unify_report, dict) else None
    sa = str((sur or {}).get("styleAnchors") or "").strip()
    if sa:
        parts.append("\n## 文风锚点（摘录）\n")
        parts.append(sa)
    cs_style = str((sur or {}).get("changeSummary") or "").strip()
    if cs_style:
        parts.append("\n## 风格修订摘要\n")
        parts.append(cs_style)
    dp = str(draft_plain or "").strip()
    if dp:
        if style_skip_full_text:
            parts.append("\n## 正文（风格统一后）\n")
            parts.append("（该正文已由风格统一阶段写入当前章节，请勿在对话中再次输出全文。）\n")
        else:
            parts.append("\n## 初稿正文\n")
            parts.append(dp)
    if review_issues:
        parts.append("\n## 审校问题列表（结构化）\n")
        parts.append(json.dumps(review_issues, ensure_ascii=False, indent=2))
    pf = str(polish_final_text or "").strip()
    if polish_skip_full_text:
        parts.append("\n## 润色定稿\n")
        parts.append("（正文已由润色阶段写入当前章节，请勿在对话中再次输出全文。）\n")
    elif pf:
        parts.append("\n## 润色定稿（若存在，应在回复中完整呈现）\n")
        parts.append(pf)
    ps = str(polish_change_summary or "").strip()
    if ps:
        parts.append("\n## 修订摘要\n")
        parts.append(ps)

    presenter_messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "\n".join(parts)},
    ]
    send_chunk({"subagentMainPresenter": True})
    await _pipe_model_stream_to_chunks(
        send_chunk=send_chunk,
        signal=signal,
        key=key,
        api_provider=api_provider,
        stream_messages=presenter_messages,
        request_params=request_params,
        model_name=model_name,
        emit_done_when_finished=True,
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_subagent_pipeline(
    *,
    send_chunk: Callable,
    signal: asyncio.Event | None,
    key: str,
    api_provider: str,
    request_params: dict,
    tool_ctx: dict,
    messages: list[dict],
    skill_specs: dict,
    agent_actions: list[str] | None = None,
    model: str,
) -> None:
    """Run the multi-stage writing-expert pipeline."""
    rp = {**request_params, "thinking": {"type": "disabled"}}
    tooling_appendix = _build_subagent_tooling_context_appendix(tool_ctx)

    async def get_tools_for_stage(stage: str) -> list[dict]:
        allowed_set = set(STAGE_PINNED_TOOLS.get(stage) or [])
        all_tools = to_openai_tools(get_api_skill_items())
        candidates = filter_tool_schemas_by_names(all_tools, allowed_set)
        candidates = _ensure_stage_candidate_tools(candidates, allowed_set)
        filtered = filter_tool_schemas_by_names(candidates, allowed_set)
        return _ensure_pinned_tools_for_stage(stage, filtered, allowed_set)

    async def chat_no_stream_unified(msgs: list[dict], stage_tools: list[dict]) -> dict:
        params = dict(rp)
        if stage_tools:
            params["tools"] = stage_tools
        return await create_chat_no_stream(key, msgs, params, api_provider, signal)

    def mark_edit_saved(used_calls: list[dict], tool_results: list[dict]) -> bool:
        for tc in (used_calls or []):
            if (tc.get("function") or {}).get("name") != "editChapterContent":
                continue
            tr = next((r for r in (tool_results or []) if r.get("tool_call_id") == tc.get("id")), None)
            if not tr or not tr.get("content"):
                continue
            try:
                o = json.loads(tr["content"])
                if o and o.get("success") is True:
                    return True
            except Exception:
                pass
        return False

    async def run_non_stream_tool_loop(
        current_messages: list[dict], stage_tools: list[dict], stage_name: str,
    ) -> dict:
        allowed_names = set(STAGE_PINNED_TOOLS.get(stage_name) or [])
        prev_allowed = tool_ctx.get("subagentAllowedToolNames")
        tool_ctx["subagentAllowedToolNames"] = allowed_names

        loop_messages = list(current_messages)
        final_text = ""
        edit_chapter_saved = False

        for _guard in range(6):
            if signal and signal.is_set():
                break
            resp = await chat_no_stream_unified(loop_messages, stage_tools)
            message = resp.get("message") or {}
            content = str(message.get("content") or "")
            if content:
                final_text = content
            raw_calls = message.get("tool_calls") or []
            calls_list = normalize_tool_calls_list(raw_calls if isinstance(raw_calls, list) else [])
            if not calls_list:
                _restore_allowed(tool_ctx, prev_allowed)
                return {"text": final_text, "thinking": "", "editChapterSaved": edit_chapter_saved}

            executable_calls = calls_list
            send_chunk({
                "toolCalls": executable_calls,
                "toolCallsInProgress": True,
                "partialContent": "",
                "partialThinking": "",
                "orchestratorInfo": {"stage": stage_name},
                "model": model,
            })

            tool_results = await executor_run_tools(executable_calls, tool_ctx, lambda ev: _forward_tool_events(send_chunk, ev))

            if mark_edit_saved(executable_calls, tool_results):
                edit_chapter_saved = True

            assistant_msg = {
                "role": "assistant",
                "content": final_text,
                "tool_calls": [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}
                    for tc in executable_calls
                ],
            }
            tool_msgs = [{"role": "tool", "tool_call_id": r["tool_call_id"], "content": r["content"]} for r in tool_results]
            loop_messages = loop_messages + [assistant_msg] + tool_msgs

            all_chapter_body = (
                executable_calls
                and all((tc.get("function") or {}).get("name") in CHAPTER_CATALOG_BODY_TOOL_NAMES for tc in executable_calls)
            )
            all_failed = (
                all_chapter_body
                and len(tool_results) == len(executable_calls)
                and all(_tool_result_looks_like_json_error(r.get("content", "")) for r in tool_results)
            )
            if all_failed:
                _restore_allowed(tool_ctx, prev_allowed)
                return {"text": final_text, "thinking": "", "editChapterSaved": edit_chapter_saved}

        _restore_allowed(tool_ctx, prev_allowed)
        return {"text": final_text, "thinking": "", "editChapterSaved": edit_chapter_saved}

    async def run_stage(stage: str, user_content: str) -> dict:
        cfg = SUBAGENT_REGISTRY[stage]
        base_system = next((str(m.get("content") or "") for m in messages if m and m.get("role") == "system"), "").strip()
        stage_system = "\n\n".join(filter(None, [base_system, tooling_appendix, cfg["systemPrompt"]]))
        stage_msgs = [
            {"role": "system", "content": stage_system},
            *[m for m in messages if m and m.get("role") != "system"],
            {"role": "user", "content": user_content},
        ]
        send_chunk({"subagentStage": stage, "subagentStageName": cfg["name"], "subagentStageStarting": True})
        stage_tools = await get_tools_for_stage(stage)
        stage_res = await run_non_stream_tool_loop(stage_msgs, stage_tools, stage)
        send_chunk({"subagentStageDone": stage, "subagentStageName": cfg["name"]})
        parsed = extract_structured_json_from_model_text(stage_res["text"])
        return {"rawText": stage_res["text"], "parsed": parsed, "editChapterSaved": bool(stage_res.get("editChapterSaved"))}

    # -----------------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------------
    latest_user = next((m for m in reversed(messages) if m and m.get("role") == "user"), None)
    user_text = str((latest_user or {}).get("content") or "")

    flags = _normalize_pipeline_flags(agent_actions)
    need_plan = flags["needPlan"]
    need_draft = flags["needDraft"]
    need_style = flags["needStyleUnify"]
    need_review = flags["needReview"]
    need_polish = flags["needPolish"]
    only_draft = flags["onlyDraft"]
    resolve_action = flags["resolveAction"]
    pipeline_label = flags["pipelineActionLabel"]

    # -- ANALYZE --
    analyze_input = f"请输出 AnalyzeReport JSON（仅 JSON 对象，字段：summary, goals, constraints, risks, evidence）。\n用户请求：{user_text}"
    analyze_res = await run_stage(STAGES.ANALYZE, analyze_input)
    analyze_report = normalize_analyze_report(analyze_res["parsed"], user_text)
    send_chunk({"subagentStage": STAGES.ANALYZE, "subagentPayload": analyze_report})

    hint_after_analyze = "即将由主稿专家汇总本轮结果。"
    if need_plan:
        hint_after_analyze = "接下来将进行写作规划。"
    elif need_draft:
        hint_after_analyze = "接下来将处理正文与后续步骤。"
    await _stream_stage_transition(
        send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
        request_params=rp, messages=messages, user_text=user_text,
        completed_stage_name=SUBAGENT_REGISTRY[STAGES.ANALYZE]["name"],
        next_line=hint_after_analyze, model_name=model,
    )

    blueprint: dict | None = None
    draft: dict | None = None
    review_issues: list[dict] = []

    # -- PLAN --
    if need_plan:
        plan_input = "\n".join([
            "请基于 AnalyzeReport 生成 WritingBlueprint JSON（仅 JSON）。",
            "AnalyzeReport:",
            json.dumps(analyze_report, ensure_ascii=False),
            "注意：不要复述原始长素材。",
        ])
        plan_res = await run_stage(STAGES.PLAN, plan_input)
        blueprint = normalize_writing_blueprint(plan_res["parsed"])
        send_chunk({"subagentStage": STAGES.PLAN, "subagentPayload": blueprint})
        hint = "即将由主稿专家汇总。" if not need_draft else "接下来将撰写初稿正文。"
        await _stream_stage_transition(
            send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
            request_params=rp, messages=messages, user_text=user_text,
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.PLAN]["name"],
            next_line=hint, model_name=model,
        )

    # -- DRAFT --
    if need_draft:
        draft_input = "\n".join([
            "请输出 DraftDocument JSON（仅 JSON，字段：title, content, notes）。",
            "输入蓝图:",
            json.dumps(blueprint or {}, ensure_ascii=False),
            "并仅引用当前章节必要素材；content 为完整初稿正文。",
        ])
        draft_res = await run_stage(STAGES.DRAFT, draft_input)
        draft = normalize_draft_document(draft_res["parsed"])
        send_chunk({"subagentStage": STAGES.DRAFT, "subagentPayloadMeta": {"contentLength": len(draft.get("content", ""))}})
        hint = "即将由主稿专家汇总。"
        if need_style:
            hint = "接下来将参照前文 3～5 章统一文风。"
        elif need_review:
            hint = "接下来将审校正文。"
        elif only_draft:
            hint = "接下来由主稿专家为你整理并呈现初稿。"
        await _stream_stage_transition(
            send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
            request_params=rp, messages=messages, user_text=user_text,
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.DRAFT]["name"],
            next_line=hint, model_name=model,
        )

    draft_plain = _resolve_draft_plain_text(draft, user_text, resolve_action)
    style_unify_report: dict | None = None
    style_skip_full_text = False

    # -- STYLE_UNIFY --
    if need_style and str(draft_plain or "").strip():
        prior_hint = _build_style_unify_prior_chapter_hint(tool_ctx)
        style_input = "\n".join([
            "请输出 StyleUnifyResult JSON（仅 JSON），字段：",
            "styleAnchors（string：从前文归纳的人称/时态/节奏/句式与用语习惯，勿大段粘贴原文）、",
            "content（string：对齐文风后的当前章完整正文；须保留初稿剧情与人设，仅做叙述层面统一）、",
            "changeSummary（string：相对「待统一初稿」的修改说明）、",
            "priorChaptersRead（可选 array，每项含 chapterId、title，标明实际参照的章节）。",
            "必须先 listWritingChapters，再按宿主列表中的 chapterId 用 batchGetChapterContents 或 getChapterContent 读取前文；不得跳过读前文直接臆造风格。",
            "若需写回当前章：在输出 JSON 前调用 editChapterContent；成功后 JSON 中 content 可短占位。",
            prior_hint,
            "\n待统一初稿：\n",
            draft_plain,
        ])
        style_res = await run_stage(STAGES.STYLE_UNIFY, style_input)
        style_unify_report = normalize_style_unify_result(style_res["parsed"])
        style_skip_full_text = bool(style_res.get("editChapterSaved"))
        merged = str(style_unify_report.get("content") or "").strip()
        if merged and (not style_skip_full_text or _looks_like_body_text(merged)):
            draft_plain = merged
        send_chunk({
            "subagentStage": STAGES.STYLE_UNIFY,
            "subagentPayloadMeta": {
                "unifiedLength": len(draft_plain or ""),
                "priorCount": len(style_unify_report.get("priorChaptersRead") or []),
            },
        })
        hint = "接下来由主稿专家整合并正式回复你。" if not need_review else "接下来将审校正文。"
        await _stream_stage_transition(
            send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
            request_params=rp, messages=messages, user_text=user_text,
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.STYLE_UNIFY]["name"],
            next_line=hint, model_name=model,
        )

    # -- REVIEW --
    if need_review:
        segments = split_draft_into_segments(draft_plain)
        all_issues: list[dict] = []
        if not segments:
            send_chunk({"toolRouterWarning": "当前没有可审校的文本：请先由写作阶段生成初稿，或在输入框粘贴待审正文（≥40 字）后再选「仅审校/仅润色」。"})
        for seg_i, seg in enumerate(segments):
            review_input = "\n".join([
                "请输出 ReviewIssues JSON 数组（仅 JSON 数组）。每项含 segmentIndex, span, issueType, severity, suggestion, context。",
                f"segmentIndex={seg_i}",
                "segmentText:",
                seg,
            ])
            try:
                review_res = await run_stage(STAGES.REVIEW, review_input)
                issues = normalize_review_issues(review_res["parsed"])
                all_issues.extend({**x, "segmentIndex": seg_i} for x in issues)
            except Exception as exc:
                all_issues.append({
                    "segmentIndex": seg_i,
                    "span": seg[:50],
                    "issueType": "system",
                    "severity": "low",
                    "suggestion": f"该分段审校失败：{exc}",
                    "context": seg[:300],
                })
        review_issues = all_issues
        send_chunk({"subagentStage": STAGES.REVIEW, "subagentPayloadMeta": {"issueCount": len(review_issues)}})
        hint = "接下来由主稿专家汇总审校结果。" if not need_polish else "接下来将进行润色定稿。"
        await _stream_stage_transition(
            send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
            request_params=rp, messages=messages, user_text=user_text,
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.REVIEW]["name"],
            next_line=hint, model_name=model,
        )

    # -- POLISH --
    polish_final_text = ""
    polish_change_summary = ""
    polish_skip_full_text = False
    if need_polish:
        polish_input = "\n".join([
            "请输出 PolishedResult JSON（仅 JSON），字段：finalText、changeSummary。",
            "若需将润色结果保存到左侧当前写作章节：在输出 JSON 之前先调用 editChapterContent，参数 content 为润色后的完整正文。",
            "若已成功调用 editChapterContent，JSON 中 finalText 可填简短占位或空串，changeSummary 仍须简要说明改动。",
            "不要要求全文，仅基于问题点位与上下文窗口修订。",
            "ReviewIssuesWithContext:",
            json.dumps([
                {**x, "context": x.get("context") or extract_context_around_span(draft_plain, x.get("span", ""))}
                for x in review_issues
            ], ensure_ascii=False),
        ])
        polish_res = await run_stage(STAGES.POLISH, polish_input)
        p = polish_res["parsed"] if isinstance(polish_res.get("parsed"), dict) else {}
        polish_skip_full_text = bool(polish_res.get("editChapterSaved"))
        polished_candidate = str(p.get("finalText") or "").strip()
        polish_final_text = (
            polished_candidate
            if polished_candidate and (not polish_skip_full_text or _looks_like_body_text(polished_candidate))
            else str(draft_plain or "")
        )
        polish_change_summary = str(p.get("changeSummary") or "")
        await _stream_stage_transition(
            send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
            request_params=rp, messages=messages, user_text=user_text,
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.POLISH]["name"],
            next_line="接下来由主稿专家整合全文并正式回复你。", model_name=model,
        )

    # -- FINAL WRITE-BACK（有正文且已选章节时才写回；仅分析/规划等无正文场景跳过，继续主稿呈现） --
    final_content = str(polish_final_text or draft_plain or "").strip()
    chapter_id = str((tool_ctx or {}).get("chapterId") or "").strip()
    if final_content and chapter_id:
        final_write_call = {
            "id": f"final_write_{int(time.time() * 1000)}",
            "type": "function",
            "function": {
                "name": "editChapterContent",
                "arguments": json.dumps(
                    {"chapterId": chapter_id, "content": final_content},
                    ensure_ascii=False,
                ),
            },
        }
        final_res = await executor_run_tools([final_write_call], tool_ctx, lambda _: None)
        payload_str = str((final_res[0] if final_res else {}).get("content") or "")
        try:
            ok = json.loads(payload_str).get("success") is True
        except Exception:
            ok = False
        if not ok:
            raise RuntimeError("最终章节写入失败，已中止回复。")
    elif final_content and not chapter_id:
        logger.info("[subagent] skip final write-back: no chapterId")

    # -- PRESENTER --
    await _stream_main_agent_presenter(
        send_chunk=send_chunk, signal=signal, key=key, api_provider=api_provider,
        request_params=rp, messages=messages, user_text=user_text,
        pipeline_action_label=pipeline_label,
        analyze_report=analyze_report, blueprint=blueprint,
        draft_plain=draft_plain, style_unify_report=style_unify_report,
        style_skip_full_text=style_skip_full_text,
        review_issues=review_issues,
        polish_final_text=polish_final_text, polish_change_summary=polish_change_summary,
        polish_skip_full_text=polish_skip_full_text,
        model_name=model,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _restore_allowed(tool_ctx: dict, prev: Any) -> None:
    if prev is None:
        tool_ctx.pop("subagentAllowedToolNames", None)
    else:
        tool_ctx["subagentAllowedToolNames"] = prev


def _forward_tool_events(send_chunk: Callable, ev: dict | None) -> None:
    if not ev:
        return
    if ev.get("chapterCreated") is not None:
        send_chunk({"chapterCreated": ev["chapterCreated"]})
    if ev.get("chapterContentUpdated") is not None:
        send_chunk({"chapterContentUpdated": ev["chapterContentUpdated"]})
    if isinstance(ev.get("toolReadCacheMask"), list):
        send_chunk({"toolReadCacheMask": ev["toolReadCacheMask"]})
    if isinstance(ev.get("toolIndexCompleted"), int):
        chunk: dict = {"toolIndexCompleted": ev["toolIndexCompleted"]}
        if ev.get("toolFromCache") is True:
            chunk["toolFromCache"] = True
        send_chunk(chunk)
