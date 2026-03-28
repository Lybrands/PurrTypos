"""Memory & foreshadowing routes — backed by mem0 via memory_service."""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Query

from schemas.memories import (
    AddForeshadowingRequest,
    AddMemoryRequest,
    ForeshadowingForPromptRequest,
    GetForeshadowingByBookRequest,
    GetForeshadowingByIdsRequest,
    GetMemoriesByBookRequest,
    GetMemoriesByIdsRequest,
    SearchMemoriesRequest,
    UpdateForeshadowingRequest,
    UpdateMemoryRequest,
)
from services import memory_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memories"])


# ---------------------------------------------------------------------------
# Error helper — port of main.js:811-831 mem0ErrMessage()
# ---------------------------------------------------------------------------

def _mem0_err_message(err: Exception) -> str:
    """Turn mem0-related exceptions into user/model-friendly Chinese messages."""
    msg = str(err)
    logger.error("[mem0] %s: %s", type(err).__name__, msg, exc_info=True)

    if re.search(r"ECONNREFUSED|127\.0\.0\.1:11434|localhost:11434|fetch failed|socket hang up|ConnectError", msg, re.I):
        return "长期记忆需要 Ollama 在本地运行。请先打开 Ollama 应用（或从开始菜单启动），再试一次。"
    if re.search(r"Cannot find module|MODULE_NOT_FOUND|ModuleNotFoundError|No module named", msg, re.I):
        return f"记忆服务依赖未正确加载：{msg}。请确认已安装 mem0ai（pip install mem0ai）。"
    if re.search(r"nomic-embed|embed.*model|ollama.*pull", msg, re.I):
        return "请先在终端执行：ollama pull nomic-embed-text，再试添加记忆。"
    if re.search(r"mem0 需要 Embedder|OPENAI_API_KEY", msg, re.I):
        return "未检测到本地 Ollama 或 OpenAI 配置。请安装并启动 Ollama，或设置 OPENAI_API_KEY 后重试。"
    return msg


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------

@router.post("/memories")
async def add_memory(body: AddMemoryRequest):
    try:
        data = await memory_service.add_memory(
            body.bookId, body.layer, body.content,
            chapter_id=body.chapterId,
            character_id=body.characterId,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.put("/memories/{id}")
async def update_memory(id: str, body: UpdateMemoryRequest):
    try:
        data = await memory_service.update_memory(id, body.data)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.delete("/memories/{id}")
async def delete_memory(id: str):
    try:
        await memory_service.delete_memory(id)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.get("/memories/by-book")
async def get_memories_by_book(
    bookId: str = Query(...),
    layer: Optional[str] = Query(None),
):
    try:
        data = await memory_service.get_memories_by_book(bookId, layer=layer)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.post("/memories/by-ids")
async def get_memories_by_ids(body: GetMemoriesByIdsRequest):
    try:
        data = await memory_service.get_memories_by_ids(body.ids)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.post("/memories/for-prompt")
async def get_memories_for_prompt(body: SearchMemoriesRequest):
    try:
        data = await memory_service.get_memories_for_prompt(
            body.bookId, body.query, options=body.options,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


# ---------------------------------------------------------------------------
# Foreshadowing
# ---------------------------------------------------------------------------

@router.post("/foreshadowing")
async def add_foreshadowing(body: AddForeshadowingRequest):
    try:
        data = await memory_service.add_foreshadowing(
            body.bookId,
            body.chapterId,
            body.content,
            type_=body.type,
            expected_chapter_id=body.expectedChapterId,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.put("/foreshadowing/{id}")
async def update_foreshadowing(id: str, body: UpdateForeshadowingRequest):
    try:
        data = await memory_service.update_foreshadowing(id, body.data)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.delete("/foreshadowing/{id}")
async def delete_foreshadowing(id: str):
    try:
        await memory_service.delete_foreshadowing(id)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.get("/foreshadowing/by-book")
async def get_foreshadowing_by_book(
    bookId: str = Query(...),
    status: Optional[str] = Query(None),
):
    try:
        data = await memory_service.get_foreshadowing_by_book(bookId, status_filter=status)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.post("/foreshadowing/by-ids")
async def get_foreshadowing_by_ids(body: GetForeshadowingByIdsRequest):
    try:
        data = await memory_service.get_foreshadowing_by_ids(body.ids)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}


@router.post("/foreshadowing/for-prompt")
async def get_foreshadowing_for_prompt(body: ForeshadowingForPromptRequest):
    try:
        data = await memory_service.get_foreshadowing_for_prompt(
            body.bookId, body.query, options=body.options,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _mem0_err_message(exc)}
