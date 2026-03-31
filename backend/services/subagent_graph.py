"""
写作专家管线 — LangGraph 编排。

节点内仍调用原有的 run_stage / send_chunk / Presenter，不改变前端协议与工具执行。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from services.subagent_config import (
    STAGES,
    SUBAGENT_REGISTRY,
    extract_structured_json_from_model_text,
    normalize_analyze_report,
    normalize_draft_document,
    normalize_review_issues,
    normalize_style_unify_result,
    normalize_writing_blueprint,
)
from services.subagent_stage_digest import (
    format_analyze_digest,
    format_draft_thoughts_digest,
    format_plan_digest,
    format_review_digest,
)
from services.subagent_shared import (
    build_style_unify_prior_chapter_hint,
    extract_context_around_span,
    looks_like_body_text,
    resolve_draft_plain_text,
    split_draft_into_segments,
    stream_main_agent_presenter,
    stream_stage_transition,
)
from services.tool_executor import run_tools as executor_run_tools

logger = logging.getLogger(__name__)


class SubagentGraphState(TypedDict, total=False):
    user_text: str
    analyze_report: dict
    blueprint: dict | None
    draft: dict | None
    draft_plain: str
    style_unify_report: dict | None
    style_skip_full_text: bool
    review_issues: list[dict]
    polish_final_text: str
    polish_change_summary: str
    polish_skip_full_text: bool


async def run_writing_expert_langgraph(
    *,
    user_text: str,
    flags: dict[str, Any],
    pipeline_label: str,
    run_stage: Callable[..., Any],
    send_chunk: Callable[..., Any],
    signal: Any,
    key: str,
    api_provider: str,
    rp: dict,
    messages: list[dict],
    tool_ctx: dict,
    model: str,
) -> None:
    need_plan: bool = bool(flags.get("needPlan"))
    need_draft: bool = bool(flags.get("needDraft"))
    need_style: bool = bool(flags.get("needStyleUnify"))
    need_review: bool = bool(flags.get("needReview"))
    need_polish: bool = bool(flags.get("needPolish"))
    only_draft: bool = bool(flags.get("onlyDraft"))
    resolve_action: str = str(flags.get("resolveAction") or "")

    async def node_analyze(state: SubagentGraphState) -> dict[str, Any]:
        if signal and signal.is_set():
            return {}
        analyze_input = (
            "请输出 AnalyzeReport JSON（仅 JSON 对象，字段：summary, goals, constraints, risks, evidence）。\n"
            f"用户请求：{state['user_text']}"
        )
        analyze_res = await run_stage(STAGES.ANALYZE, analyze_input)
        analyze_report = normalize_analyze_report(analyze_res["parsed"], state["user_text"])
        send_chunk({"subagentStage": STAGES.ANALYZE, "subagentPayload": analyze_report})
        send_chunk({"subagentPipelineDigest": format_analyze_digest(analyze_report)})
        hint_after = "即将由主稿专家汇总本轮结果。"
        if need_plan:
            hint_after = "接下来将进行写作规划。"
        elif need_draft:
            hint_after = "接下来将处理正文与后续步骤。"
        await stream_stage_transition(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.ANALYZE]["name"],
            next_line=hint_after,
            model_name=model,
        )
        return {"analyze_report": analyze_report}

    async def node_plan(state: SubagentGraphState) -> dict[str, Any]:
        if signal and signal.is_set():
            return {}
        ar = state.get("analyze_report") or {}
        plan_input = "\n".join([
            "请基于 AnalyzeReport 生成 WritingBlueprint JSON（仅 JSON）。",
            "AnalyzeReport:",
            json.dumps(ar, ensure_ascii=False),
            "注意：不要复述原始长素材。",
        ])
        plan_res = await run_stage(STAGES.PLAN, plan_input)
        blueprint = normalize_writing_blueprint(plan_res["parsed"])
        send_chunk({"subagentStage": STAGES.PLAN, "subagentPayload": blueprint})
        send_chunk({"subagentPipelineDigest": format_plan_digest(blueprint)})
        hint = "即将由主稿专家汇总。" if not need_draft else "接下来将撰写初稿正文。"
        await stream_stage_transition(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.PLAN]["name"],
            next_line=hint,
            model_name=model,
        )
        return {"blueprint": blueprint}

    async def node_draft(state: SubagentGraphState) -> dict[str, Any]:
        if signal and signal.is_set():
            return {}
        blueprint = state.get("blueprint")
        draft_input = "\n".join([
            "请输出 DraftDocument JSON（仅 JSON，字段：title, content, notes）。",
            "输入蓝图:",
            json.dumps(blueprint or {}, ensure_ascii=False),
            "并仅引用当前章节必要素材；content 为完整初稿正文。",
        ])
        draft_res = await run_stage(STAGES.DRAFT, draft_input)
        draft = normalize_draft_document(draft_res["parsed"])
        send_chunk({
            "subagentStage": STAGES.DRAFT,
            "subagentPayloadMeta": {"contentLength": len(draft.get("content", ""))},
        })
        send_chunk({"subagentPipelineDigest": format_draft_thoughts_digest(draft)})
        hint = "即将由主稿专家汇总。"
        if need_style:
            hint = "接下来将参照前文 3～5 章统一文风。"
        elif need_review:
            hint = "接下来将审校正文。"
        elif only_draft:
            hint = "接下来由主稿专家为你整理并呈现初稿。"
        await stream_stage_transition(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.DRAFT]["name"],
            next_line=hint,
            model_name=model,
        )
        return {"draft": draft}

    async def node_resolve_draft(state: SubagentGraphState) -> dict[str, Any]:
        draft = state.get("draft")
        plain = resolve_draft_plain_text(
            draft if isinstance(draft, dict) else None,
            state["user_text"],
            resolve_action,
        )
        return {"draft_plain": plain}

    async def node_style_unify(state: SubagentGraphState) -> dict[str, Any]:
        if signal and signal.is_set():
            return {}
        draft_plain = str(state.get("draft_plain") or "")
        prior_hint = build_style_unify_prior_chapter_hint(tool_ctx)
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
        new_plain = draft_plain
        if merged and (not style_skip_full_text or looks_like_body_text(merged)):
            new_plain = merged
        send_chunk({
            "subagentStage": STAGES.STYLE_UNIFY,
            "subagentPayloadMeta": {
                "unifiedLength": len(new_plain or ""),
                "priorCount": len(style_unify_report.get("priorChaptersRead") or []),
            },
        })
        hint = "接下来由主稿专家整合并正式回复你。" if not need_review else "接下来将审校正文。"
        await stream_stage_transition(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.STYLE_UNIFY]["name"],
            next_line=hint,
            model_name=model,
        )
        return {
            "draft_plain": new_plain,
            "style_unify_report": style_unify_report,
            "style_skip_full_text": style_skip_full_text,
        }

    async def node_review(state: SubagentGraphState) -> dict[str, Any]:
        if signal and signal.is_set():
            return {}
        draft_plain = str(state.get("draft_plain") or "")
        segments = split_draft_into_segments(draft_plain)
        all_issues: list[dict] = []
        if not segments:
            send_chunk({
                "toolRouterWarning": "当前没有可审校的文本：请先由写作阶段生成初稿，或在输入框粘贴待审正文（≥40 字）后再选「仅审校/仅润色」。",
            })
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
        send_chunk({"subagentStage": STAGES.REVIEW, "subagentPayloadMeta": {"issueCount": len(all_issues)}})
        send_chunk({"subagentPipelineDigest": format_review_digest(all_issues)})
        hint = "接下来由主稿专家汇总审校结果。" if not need_polish else "接下来将进行润色定稿。"
        await stream_stage_transition(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.REVIEW]["name"],
            next_line=hint,
            model_name=model,
        )
        return {"review_issues": all_issues}

    async def node_polish(state: SubagentGraphState) -> dict[str, Any]:
        if signal and signal.is_set():
            return {}
        draft_plain = str(state.get("draft_plain") or "")
        review_issues = state.get("review_issues") or []
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
            if polished_candidate and (not polish_skip_full_text or looks_like_body_text(polished_candidate))
            else str(draft_plain or "")
        )
        polish_change_summary = str(p.get("changeSummary") or "")
        await stream_stage_transition(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            completed_stage_name=SUBAGENT_REGISTRY[STAGES.POLISH]["name"],
            next_line="接下来由主稿专家整合全文并正式回复你。",
            model_name=model,
        )
        return {
            "polish_final_text": polish_final_text,
            "polish_change_summary": polish_change_summary,
            "polish_skip_full_text": polish_skip_full_text,
        }

    async def node_final_writeback(state: SubagentGraphState) -> dict[str, Any]:
        polish_final = str(state.get("polish_final_text") or "")
        draft_plain = str(state.get("draft_plain") or "")
        final_content = str(polish_final or draft_plain or "").strip()
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
        return {}

    async def node_presenter(state: SubagentGraphState) -> dict[str, Any]:
        analyze_report = state.get("analyze_report") or {}
        blueprint = state.get("blueprint")
        draft_plain = str(state.get("draft_plain") or "")
        style_unify_report = state.get("style_unify_report")
        style_skip_full_text = bool(state.get("style_skip_full_text"))
        review_issues = state.get("review_issues") or []
        polish_final_text = str(state.get("polish_final_text") or "")
        polish_change_summary = str(state.get("polish_change_summary") or "")
        polish_skip_full_text = bool(state.get("polish_skip_full_text"))
        await stream_main_agent_presenter(
            send_chunk=send_chunk,
            signal=signal,
            key=key,
            api_provider=api_provider,
            request_params=rp,
            messages=messages,
            user_text=state["user_text"],
            pipeline_action_label=pipeline_label,
            analyze_report=analyze_report if isinstance(analyze_report, dict) else {},
            blueprint=blueprint if isinstance(blueprint, dict) else None,
            draft_plain=draft_plain,
            style_unify_report=style_unify_report if isinstance(style_unify_report, dict) else None,
            style_skip_full_text=style_skip_full_text,
            review_issues=list(review_issues) if isinstance(review_issues, list) else [],
            polish_final_text=polish_final_text,
            polish_change_summary=polish_change_summary,
            polish_skip_full_text=polish_skip_full_text,
            model_name=model,
        )
        return {}

    def route_after_analyze(_: SubagentGraphState) -> str:
        if need_plan:
            return "plan"
        if need_draft:
            return "draft"
        return "resolve_draft"

    def route_after_plan(_: SubagentGraphState) -> str:
        if need_draft:
            return "draft"
        return "resolve_draft"

    def route_after_resolve(s: SubagentGraphState) -> str:
        if need_style and str(s.get("draft_plain") or "").strip():
            return "style_unify"
        if need_review:
            return "review"
        if need_polish:
            return "polish"
        return "final_writeback"

    def route_after_style(_: SubagentGraphState) -> str:
        if need_review:
            return "review"
        if need_polish:
            return "polish"
        return "final_writeback"

    def route_after_review(_: SubagentGraphState) -> str:
        if need_polish:
            return "polish"
        return "final_writeback"

    graph = StateGraph(SubagentGraphState)
    graph.add_node("analyze", node_analyze)
    graph.add_node("plan", node_plan)
    graph.add_node("draft", node_draft)
    graph.add_node("resolve_draft", node_resolve_draft)
    graph.add_node("style_unify", node_style_unify)
    graph.add_node("review", node_review)
    graph.add_node("polish", node_polish)
    graph.add_node("final_writeback", node_final_writeback)
    graph.add_node("presenter", node_presenter)

    graph.add_edge(START, "analyze")
    graph.add_conditional_edges(
        "analyze",
        route_after_analyze,
        {"plan": "plan", "draft": "draft", "resolve_draft": "resolve_draft"},
    )
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {"draft": "draft", "resolve_draft": "resolve_draft"},
    )
    graph.add_edge("draft", "resolve_draft")
    graph.add_conditional_edges(
        "resolve_draft",
        route_after_resolve,
        {
            "style_unify": "style_unify",
            "review": "review",
            "polish": "polish",
            "final_writeback": "final_writeback",
        },
    )
    graph.add_conditional_edges(
        "style_unify",
        route_after_style,
        {"review": "review", "polish": "polish", "final_writeback": "final_writeback"},
    )
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {"polish": "polish", "final_writeback": "final_writeback"},
    )
    graph.add_edge("polish", "final_writeback")
    graph.add_edge("final_writeback", "presenter")
    graph.add_edge("presenter", END)

    app = graph.compile()
    initial: SubagentGraphState = {
        "user_text": user_text,
        "blueprint": None,
        "draft": None,
        "review_issues": [],
        "polish_final_text": "",
        "polish_change_summary": "",
        "polish_skip_full_text": False,
        "style_skip_full_text": False,
    }
    await app.ainvoke(initial)
