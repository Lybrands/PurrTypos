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
        story_background, files, prompt_templates,
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

    yield

    await db.close()


app = FastAPI(title="PurrTypos Backend", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, generic_error_handler)


@app.get("/health")
async def health():
    return {"status": "ok"}


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
