"""
专家团模式 — Microsoft AutoGen（aut AgentChat）多智能体多轮对话。

与写作专家（LangGraph 顺序管线）隔离：本模块仅在选择「专家团」时启用。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable

from services.subagent_pipeline import _build_subagent_tooling_context_appendix
from utils.url import normalize_base_url

logger = logging.getLogger(__name__)


EXPERT_TEAM_SELECTOR_PROMPT = """\
你是网文「专家团」编排器：从参与者中选出下一位发言者；你输出**唯一的一行**必须是 {participants} 中的某个名称，不要其它字符。

约定：自然中文接力；可争论、补充；不要一人包办全程。
当定稿明确时，由【总编辑】或【主笔】在其发言的**最后一行单独**写下 TERMINATE（全大写）以结束全场讨论。

{roles}

已发生对话：
{history}

请只输出一名下一位发言者名称（从 {participants} 中选）。
"""


def _agent_definitions() -> list[tuple[str, str, str]]:
    """(name, description, system_message) — name 用于对话与选择器。"""
    return [
        (
            "总编辑",
            "对齐目标、读者与红线，拍板方向，可要求他人补充。",
            "你是专家团中的「总编辑」。多轮对话中承接他人发言，用自然中文；"
            "简明决断，指出毒点与优先级。不要编造未讨论的设定。"
            "定稿时若认为可以结束本回合，在消息最后一行单独写 TERMINATE。",
        ),
        (
            "本章策划",
            "本章爽点节拍、钩子、结构衔接；不独揽长正文。",
            "你是「本章策划」。把总编辑的方向落成可写节拍与钩子位置；"
            "可反驳、可修订前提；不写完整章节正文，除非他人明确请你代拟纲要级段落。",
        ),
        (
            "主笔",
            "撰写/修订正文，落实策划节拍。",
            "你是「主笔」。在策划与总编辑共识下写正文草案或改稿；可回应追读顾问与设定审校。"
            "需要收束本章产出时，在最后一行单独写 TERMINATE。",
        ),
        (
            "追读顾问",
            "追读节奏、前三屏与悬念；语气衔接建议。",
            "你是「追读顾问」。从网文追读体验点评：段距、节奏、悬念断点；"
            "可提示与前文语气衔接，不替主笔通篇重写。",
        ),
        (
            "设定审校",
            "设定/时间线/穿帮与可执行修改点。",
            "你是「设定审校」。专注穿帮与高置信问题；批评要具体可改；"
            "避免空泛褒贬。",
        ),
    ]


async def run_expert_team_autogen(
    *,
    send_chunk: Callable[..., Any],
    signal: asyncio.Event | None,
    tool_ctx: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
    api_provider: str,
    key: str,
    request_params: dict[str, Any],
) -> None:
    """
    运行 AutoGen SelectorGroupChat，将每位参与者的回复以 Markdown 块经 delta 推送到前端。

    `api_provider == anthropic` 时使用 Anthropic 官方 API 客户端，否则使用 OpenAI 兼容 Chat Completions。
    """
    try:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.conditions import (
            ExternalTermination,
            MaxMessageTermination,
            TextMentionTermination,
        )
        from autogen_agentchat.messages import TaskResult, TextMessage
        from autogen_agentchat.teams import SelectorGroupChat
    except ImportError as e:
        logger.exception("[expert-team-autogen] missing dependency")
        send_chunk({
            "error": f"未安装 AutoGen 依赖，请在 backend 执行 pip install -r requirements.txt（需包含 autogen-agentchat、autogen-ext）。详情：{e}",
        })
        return

    prov = (api_provider or "").strip().lower()
    if prov == "anthropic":
        try:
            from autogen_ext.models.anthropic import AnthropicChatCompletionClient as _ExpertTeamModelClient
        except ImportError as e:
            logger.exception("[expert-team-autogen] anthropic extra missing")
            send_chunk({
                "error": (
                    "专家团使用 Claude 需安装 autogen-ext 的 anthropic 扩展：在 backend 执行 "
                    f'pip install "autogen-ext[anthropic]>=0.4.9"。详情：{e}'
                ),
            })
            return
    else:
        try:
            from autogen_ext.models.openai import OpenAIChatCompletionClient as _ExpertTeamModelClient
        except ImportError as e:
            logger.exception("[expert-team-autogen] openai extra missing")
            send_chunk({
                "error": (
                    "未安装 OpenAI 兼容模型客户端扩展，请在 backend 执行 "
                    f'pip install "autogen-ext[openai]>=0.4.9"。详情：{e}'
                ),
            })
            return

    latest_user = next(
        (m for m in reversed(messages or []) if m and m.get("role") == "user"),
        None,
    )
    user_text = str((latest_user or {}).get("content") or "").strip()
    if not user_text:
        send_chunk({"error": "缺少用户消息。"})
        return

    base_sys = next(
        (str(m.get("content") or "") for m in (messages or []) if m.get("role") == "system"),
        "",
    ).strip()
    appendix = _build_subagent_tooling_context_appendix(tool_ctx or {})
    task_parts = [
        user_text,
        "",
        "【宿主作品上下文（摘录）】",
        appendix or "（无书籍绑定或目录信息）",
    ]
    if base_sys:
        task_parts.insert(0, f"【系统提示片段】\n{base_sys[:2000]}")
    task_text = "\n".join(task_parts)

    base_url = normalize_base_url(str(request_params.get("baseURL") or ""))
    temperature = request_params.get("temperature")
    max_tokens = request_params.get("max_tokens") or 8192

    client_kwargs: dict[str, Any] = {
        "model": model,
        "api_key": (key or "").strip() or None,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    if isinstance(temperature, (int, float)):
        client_kwargs["temperature"] = float(temperature)
    if isinstance(max_tokens, int) and max_tokens > 0:
        client_kwargs["max_tokens"] = max_tokens

    model_client = _ExpertTeamModelClient(**client_kwargs)

    defs = _agent_definitions()
    participants: list[AssistantAgent] = []
    for name, description, system_message in defs:
        participants.append(
            AssistantAgent(
                name,
                description=description,
                model_client=model_client,
                system_message=system_message,
            ),
        )

    text_term = TextMentionTermination("TERMINATE")
    max_msg_term = MaxMessageTermination(36)
    external_term = ExternalTermination()
    termination = text_term | max_msg_term | external_term

    team = SelectorGroupChat(
        participants,
        model_client=model_client,
        termination_condition=termination,
        selector_prompt=EXPERT_TEAM_SELECTOR_PROMPT,
        allow_repeated_speaker=True,
    )

    async def _watch_abort() -> None:
        try:
            while True:
                if signal and signal.is_set():
                    external_term.set()
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            return

    abort_watcher = asyncio.create_task(_watch_abort()) if signal else None
    try:
        async for message in team.run_stream(task=task_text):
            if signal and signal.is_set():
                break
            if isinstance(message, TaskResult):
                logger.info("[expert-team-autogen] stop_reason=%s", message.stop_reason)
                break
            if isinstance(message, TextMessage):
                if (message.source or "").strip().lower() == "user":
                    continue
                body = str(message.content or "").strip()
                body = re.sub(r"\s*TERMINATE\s*$", "", body, flags=re.IGNORECASE).strip()
                if not body:
                    continue
                src = str(message.source or "参与者").strip()
                block = f"\n\n---\n\n**{src}**\n\n{body}"
                send_chunk({"delta": block})
        if not (signal and signal.is_set()):
            send_chunk({"done": True, "model": model})
    except asyncio.CancelledError:
        logger.info("[expert-team-autogen] cancelled")
        raise
    except Exception as e:
        logger.exception("[expert-team-autogen] run failed")
        send_chunk({"error": str(e)})
    finally:
        if abort_watcher:
            abort_watcher.cancel()
            try:
                await abort_watcher
            except asyncio.CancelledError:
                pass
        try:
            await model_client.close()
        except Exception:
            pass
