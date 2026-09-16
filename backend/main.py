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
from types import SimpleNamespace

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


def _set_memory_component_status(application, value: dict) -> None:
    state = getattr(application, "state", None)
    if state is None:
        state = SimpleNamespace()
        application.state = state
    state.memory_component = value


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _lifespan_owner

    from application.agent_composition import (
        AgentComposition,
        clear_agent_composition,
        set_agent_composition,
    )
    from agents.shared.acceptance_rollout import resolve_process_rollout_policy
    from application.composition_factory import create_versioned_agent_composition
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
    novel_analysis_recovery_monitor: asyncio.Task[None] | None = None
    novel_analysis_baseline_monitor: asyncio.Task[None] | None = None
    orphan_monitor_stop: asyncio.Event | None = None
    artifact_monitor_stop: asyncio.Event | None = None
    novel_analysis_recovery_stop: asyncio.Event | None = None
    knowledge_resource = None
    try:
        data_dir = DATA_DIR if DATA_DIR and DATA_DIR != Path("") else None
        db = DatabaseConnection(data_dir)
        await db.init()
        set_db(db)
        from application.novel_knowledge_service import get_novel_knowledge_service
        knowledge_resource = get_novel_knowledge_service(db)
        await knowledge_resource.initialize(db._data_dir)
        execution_db = DatabaseConnection(data_dir)
        await execution_db.init(initialize_schema=False)

        from infrastructure.persistence.approval_store import (
            recover_pending_approvals,
        )

        recovered_approvals = await recover_pending_approvals(db)
        if recovered_approvals:
            logging.getLogger(__name__).warning(
                "Recovered %s pending Agent approval(s) after restart",
                recovered_approvals,
            )

        from application.agent_orphan_recovery_service import (
            AgentOrphanRecoveryService,
        )
        from infrastructure.persistence.writing_chat_request_store import (
            SqliteWritingChatRequestStore,
        )

        await SqliteWritingChatRequestStore(db).recover_unbound()

        from application.memory_component import (
            MemoryComponentConfigurationError,
            create_memory_component_resource,
        )
        from infrastructure.memory import MemoryResourceError

        memory_resource = None
        _set_memory_component_status(application, {"status": "unconfigured"})
        try:
            memory_resource = await create_memory_component_resource(
                db,
                data_dir=(data_dir or Path(".")),
            )
            if memory_resource is not None:
                _set_memory_component_status(application, {
                    "status": "ready",
                    "embeddingDimensions": (
                        memory_resource.configuration.embedding_dimensions
                    ),
                })
        except (
            MemoryComponentConfigurationError,
            MemoryResourceError,
        ) as error:
            _set_memory_component_status(application, {
                "status": "unavailable",
                "code": error.code,
            })
            logging.getLogger(__name__).error(
                "Memory component is unavailable: %s",
                error.code,
            )
        rollout_policy = resolve_process_rollout_policy(
            data_dir=data_dir,
        )
        composition = create_versioned_agent_composition(
            db,
            execution_db=execution_db,
            memory_resource=memory_resource,
            agent_rollout_policy=rollout_policy,
        )
        recovered_long_tasks = await (
            composition.long_task_repository.recover_after_restart()
        )
        if recovered_long_tasks:
            logging.getLogger(__name__).warning(
                "Checkpointed %s abandoned long task(s) after restart: %s",
                len(recovered_long_tasks),
                ", ".join(recovered_long_tasks),
            )
        orphan_recovery = AgentOrphanRecoveryService(db, composition)
        # Startup does not prove that another process holding a live lease is
        # dead. Normal orphan recovery honours persisted lease deadlines and
        # will reclaim genuinely abandoned work after expiry.
        recovered_runs = await orphan_recovery.recover(after_restart=False)
        from infrastructure.persistence.run_conversation_store import (
            materialize_terminal_writing_run_holes,
        )

        await materialize_terminal_writing_run_holes(db)
        if recovered_runs:
            logging.getLogger(__name__).warning(
                "Recovered %s abandoned Agent Run(s) after restart: %s",
                len(recovered_runs),
                ", ".join(recovered_runs),
            )

        from agents.screenplay.recovery_service import (
            ScreenplayReplacementRecoveryService,
        )

        recovered_turn_ids = await ScreenplayReplacementRecoveryService(
            db,
            composition,
        ).recover_stale_admissions()
        if recovered_turn_ids:
            logging.getLogger(__name__).warning(
                "Recovered %s screenplay Turn projection(s) after restart",
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

        set_agent_composition(composition)

        from agents.novel_analysis.automatic_recovery import (
            NovelAnalysisReplacementAutomaticRecovery,
            monitor_novel_analysis_replacement_recovery,
        )
        from agents.novel_analysis.reliability_baseline import (
            NovelAnalysisReliabilityBaselineService,
            monitor_novel_analysis_reliability_baseline,
        )
        novel_analysis_recovery = NovelAnalysisReplacementAutomaticRecovery(
            db,
            composition,
        )
        novel_analysis_baseline = NovelAnalysisReliabilityBaselineService(db)
        recovered_novel_analyses = await novel_analysis_recovery.recover_due()
        if recovered_novel_analyses:
            logging.getLogger(__name__).info(
                "Dispatched automatic recovery for %s novel-analysis task(s) after startup",
                len(recovered_novel_analyses),
            )
        try:
            await novel_analysis_baseline.capture_due()
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to capture startup novel-analysis reliability baseline",
            )
        if memory_resource is not None:
            from application.memory_delivery import MemoryDeliveryService
            from application.memory_operations import MemoryApplicationService

            recovered_memory_deliveries = await MemoryDeliveryService(
                db,
                MemoryApplicationService(db, memory_resource),
            ).recover()
            failed_memory_deliveries = tuple(
                item for item in recovered_memory_deliveries
                if item.status != "completed"
            )
            if failed_memory_deliveries:
                logging.getLogger(__name__).warning(
                    "Memory source delivery recovery left %s item(s) pending",
                    len(failed_memory_deliveries),
                )

        from infrastructure.persistence.orphan_run_monitor import (
            monitor_orphaned_runs,
        )

        orphan_monitor_stop = asyncio.Event()
        artifact_monitor_stop = asyncio.Event()
        novel_analysis_recovery_stop = asyncio.Event()
        orphan_monitor = asyncio.create_task(
            monitor_orphaned_runs(
                recover_orphans=orphan_recovery.recover,
                stop_event=orphan_monitor_stop,
                reconcile_terminal_holes=lambda: (
                    materialize_terminal_writing_run_holes(db)
                ),
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
        novel_analysis_recovery_monitor = asyncio.create_task(
            monitor_novel_analysis_replacement_recovery(
                novel_analysis_recovery,
                stop_event=novel_analysis_recovery_stop,
            )
        )
        novel_analysis_baseline_monitor = asyncio.create_task(
            monitor_novel_analysis_reliability_baseline(
                novel_analysis_baseline,
                stop_event=novel_analysis_recovery_stop,
            )
        )

        from routers import (
            ai,
            articles,
            books,
            chapter_diff,
            chapters,
            characters,
            conversations,
            continuations,
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
            writing_techniques,
            novel_sources,
            novel_knowledge,
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
        application.include_router(chapter_diff.router, prefix="/api")
        application.include_router(setting_diff.router, prefix="/api")
        application.include_router(setting_entities.router, prefix="/api")
        application.include_router(story_memory.router, prefix="/api")
        application.include_router(writing_techniques.router, prefix="/api")
        application.include_router(novel_sources.router, prefix="/api")
        application.include_router(novel_knowledge.router, prefix="/api")
        application.include_router(continuations.router, prefix="/api")
        application.include_router(dashboard.router, prefix="/api")
        application.include_router(export.router, prefix="/api")

        yield
    finally:
        try:
            if artifact_monitor_stop is not None:
                artifact_monitor_stop.set()
            if orphan_monitor_stop is not None:
                orphan_monitor_stop.set()
            if novel_analysis_recovery_stop is not None:
                novel_analysis_recovery_stop.set()
            if artifact_monitor is not None:
                await artifact_monitor
            if orphan_monitor is not None:
                await orphan_monitor
            if novel_analysis_recovery_monitor is not None:
                await novel_analysis_recovery_monitor
            if novel_analysis_baseline_monitor is not None:
                await novel_analysis_baseline_monitor
        finally:
            try:
                if composition is not None:
                    await composition.shutdown()
                    clear_agent_composition(composition)
            finally:
                try:
                    if knowledge_resource is not None:
                        await knowledge_resource.close()
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
                        _set_memory_component_status(
                            application,
                            {"status": "closed"},
                        )


app = FastAPI(title="PurrTypos Backend", version="0.5.2", lifespan=lifespan)

# CORS：本服务**仅供本机 Electron 渲染进程**调用。
# - "app://." 来自当前 Electron 打包协议；"null" 保留给 file://
#   加载的本机页面。
# - regex 覆盖 Vite dev server (http://localhost:5174) 与本机其他端口。
# 之前的 ``allow_origins=["*"]`` 让任何跨域脚本都能命中本机 API，对桌面端
# 是不必要的攻击面。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://.", "null"],
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
        "memory": getattr(
            app.state,
            "memory_component",
            {"status": "unconfigured"},
        ),
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
