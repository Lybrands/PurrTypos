"""
Agent tool definitions — port of electron/agentToolDefinitions.js.

SKILL_SPECS (DAG / orchestration metadata) and format conversion to
OpenAI-compatible tools arrays.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Skill specs (DAG dependencies, read/write risk)
# ---------------------------------------------------------------------------

SKILL_SPECS: dict[str, dict[str, Any]] = {
    "getGlobalOutline": {
        "riskLevel": "read",
        "provides": ["globalOutlineMarkdown"],
        "consumes": ["bookId"],
    },
    "editGlobalOutline": {
        "riskLevel": "write",
        "requires": ["getGlobalOutline"],
        "consumes": ["bookId", "markdownContent"],
        "autoResolveArgs": ["bookId"],
    },
    "listOutlines": {
        "riskLevel": "read",
        "provides": ["outlinesIndex"],
        "consumes": ["bookId"],
    },
    "queryOutline": {
        "riskLevel": "read",
        "requires": ["listOutlines"],
        "provides": ["outlineDetails"],
        "consumes": ["bookId"],
        "autoResolveArgs": ["bookId", "outlineIds"],
    },
    "updateOutline": {
        "riskLevel": "write",
        "requires": ["listOutlines", "queryOutline"],
        "consumes": ["bookId", "outlineId"],
        "autoResolveArgs": ["bookId", "outlineId"],
    },
    "listWritingChapters": {
        "riskLevel": "read",
        "provides": ["writingChaptersIndex"],
        "consumes": ["bookId"],
    },
    "createWritingChapter": {
        "riskLevel": "write",
        "requires": ["listWritingChapters"],
        "consumes": ["bookId"],
        "autoResolveArgs": ["bookId"],
    },
    "getChapterContent": {
        "riskLevel": "read",
        "requires": ["listWritingChapters"],
        "consumes": ["bookId", "chapterId"],
    },
    "batchGetChapterContents": {
        "riskLevel": "read",
        "requires": ["listWritingChapters"],
        "consumes": ["chapterIds"],
    },
    "editChapterContent": {
        "riskLevel": "write",
        "requires": ["listWritingChapters"],
        "consumes": ["chapterId", "content"],
        "autoResolveArgs": ["chapterId"],
    },
    "addForeshadowing": {
        "riskLevel": "write",
        "requires": ["listWritingChapters"],
        "consumes": ["bookId", "chapterId", "content"],
    },
    "listBookCharacters": {
        "riskLevel": "read",
        "provides": ["bookCharactersIndex"],
        "consumes": ["bookId"],
    },
    "getBookCharacters": {
        "riskLevel": "read",
        "requires": ["listBookCharacters"],
        "provides": ["bookCharactersDetails"],
        "consumes": ["bookId"],
    },
    "createCharacter": {
        "riskLevel": "write",
        "requires": ["listBookCharacters"],
        "consumes": ["bookId", "name"],
        "autoResolveArgs": ["bookId"],
    },
    "updateCharacter": {
        "riskLevel": "write",
        "requires": ["listBookCharacters"],
        "consumes": ["bookId", "characterId"],
        "autoResolveArgs": ["bookId"],
    },
    "editStoryBackground": {
        "riskLevel": "write",
        "requires": ["getStoryBackground"],
        "consumes": ["bookId", "content"],
        "autoResolveArgs": ["bookId"],
    },
}


# ---------------------------------------------------------------------------
# OpenAI-compatible tools conversion
# ---------------------------------------------------------------------------

def to_openai_tools(items: list[dict]) -> list[dict]:
    """Convert skill items (from tool_router) into OpenAI function-call tool array."""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": s.get("parameters", {"type": "object", "properties": {}, "required": []}),
            },
        }
        for s in items
    ]


def get_skill_specs() -> dict[str, dict[str, Any]]:
    """Return skill specs aligned with the currently loaded tool names.

    Must be called after ``tool_router.set_skills_path()`` has been invoked.
    """
    from services.tool_router import get_api_skill_items

    items = get_api_skill_items()
    out: dict[str, dict] = {}
    for item in items:
        raw = SKILL_SPECS.get(item["name"], {})
        out[item["name"]] = {
            "requires": list(raw.get("requires", [])),
            "provides": list(raw.get("provides", [])),
            "consumes": list(raw.get("consumes", [])),
            "riskLevel": "write" if raw.get("riskLevel") == "write" else "read",
            "autoResolveArgs": list(raw.get("autoResolveArgs", [])),
        }
    return out
