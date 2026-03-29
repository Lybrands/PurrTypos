"""
Tool router — port of electron/toolRouter.js.

Scans ``skills/<name>/SKILL.md`` directories, parses frontmatter +
JSON schema, and provides embedding-based Top-K retrieval with
optional LLM intent classification via Ollama.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ROUTER_TOP_K = 10
ROUTER_BODY_PREVIEW_MAX = 400

_ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
_intent_model = os.environ.get("TOOL_ROUTER_INTENT_MODEL", "qwen2.5:3b")
_use_intent = os.environ.get("TOOL_ROUTER_USE_LLM_INTENT", "1") != "0"

GLOBAL_OUTLINE_READ_TOOL = "getGlobalOutline"
GLOBAL_OUTLINE_EDIT_TOOL = "editGlobalOutline"
OUTLINE_LIST_TOOL = "listOutlines"
OUTLINE_QUERY_TOOL = "queryOutline"
OUTLINE_UPDATE_TOOL = "updateOutline"

FRONTMATTER_ALLOWED = {"name", "description", "short_description"}

# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------

_cached_skills: list[dict] = []
_router_hints: dict[str, str] = {}
_skills_dir_path: str = ""
_tool_vectors_cache: dict[str, list[float]] = {}


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

async def _get_embedding(text: str) -> list[float]:
    """Get embedding vector for a text string via configured embedding service."""
    t = (text or "").strip()
    if not t:
        return []
    try:
        import httpx

        has_ollama = os.environ.get("MEM0_USE_OLLAMA", "1") != "0"
        if has_ollama:
            url = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
            model = os.environ.get("MEM0_EMBED_MODEL", "nomic-embed-text")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{url}/api/embeddings",
                    json={"model": model, "prompt": t},
                )
                resp.raise_for_status()
                return resp.json().get("embedding", [])

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            model = os.environ.get("MEM0_EMBED_MODEL", "text-embedding-3-small")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url}/embeddings",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "input": t},
                )
                resp.raise_for_status()
                data = resp.json()
                emb = (data.get("data") or [{}])[0].get("embedding", [])
                return emb

        raise RuntimeError("工具路由需要 Embedder：请安装并启动 Ollama（推荐），或设置 OPENAI_API_KEY。")
    except Exception as exc:
        logger.warning("[toolRouter] embedding failed: %s", exc)
        return []


def _embed_service_label() -> str:
    has_ollama = os.environ.get("MEM0_USE_OLLAMA", "1") != "0"
    model = os.environ.get("MEM0_EMBED_MODEL", "nomic-embed-text")
    if has_ollama:
        return f"Ollama {model}"
    if os.environ.get("OPENAI_API_KEY"):
        m = os.environ.get("MEM0_EMBED_MODEL", "text-embedding-3-small")
        return f"OpenAI {m}"
    return "嵌入服务"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _preview_str(s: str, max_len: int = 200) -> str:
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    if not t:
        return "（空）"
    return t if len(t) <= max_len else t[:max_len] + "…"


def _plain_body_preview(md: str, max_len: int) -> str:
    if not md or not str(md).strip():
        return ""
    t = re.sub(r"\s+", " ", str(md)).strip()
    return t if len(t) <= max_len else t[:max_len] + "…"


def _is_likely_parameters_schema(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("type") == "object" and isinstance(obj.get("properties"), dict):
        return True
    if isinstance(obj.get("properties"), dict):
        return True
    return False


def _extract_parameters_from_body(content: str) -> dict | None:
    src = str(content or "")
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", src, re.IGNORECASE):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            if _is_likely_parameters_schema(obj):
                params = json.loads(json.dumps(obj))
                if "type" not in params:
                    params["type"] = "object"
                return params
        except Exception:
            continue
    return None


def _body_text_for_embedding(description: str, content: str) -> str:
    stripped = re.sub(r"```(?:json)?\s*[\s\S]*?```", " ", str(content or ""), flags=re.IGNORECASE)
    preview = _plain_body_preview(stripped, ROUTER_BODY_PREVIEW_MAX)
    if not preview:
        return str(description or "").strip()
    return f"{str(description or '').strip()} {preview}".strip()


def _parse_frontmatter(raw_text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (data, content)."""
    raw_text = raw_text or ""
    if not raw_text.startswith("---"):
        return {}, raw_text
    end = raw_text.find("---", 3)
    if end < 0:
        return {}, raw_text
    fm_text = raw_text[3:end]
    content = raw_text[end + 3:].lstrip("\n")
    try:
        data = yaml.safe_load(fm_text)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return data, content


