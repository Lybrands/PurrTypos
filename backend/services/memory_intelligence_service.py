"""Optional LLM-powered extraction for long-term memory candidates.

This service is deliberately gated by ``memory_intelligence_enabled``. When the
switch is off or the model config is incomplete, callers get an empty result and
can continue with the deterministic local deposition path.
"""

from __future__ import annotations

import json
import re
from typing import Any

from services.ai_provider import create_chat_no_stream
from services import long_term_memory_service

MEMORY_INTELLIGENCE_ENABLED_KEY = "memory_intelligence_enabled"
MEMORY_INTELLIGENCE_MODEL_ID_KEY = "memory_intelligence_model_id"

VALID_KINDS = {"canon", "plot", "character", "world", "foreshadowing", "style", "summary"}
VALID_RELATIONS = {"supersedes", "contradicts", "supports", "relates_to"}

SYSTEM_PROMPT = """你是小说创作软件的长期记忆提炼器。
从用户已经接受的改动中提炼“值得长期记住”的事实、设定、人物变化、世界观、风格约束、伏笔或大纲摘要。

要求：
1. 只输出 JSON，不要输出 Markdown。
2. 不要把普通润色、重复描述、临时措辞写入记忆。
3. AI 改动产生的内容都应作为候选，由用户后续确认，所以 status 固定由系统设为 pending。
4. 如果发现新候选与既有记忆冲突、替代或支持，可在 relation 中引用既有记忆 id。

JSON 格式：
{
  "memories": [
    {
      "kind": "canon|plot|character|world|foreshadowing|style|summary",
      "content": "完整、可独立理解的记忆内容",
      "summary": "可选短摘要",
      "keywords": "空格分隔关键词",
      "importance": 1-5,
      "confidence": 0-1,
      "relation": {
        "targetId": 123,
        "type": "supersedes|contradicts|supports|relates_to",
        "note": "可选说明"
      }
    }
  ]
}
"""


async def is_enabled(db: Any) -> bool:
    return _coerce_bool(await _get_setting(db, MEMORY_INTELLIGENCE_ENABLED_KEY), False)


async def extract_and_store_candidates(
    db: Any,
    *,
    book_id: str,
    source_type: str,
    source_id: str | int | None,
    source_text: str,
    scope_type: str = "book",
    scope_id: str | int | None = None,
    default_kind: str = "plot",
    source_title: str = "",
) -> list[dict]:
    """Extract pending memory candidates with the configured LLM.

    Returns an empty list when the feature is disabled, unavailable, or the model
    output does not contain usable candidates. Callers should treat that as a
    normal fallback condition.
    """

    clean_book_id = str(book_id or "").strip()
    clean_text = str(source_text or "").strip()
    if not clean_book_id or not clean_text:
        return []
    if not await is_enabled(db):
        return []

    config = await _get_model_config(db)
    if not config:
        return []

    existing = await _nearby_existing_memories(clean_book_id, clean_text)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_user_prompt(
                source_type=source_type,
                source_title=source_title,
                scope_type=scope_type,
                source_text=clean_text,
                existing=existing,
            ),
        },
    ]
    options = {
        "model": config["name"],
        "baseURL": config.get("baseUrl") or config.get("baseURL") or "",
        "temperature": 0,
        "max_tokens": 1200,
        "reasoning": "off",
    }

    try:
        result = await create_chat_no_stream(
            str(config.get("apiKey") or ""),
            messages,
            options,
            str(config.get("apiProvider") or "openai"),
        )
    except Exception:
        return []

    payload = _parse_json_payload(((result.get("message") or {}).get("content") or ""))
    raw_memories = payload.get("memories") if isinstance(payload, dict) else None
    if not isinstance(raw_memories, list):
        return []

    created: list[dict] = []
    for raw in raw_memories[:8]:
        candidate = _normalize_candidate(raw, default_kind=default_kind)
        if not candidate:
            continue
        try:
            item = await long_term_memory_service.create_memory_item(
                book_id=clean_book_id,
                kind=candidate["kind"],
                content=candidate["content"],
                summary=candidate.get("summary", ""),
                keywords=candidate.get("keywords", ""),
                scope_type=scope_type or "book",
                scope_id=str(scope_id) if scope_id is not None else None,
                importance=candidate["importance"],
                confidence=candidate["confidence"],
                status="pending",
                source_type=f"ai_intelligence:{source_type}",
                source_id=source_id,
            )
        except Exception:
            continue
        relation = candidate.get("relation")
        if relation:
            await _link_candidate(clean_book_id, item["id"], relation)
        created.append(item)
    return created


