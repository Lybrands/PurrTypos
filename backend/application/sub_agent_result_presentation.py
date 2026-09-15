"""Project private Child results into readable, protocol-free conversation text."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence


_PREFERRED_TEXT_KEYS = (
    "summaryMarkdown",
    "answer",
    "message",
    "summary",
    "analysis",
    "bodyMarkdown",
    "content",
    "text",
    "profile_md",
    "finding",
)


def present_sub_agent_result(value: object) -> str:
    content = str(value or "").strip()
    if not content:
        return ""
    candidate = _structured_candidate(content)
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        if _looks_structured(candidate):
            summary = _extract_json_string(candidate, "summaryMarkdown")
            return summary or "该次结构化结果未能完整解析，主 Agent 将重新执行此步骤。"
        return content
    projected = _project(payload)
    return projected or "已完成处理，结果已交付主 Agent。"


def _project(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        sections: list[str] = []
        primary = next((
            value.get(key).strip()
            for key in _PREFERRED_TEXT_KEYS
            if isinstance(value.get(key), str) and value.get(key).strip()
        ), "")
        if primary:
            sections.append(primary)
        for key, title in (
            ("findings", "分析结果"),
            ("conflicts", "需核对内容"),
            ("facts", "创作资料"),
            ("craftCards", "写作技法"),
            ("techniques", "写作技法"),
        ):
            items = value.get(key)
            rendered = _project_items(items, title_key=("subjectKey" if key == "facts" else None))
            if rendered:
                sections.append(f"## {title}\n\n{rendered}")
        if sections:
            return "\n\n".join(dict.fromkeys(sections))
        texts = [
            item.strip()
            for item in value.values()
            if isinstance(item, str) and item.strip()
        ]
        return "\n\n".join(dict.fromkeys(texts))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _project_items(value)
    return ""


def _project_items(value: object, *, title_key: str | None = None) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ""
    rendered = []
    for item in value:
        if isinstance(item, Mapping):
            title = next((
                str(item.get(key) or "").strip()
                for key in tuple(filter(None, (title_key, "subject", "title", "name")))
                if str(item.get(key) or "").strip()
            ), "")
            body = _project(item.get("value")) if title_key else _project(item)
            if not body:
                continue
            rendered.append(f"### {title}\n\n{body}" if title and title != body else body)
            continue
        body = _project(item)
        if body:
            rendered.append(body)
    return "\n\n".join(rendered)


def _looks_structured(value: str) -> bool:
    return value.lstrip().startswith(("{", "["))


def _structured_candidate(value: str) -> str:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", value, re.IGNORECASE)
    if fenced is not None:
        return fenced.group(1).strip()
    return value.strip()


def _extract_json_string(value: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*', value)
    if match is None:
        return ""
    try:
        decoded, _ = json.JSONDecoder().raw_decode(value[match.end():])
    except (TypeError, ValueError):
        return ""
    return decoded.strip() if isinstance(decoded, str) else ""


__all__ = ["present_sub_agent_result"]