# ---------------------------------------------------------------------------
# Skills loading
# ---------------------------------------------------------------------------

def set_skills_path(dir_path: str) -> None:
    global _cached_skills, _router_hints, _skills_dir_path, _tool_vectors_cache
    _skills_dir_path = dir_path or ""
    _cached_skills = []
    _router_hints = {}
    _tool_vectors_cache = {}


def _discover_skill_tool_names() -> list[str]:
    if not _skills_dir_path or not os.path.isdir(_skills_dir_path):
        return []
    out: list[str] = []
    for entry in sorted(os.listdir(_skills_dir_path)):
        if entry.startswith("."):
            continue
        full = os.path.join(_skills_dir_path, entry)
        if not os.path.isdir(full):
            continue
        md = os.path.join(full, "SKILL.md")
        if os.path.isfile(md):
            out.append(entry)
    return out


def _load_skill_markdown_only(canonical_name: str) -> dict:
    md_path = os.path.join(_skills_dir_path, canonical_name, "SKILL.md")
    raw = Path(md_path).read_text(encoding="utf-8")
    data, content = _parse_frontmatter(raw)

    if isinstance(data, dict):
        for k in list(data.keys()):
            if k not in FRONTMATTER_ALLOWED:
                logger.warning(
                    "[toolRouter] %s/SKILL.md frontmatter 字段「%s」已忽略", canonical_name, k
                )

    if data.get("name") and str(data["name"]).strip() != canonical_name:
        logger.warning(
            "[toolRouter] %s/SKILL.md frontmatter name 与目录名不一致，已以目录名为准", canonical_name
        )

    desc = str(data.get("description") or data.get("short_description") or "").strip()
    if not desc:
        raise ValueError("frontmatter 缺少 description / short_description")

    params = _extract_parameters_from_body(content)
    if not params:
        raise ValueError("正文缺少合法的 ```json 代码块（须为 OpenAI 兼容的 parameters JSON Schema）")

    skill = {"name": canonical_name, "description": desc, "parameters": params}
    router_embed_text = _body_text_for_embedding(desc, content)
    return {"skill": skill, "routerEmbedText": str(router_embed_text).strip()}


def _load_skills_from_disk() -> None:
    global _cached_skills, _router_hints, _tool_vectors_cache
    if _cached_skills:
        logger.info("[toolRouter] skills already cached (%d)", len(_cached_skills))
        return
    try:
        _cached_skills = []
        _router_hints = {}
        if not _skills_dir_path:
            logger.warning("[toolRouter] skillsDirPath 未设置，工具列表为空")
            return
        logger.info("[toolRouter] scanning %s ...", _skills_dir_path)
        names = _discover_skill_tool_names()
        logger.info("[toolRouter] discovered %d skill dirs: %s", len(names), names)
        for tool_name in names:
            try:
                result = _load_skill_markdown_only(tool_name)
                _cached_skills.append(result["skill"])
                _router_hints[result["skill"]["name"]] = result["routerEmbedText"]
            except Exception as exc:
                logger.warning("[toolRouter] 跳过 %s: %s", tool_name, exc)
        _tool_vectors_cache = {}
        logger.info("[toolRouter] loaded %d skills", len(_cached_skills))
        if not _cached_skills:
            logger.warning("[toolRouter] 未加载到任何 SKILL.md，请检查 skills 目录")
    except Exception as exc:
        logger.error("[toolRouter] loadSkillsFromDisk failed: %s", exc)


