"""
写作专家 — 与 LangGraph / 管线共用的纯函数与流式呈现。

避免 subagent_graph 依赖 subagent_pipeline（工具闭环与 run_stage 仍留在 pipeline）。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Callable

from services.ai_provider import create_chat_stream
from services.subagent_config import BODY_DIALOGUE_QUOTE_RULE, EXEC_ACTIONS
from utils.streaming import text_from_stream_choice0
from utils.writing_chapters import get_writable_chapters_for_agent

# ---------------------------------------------------------------------------
# Draft / text
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


def resolve_draft_plain_text(draft_doc: dict | None, user_text: str, action: str) -> str:
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


def looks_like_body_text(text: str) -> bool:
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


def normalize_pipeline_flags(agent_actions: list[str] | None) -> dict:
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


def build_style_unify_prior_chapter_hint(tool_ctx: dict) -> str:
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
# Presenter / transition prompts
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


async def pipe_model_stream_to_chunks(
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


async def stream_stage_transition(
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
        await pipe_model_stream_to_chunks(
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


async def stream_main_agent_presenter(
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
    await pipe_model_stream_to_chunks(
        send_chunk=send_chunk,
        signal=signal,
        key=key,
        api_provider=api_provider,
        stream_messages=presenter_messages,
        request_params=request_params,
        model_name=model_name,
        emit_done_when_finished=True,
    )
