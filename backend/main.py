"""
PurrTypos Python backend — FastAPI application entry point.
Launched as a child process by Electron.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import HOST, PORT, DATA_DIR
from exceptions import AppError, app_error_handler, generic_error_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    from database.connection import DatabaseConnection
    from dependencies import set_db

    data_dir = DATA_DIR if DATA_DIR and DATA_DIR != Path("") else None
    db = DatabaseConnection(data_dir)
    await db.init()
    set_db(db)

    from config import SKILLS_DIR
    from services.tool_router import set_skills_path as _set_skills_path, ensure_skills_loaded
    skills_dir = SKILLS_DIR if SKILLS_DIR and SKILLS_DIR != Path("") else Path(__file__).parent / "skills"
    _set_skills_path(str(skills_dir))
    ensure_skills_loaded()

    from routers import (
        books, outlines, chapters, articles, characters,
        sessions, conversations, memories, ai, settings,
        story_background, files, prompt_templates, book_style,
        chapter_diff,
    )
    application.include_router(books.router, prefix="/api")
    application.include_router(outlines.router, prefix="/api")
    application.include_router(chapters.router, prefix="/api")
    application.include_router(articles.router, prefix="/api")
    application.include_router(characters.router, prefix="/api")
    application.include_router(sessions.router, prefix="/api")
    application.include_router(conversations.router, prefix="/api")
    application.include_router(memories.router, prefix="/api")
    application.include_router(ai.router, prefix="/api")
    application.include_router(settings.router, prefix="/api")
    application.include_router(story_background.router, prefix="/api")
    application.include_router(files.router, prefix="/api")
    application.include_router(prompt_templates.router, prefix="/api")
    application.include_router(book_style.router, prefix="/api")
    application.include_router(chapter_diff.router, prefix="/api")

    yield

    await db.close()


app = FastAPI(title="PurrTypos Backend", version="0.4.0", lifespan=lifespan)

# CORS：本服务**仅供本机 Electron 渲染进程**调用。
# - "null" 来自打包后 file:// 加载的页面发起 fetch 时 Origin 为 "null"。
# - regex 覆盖 Vite dev server (http://localhost:5173) 与本机其他端口。
# 之前的 ``allow_origins=["*"]`` 让任何跨域脚本都能命中本机 API，对桌面端
# 是不必要的攻击面。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, generic_error_handler)


@app.get("/health")
async def health():
    """健康检查：连同数据库连接一起探活。

    返回 ``status="ok"`` 表示后端进程 + DB 连接都可用；
    ``status="degraded"`` 表示进程活着但 DB 不可用——前端可据此显示红灯
    而不是任由请求挂死。
    """
    from dependencies import _db_instance
    db_ok = bool(_db_instance) and await _db_instance.is_healthy()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "up" if db_ok else "down",
    }


@app.post("/api/debug-log")
async def debug_log():
    return {"success": True}


def main():
    port = PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    uvicorn.run(app, host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