def ensure_skills_loaded() -> None:
    _load_skills_from_disk()


def get_api_skill_items() -> list[dict]:
    ensure_skills_loaded()
    return [dict(s) for s in _cached_skills]


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = sum(x * x for x in a[:n])
    nb = sum(x * x for x in b[:n])
    norm = math.sqrt(na) * math.sqrt(nb) or 1.0
    return dot / norm


# ---------------------------------------------------------------------------
# Forced/prerequisite tools
# ---------------------------------------------------------------------------

def _pick_forced_tools_by_query(query: str) -> list[str]:
    text = str(query or "").lower()
    if not text:
        return []
    has_global = bool(re.search(r"总纲|全局大纲|global\s*outline", text))
    if not has_global:
        return []
    is_edit = bool(re.search(r"编辑|修改|更新|重写|改写|写入|保存|覆盖|调整|完善|补充|rewrite|edit|update|save", text))
    if is_edit:
        return [GLOBAL_OUTLINE_EDIT_TOOL, GLOBAL_OUTLINE_READ_TOOL]
    return [GLOBAL_OUTLINE_READ_TOOL]


def _apply_tool_prerequisites(names: list[str]) -> list[str]:
    lst = [n for n in (names or []) if n]
    if not lst:
        return lst
    has_query_or_update = OUTLINE_QUERY_TOOL in lst or OUTLINE_UPDATE_TOOL in lst
    if not has_query_or_update:
        return lst
    return list(dict.fromkeys([OUTLINE_LIST_TOOL] + lst))


def _build_tool_schemas(skill_items: list[dict], names: list[str]) -> list[dict]:
    by_name = {s["name"]: s for s in skill_items}
    out: list[dict] = []
    for name in names:
        s = by_name.get(name)
        if not s:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": s.get("parameters", {"type": "object", "properties": {}, "required": []}),
            },
        })
    return out


# ---------------------------------------------------------------------------
# LLM intent classification
# ---------------------------------------------------------------------------

async def _llm_intent_for_tools(query: str, candidate_names: list[str]) -> dict:
    warnings: list[str] = []
    if not candidate_names:
        return {"success": True, "toolNames": [], "warnings": warnings}
    if not _use_intent:
        return {"success": True, "toolNames": list(candidate_names), "warnings": warnings}

    by_name = {s["name"]: s for s in _cached_skills}
    lines = []
    for name in candidate_names:
        hint = (_router_hints.get(name) or "").strip()
        js = by_name.get(name)
        desc = hint or (js.get("description", "") if js else "")
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    tool_list = "\n".join(lines)

    prompt = (
        f"你是一个写作助手的工具选择器。用户说：「{query}」\n\n"
        "以下是候选工具及简要说明（从下列候选中选出与本轮用户意图相关的**全部**工具名，"
        "用英文逗号分隔；需要几个选几个，不要人为限制数量。不要解释、不要返回说明文字）：\n"
        f"{tool_list}\n\n"
        "只返回工具名，多个用英文逗号分隔。例如：queryOutline,listOutlines,searchSparkIdeas 或仅一个：getChapterContent"
    )

    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_ollama_url}/api/generate",
                json={
                    "model": _intent_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 256},
                },
            )
            if resp.status_code != 200:
                warnings.append(f"Ollama {_intent_model} 模型调用失败，HTTP {resp.status_code}")
                return {"success": True, "toolNames": list(candidate_names), "warnings": warnings}

            data = resp.json()
            text = (data.get("response") or "").strip()
            valid = set(candidate_names)
            filtered = [s.strip() for s in re.split(r"[,，\s\n]+", text) if s.strip() and s.strip() in valid]
            if not filtered:
                lower = text.lower()
                filtered = [n for n in candidate_names if n.lower() in lower]
            if not filtered:
                filtered = list(candidate_names)
                warnings.append(f"Ollama {_intent_model} 模型调用失败，输出无法解析为工具名")
            seen: set[str] = set()
            deduped: list[str] = []
            for n in filtered:
                if n not in seen:
                    seen.add(n)
                    deduped.append(n)
            return {"success": True, "toolNames": deduped, "warnings": warnings}
    except Exception as exc:
        warnings.append(f"Ollama {_intent_model} 模型调用失败，{exc}")
        return {"success": True, "toolNames": list(candidate_names), "warnings": warnings}


