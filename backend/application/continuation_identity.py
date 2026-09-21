from exceptions import AppError


async def require_editable_identity(db, identity):
    if str(identity).startswith("source:") or await db.fetch_one("SELECT 1 FROM continuation_source_sections WHERE id=? LIMIT 1", [str(identity)]):
        raise AppError("原作历史章节只读，不能作为可编辑章节使用", 403)
