from __future__ import annotations

from fastapi import APIRouter

from database.crud import book_style as book_style_crud
from dependencies import get_db
from schemas.book_style import SaveBookStyleRequest

router = APIRouter(tags=["book-style"])


@router.get("/book-style/{bookId}")
async def get_book_style(bookId: str):
    db = get_db()
    row = await book_style_crud.get_book_style(db, bookId)
    return {"success": True, "data": row}


@router.put("/book-style/{bookId}")
async def save_book_style(bookId: str, body: SaveBookStyleRequest):
    db = get_db()
    await book_style_crud.save_book_style(db, bookId, body.model_dump())
    return {"success": True}