# ---------------------------------------------------------------------------
# Main query router
# ---------------------------------------------------------------------------

async def get_tools_for_query(query: str) -> dict:
    """Resolve tools for a user query via embedding Top-K + optional intent."""
    warnings: list[str] = []
    candidates: list[dict] = []
    intent: dict = {"likelySkills": []}
    ensure_skills_loaded()

    if not _cached_skills:
        return {"tools": [], "warnings": warnings, "candidates": candidates, "intent": intent}

    text = (query or "").strip()
    forced_tools = _pick_forced_tools_by_query(text)

    if not text:
        names = [s["name"] for s in _cached_skills[:ROUTER_TOP_K]]
        final = _apply_tool_prerequisites(list(dict.fromkeys(forced_tools + names)))
        intent["likelySkills"] = final
        return {
            "tools": _build_tool_schemas(_cached_skills, final),
            "warnings": warnings,
            "candidates": candidates,
            "intent": intent,
        }

    try:
        tool_texts = [
            {"name": s["name"], "text": f"{s['name']}: {(_router_hints.get(s['name']) or s.get('description', '')).strip() or s['name']}"}
            for s in _cached_skills
        ]

        vectors: list[list[float]] = []
        for t in tool_texts:
            v = _tool_vectors_cache.get(t["name"])
            if v:
                vectors.append(v)
            else:
                v = await _get_embedding(t["text"])
                if v:
                    _tool_vectors_cache[t["name"]] = v
                vectors.append(v or [])

        query_vec = await _get_embedding(text)
        if not query_vec:
            fallback = [s["name"] for s in _cached_skills[:ROUTER_TOP_K]]
            final = _apply_tool_prerequisites(list(dict.fromkeys(forced_tools + fallback)))
            warnings.append(f"{_embed_service_label()} 模型调用失败，未返回向量")
            intent["likelySkills"] = final
            return {
                "tools": _build_tool_schemas(_cached_skills, final),
                "warnings": warnings,
                "candidates": candidates,
                "intent": intent,
            }

        with_score = [
            {"name": s["name"], "score": cosine_similarity(query_vec, vectors[i])}
            for i, s in enumerate(_cached_skills)
        ]
        with_score.sort(key=lambda x: x["score"], reverse=True)
        for x in with_score[:ROUTER_TOP_K]:
            candidates.append({"name": x["name"], "score": x["score"]})

        top10_raw = [t["name"] for t in with_score[:ROUTER_TOP_K]]
        top10 = list(dict.fromkeys(forced_tools + top10_raw))

        intent_res = await _llm_intent_for_tools(text, top10)
        if intent_res.get("warnings"):
            warnings.extend(intent_res["warnings"])
        intent_names = intent_res.get("toolNames") or top10
        intent["likelySkills"] = intent_names
        final = _apply_tool_prerequisites(list(dict.fromkeys(forced_tools + intent_names)))
        return {
            "tools": _build_tool_schemas(_cached_skills, final),
            "warnings": warnings,
            "candidates": candidates,
            "intent": intent,
        }
    except Exception as exc:
        logger.error("[toolRouter] getToolsForQuery error: %s", exc)
        fallback = [s["name"] for s in _cached_skills[:min(2, len(_cached_skills))]]
        final = _apply_tool_prerequisites(list(dict.fromkeys(forced_tools + fallback)))
        warnings.append(f"{_embed_service_label()} 模型调用失败，{exc}")
        intent["likelySkills"] = final
        return {
            "tools": _build_tool_schemas(_cached_skills, final),
            "warnings": warnings,
            "candidates": candidates,
            "intent": intent,
        }
