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

_lifespan_owner: object | None = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _lifespan_owner

    from application.agent_composition import (
        AgentComposition,
        clear_agent_composition,
        set_agent_composition,
    )
    from database.connection import DatabaseConnection
    from dependencies import clear_db, set_db

    if _lifespan_owner is not None:
        raise RuntimeError("backend lifespan is already active")
    owner = object()
    _lifespan_owner = owner

    db: DatabaseConnection | None = None
    composition: AgentComposition | None = None
    try:
        data_dir = DATA_DIR if DATA_DIR and DATA_DIR != Path("") else None
        db = DatabaseConnection(data_dir)
        await db.init()
        set_db(db)

        from infrastructure.persistence.approval_store import (
            recover_pending_approvals,
        )

        recovered_approvals = await recover_pending_approvals(db)
        if recovered_approvals:
            logging.getLogger(__name__).warning(
                "Recovered %s pending Agent approval(s) after restart",
                recovered_approvals,
            )

        from infrastructure.persistence.delegation_store import (
            recover_delegations,
        )

        recovered_delegations = await recover_delegations(db)
        if any(recovered_delegations.values()):
            logging.getLogger(__name__).warning(
                "Recovered Agent delegations after restart: %s",
                recovered_delegations,
            )

        from config import SKILLS_DIR

        skills_dir = (
            SKILLS_DIR
            if SKILLS_DIR and SKILLS_DIR != Path("")
            else Path(__file__).parent / "skills"
        )
        composition = AgentComposition(db, skills_dir=skills_dir)
        set_agent_composition(composition)

        from routers import (
            ai,
            articles,
            book_style,
            books,
            chapter_diff,
            chapters,
            characters,
            conversations,
            dashboard,
            export,
            files,
            memories,
            outlines,
            prompt_templates,
            screenplay,
            sessions,
            setting_diff,
            setting_entities,
            settings,
            story_memory,
            story_background,
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
        application.include_router(screenplay.router, prefix="/api")
        application.include_router(book_style.router, prefix="/api")
        application.include_router(chapter_diff.router, prefix="/api")
        application.include_router(setting_diff.router, prefix="/api")
        application.include_router(setting_entities.router, prefix="/api")
        application.include_router(story_memory.router, prefix="/api")
        application.include_router(dashboard.router, prefix="/api")
        application.include_router(export.router, prefix="/api")

        yield
    finally:
        try:
            if composition is not None:
                await composition.shutdown()
                clear_agent_composition(composition)
        finally:
            try:
                if db is not None:
                    clear_db(db)
                    await db.close()
            finally:
                if _lifespan_owner is owner:
                    _lifespan_owner = None


app = FastAPI(title="PurrTypos Backend", version="0.5.2", lifespan=lifespan)

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
