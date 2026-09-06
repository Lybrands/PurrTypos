"""Shared public-conversation input selection; domains own material selection."""
from collections.abc import Sequence
from purra.contracts import AgentMessage, MessageRole
from purra.context_budget import estimate_json_tokens


def conversation_messages(messages: Sequence[AgentMessage], *, context_window: int) -> tuple[AgentMessage, ...]:
    """Keep the current user message and a bounded suffix of complete public turns.

    Client history cannot supply system instructions, tools or execution evidence.
    The Core retains ownership of its live tool conversation and final budgeting.
    """
    current_index = next((i for i in range(len(messages) - 1, -1, -1)
                          if messages[i].role == MessageRole.USER), None)
    if current_index is None:
        raise ValueError('conversation requires a current user message')
    current = messages[current_index]
    pairs = []
    pending = None
    for message in messages[:current_index]:
        if message.role == MessageRole.USER:
            pending = message
        elif message.role == MessageRole.ASSISTANT and pending is not None and message.content and not message.tool_calls:
            pairs.append((pending, message))
            pending = None
        else:
            pending = None
    remaining = max(0, min(8192, context_window // 8) - estimate_json_tokens(current.content))
    selected = []
    for pair in reversed(pairs[-32:]):
        cost = sum(estimate_json_tokens(message.content) for message in pair)
        if cost > remaining:
            break
        selected.append(pair)
        remaining -= cost
    chosen = (*tuple(message for pair in reversed(selected) for message in pair), current)
    return tuple(AgentMessage(role=message.role, content=message.content,
        host_metadata={"inputSource": "current_user" if index == len(chosen) - 1 else "public_history",
                       "instructionTrust": "conversation"})
        for index, message in enumerate(chosen))


def conversation_input_metadata(*, source: str, scope: str) -> dict:
    return {'conversationInput': {'version': 1, 'source': source, 'scope': scope,
            'trust': 'conversation', 'historyPolicy': 'complete_public_turns',
            'historyMaxTokens': 8192}}