async def _get_model_config(db: Any) -> dict | None:
    raw_configs = await _get_setting(db, "ai_model_configs")
    configs = raw_configs if isinstance(raw_configs, list) else []
    model_id = str(await _get_setting(db, MEMORY_INTELLIGENCE_MODEL_ID_KEY) or "").strip()
    selected = None
    if model_id:
        selected = next((c for c in configs if str(c.get("id") or "") == model_id), None)
    if not selected:
        selected = next((c for c in configs if c.get("apiKey") and c.get("name")), None)
    if not selected:
        return None
    if not str(selected.get("apiKey") or "").strip() or not str(selected.get("name") or "").strip():
        return None
    return selected


async def _get_setting(db: Any, key: str) -> Any:
    row = await db.fetch_one("SELECT value FROM settings WHERE key = ?", [key])
    if not row:
        return None
    raw = row["value"]
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


async def _nearby_existing_memories(book_id: str, source_text: str) -> list[dict]:
    query = " ".join(_keywords(source_text))
    try:
        return await long_term_memory_service.search_memory_items(
            book_id,
            query,
            {"statuses": ["active", "pending"], "limit": 8},
        )
    except Exception:
        return []


def _build_user_prompt(
    *,
    source_type: str,
    source_title: str,
    scope_type: str,
    source_text: str,
    existing: list[dict],
) -> str:
    existing_lines = []
    for item in existing[:8]:
        existing_lines.append(
            f"- id={item.get('id')} kind={item.get('kind')} status={item.get('status')} "
            f"content={str(item.get('content') or '')[:180]}"
        )
    existing_block = "\n".join(existing_lines) or "无"
    title = f"\n标题/对象：{source_title}" if source_title else ""
    return (
        f"来源类型：{source_type}{title}\n"
        f"作用域：{scope_type}\n\n"
        f"既有相关记忆：\n{existing_block}\n\n"
        f"已接受的新内容：\n{source_text[:6000]}"
    )


def _parse_json_payload(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_candidate(raw: Any, *, default_kind: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    content = " ".join(str(raw.get("content") or "").split()).strip()
    if len(content) < 6:
        return None
    kind = str(raw.get("kind") or default_kind or "plot").strip()
    if kind not in VALID_KINDS:
        kind = default_kind if default_kind in VALID_KINDS else "plot"
    relation = _normalize_relation(raw.get("relation"))
    return {
        "kind": kind,
        "content": content[:900],
        "summary": " ".join(str(raw.get("summary") or "").split()).strip()[:180],
        "keywords": " ".join(str(raw.get("keywords") or "").split()).strip()[:180],
        "importance": _clamp_int(raw.get("importance"), 3, 1, 5),
        "confidence": _clamp_float(raw.get("confidence"), 0.75, 0.0, 1.0),
        "relation": relation,
    }


def _normalize_relation(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    relation = str(raw.get("type") or raw.get("relation") or "").strip()
    target_id = raw.get("targetId") or raw.get("target_id")
    if relation not in VALID_RELATIONS or target_id in (None, ""):
        return None
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return None
    return {
        "target_id": target_id,
        "relation": relation,
        "note": " ".join(str(raw.get("note") or "").split()).strip()[:240],
    }


async def _link_candidate(book_id: str, item_id: int, relation: dict) -> None:
    try:
        target = await long_term_memory_service.get_memory_items_by_ids([relation["target_id"]])
        if not target or str(target[0].get("book_id") or "") != str(book_id):
            return
        await long_term_memory_service.link_memory_items(
            book_id=book_id,
            from_memory_id=item_id,
            to_memory_id=relation["target_id"],
            relation=relation["relation"],
            note=relation.get("note", ""),
        )
    except Exception:
        pass


def _keywords(text: str) -> list[str]:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", value)
    result: list[str] = []
    for word in words:
        if word not in result:
            result.append(word[:16])
    return result[:6]


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "on"}:
            return True
        if s in {"false", "0", "no", "off", ""}:
            return False
    return default


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))
