"""
PurrTypos Python backend — FastAPI application entry point.
Launched as a child process by Electron.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

# The Agent framework is an independent monorepo package. Source deployments
# install it or vendor it beside this backend; repository runs use its src root.
_purra_src = Path(__file__).resolve().parent.parent / "packages" / "purra" / "src"
if _purra_src.is_dir() and str(_purra_src) not in sys.path:
    sys.path.insert(0, str(_purra_src))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from config import (
    AGENT_ARTIFACT_MAINTENANCE_INTERVAL_SECONDS,
    AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS,
    DATA_DIR,
    HOST,
    PORT,
)
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
    from application.composition_factory import create_agent_composition
    from database.connection import DatabaseConnection
    from dependencies import clear_db, set_db

    if _lifespan_owner is not None:
        raise RuntimeError("backend lifespan is already active")
    owner = object()
    _lifespan_owner = owner

    db: DatabaseConnection | None = None
    execution_db: DatabaseConnection | None = None
    composition: AgentComposition | None = None
    orphan_monitor: asyncio.Task[None] | None = None
    artifact_monitor: asyncio.Task[None] | None = None
    orphan_monitor_stop: asyncio.Event | None = None
    artifact_monitor_stop: asyncio.Event | None = None
    try:
        data_dir = DATA_DIR if DATA_DIR and DATA_DIR != Path("") else None
        db = DatabaseConnection(data_dir)
        await db.init()
        set_db(db)
        execution_db = DatabaseConnection(data_dir)
        await execution_db.init()

        from infrastructure.persistence.approval_store import (
            recover_pending_approvals,
        )

        recovered_approvals = await recover_pending_approvals(db)
        if recovered_approvals:
            logging.getLogger(__name__).warning(
                "Recovered %s pending Agent approval(s) after restart",
                recovered_approvals,
            )

        from infrastructure.persistence.run_execution_store import (
            recover_orphaned_runs,
        )

        recovered_runs = await recover_orphaned_runs(
            execution_db,
            after_restart=True,
        )
        if recovered_runs:
            logging.getLogger(__name__).warning(
                "Recovered %s abandoned Agent Run(s) after restart: %s",
                len(recovered_runs),
                ", ".join(recovered_runs),
            )

        from infrastructure.persistence.sqlite_long_task_repository import (
            SqliteLongTaskRepository,
        )

        recovered_long_tasks = await SqliteLongTaskRepository(
            db
        ).recover_after_restart()
        if recovered_long_tasks:
            logging.getLogger(__name__).warning(
                "Checkpointed %s abandoned long task(s) after restart: %s",
                len(recovered_long_tasks),
                ", ".join(recovered_long_tasks),
            )

        from infrastructure.persistence.sqlite_screenplay_agent_repository import (
            SqliteScreenplayAgentRepository,
        )

        recovered_turn_ids = await SqliteScreenplayAgentRepository(
            db,
            owner_id="screenplay-startup-recovery",
        ).recover_after_restart()
        if recovered_turn_ids:
            logging.getLogger(__name__).warning(
                "Failed %s credential-bound screenplay Turn(s) after restart",
                len(recovered_turn_ids),
            )

        from purra.artifacts import ArtifactMaintenancePolicy
        from application.artifact_maintenance import (
            monitor_artifact_maintenance,
            run_artifact_maintenance,
        )
        from infrastructure.persistence import (
            sqlite_artifact_maintenance_repository as artifact_maintenance,
        )

        artifact_maintenance_repository = (
            artifact_maintenance.SqliteArtifactMaintenanceRepository(db)
        )
        artifact_maintenance_policy = ArtifactMaintenancePolicy(
            terminal_retention_ms=(
                int(AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS * 1_000)
                if AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS is not None
                else None
            ),
        )
        await run_artifact_maintenance(
            artifact_maintenance_repository,
            artifact_maintenance_policy,
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
        composition = create_agent_composition(
            db,
            execution_db=execution_db,
            skills_dir=skills_dir,
        )
        set_agent_composition(composition)

        from infrastructure.persistence.orphan_run_monitor import (
            monitor_orphaned_runs,
        )

        orphan_monitor_stop = asyncio.Event()
        artifact_monitor_stop = asyncio.Event()
        orphan_monitor = asyncio.create_task(
            monitor_orphaned_runs(
                execution_db,
                stop_event=orphan_monitor_stop,
            )
        )
        artifact_monitor = asyncio.create_task(
            monitor_artifact_maintenance(
                artifact_maintenance_repository,
                artifact_maintenance_policy,
                poll_interval_seconds=(
                    AGENT_ARTIFACT_MAINTENANCE_INTERVAL_SECONDS
                ),
                stop_event=artifact_monitor_stop,
            )
        )

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
            screenplay_v2,
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
        application.include_router(screenplay_v2.router, prefix="/api")
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
            if artifact_monitor_stop is not None:
                artifact_monitor_stop.set()
            if orphan_monitor_stop is not None:
                orphan_monitor_stop.set()
            if artifact_monitor is not None:
                await artifact_monitor
            if orphan_monitor is not None:
                await orphan_monitor
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
                    try:
                        if execution_db is not None:
                            await execution_db.close()
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


def _resolve_web_dist_dir() -> Path | None:
    explicit = os.environ.get("PURRTYPOS_WEB_DIST_DIR", "").strip()
    candidates = [
        Path(explicit) if explicit else None,
        Path(__file__).resolve().parent.parent / "dist",
        Path(sys.executable).resolve().parent.parent / "web"
        if getattr(sys, "frozen", False)
        else None,
    ]
    for candidate in candidates:
        if candidate and (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


WEB_DIST_DIR = _resolve_web_dist_dir()


@app.middleware("http")
async def serve_web_frontend(request: Request, call_next):
    """Serve the production React bundle without affecting /api routes."""
    if (
        WEB_DIST_DIR is None
        or request.method != "GET"
        or request.url.path == "/health"
        or request.url.path.startswith("/api/")
    ):
        return await call_next(request)

    relative = request.url.path.lstrip("/") or "index.html"
    candidate = (WEB_DIST_DIR / relative).resolve()
    try:
        candidate.relative_to(WEB_DIST_DIR)
    except ValueError:
        return await call_next(request)

    if candidate.is_file():
        media_type, _ = mimetypes.guess_type(candidate.name)
        return FileResponse(candidate, media_type=media_type)
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse(WEB_DIST_DIR / "index.html", media_type="text/html")
    return await call_next(request)


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
    args = sys.argv[1:]
    for argument in args:
        try:
            port = int(argument)
            break
        except ValueError:
            pass
    if "--open-browser" in args:
        threading.Timer(
            0.8,
            lambda: webbrowser.open(f"http://127.0.0.1:{port}"),
        ).start()
    uvicorn.run(app, host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
