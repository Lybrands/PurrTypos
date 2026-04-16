"""User-defined prompt templates for the AI chat composer."""

from __future__ import annotations

from fastapi import APIRouter

from database.crud import prompt_templates as tpl_crud
from dependencies import get_db
from schemas.prompt_templates import (
    CreatePromptTemplateRequest,
    ReorderPromptTemplatesRequest,
    UpdatePromptTemplateRequest,
)

router = APIRouter(tags=["prompt-templates"])


@router.get("/prompt-templates")
async def list_templates():
    db = get_db()
    data = await tpl_crud.list_prompt_templates(db)
    return {"success": True, "data": data}


@router.post("/prompt-templates")
async def create_template(body: CreatePromptTemplateRequest):
    db = get_db()
    data = await tpl_crud.create_prompt_template(
        db, title=body.title.strip(), content=body.content or "", sort=body.sort
    )
    return {"success": True, "data": data}


@router.put("/prompt-templates/{tpl_id}")
async def update_template(tpl_id: int, body: UpdatePromptTemplateRequest):
    db = get_db()
    data = await tpl_crud.update_prompt_template(
        db,
        tpl_id=tpl_id,
        title=body.title.strip() if body.title is not None else None,
        content=body.content,
        sort=body.sort,
    )
    if not data:
        return {"success": False, "error": "模版不存在"}
    return {"success": True, "data": data}


@router.delete("/prompt-templates/{tpl_id}")
async def delete_template(tpl_id: int):
    db = get_db()
    await tpl_crud.delete_prompt_template(db, tpl_id)
    return {"success": True}


@router.post("/prompt-templates/reorder")
async def reorder_templates(body: ReorderPromptTemplatesRequest):
    db = get_db()
    await tpl_crud.reorder_prompt_templates(db, body.ids)
    return {"success": True}
