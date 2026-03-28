"""
Unified exception handling — replaces ~50 identical try/catch blocks
in the original main.js IPC handlers.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base application error with an HTTP-friendly message."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, 404)


class DatabaseNotReadyError(AppError):
    def __init__(self):
        super().__init__("数据库未就绪", 503)


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.message},
    )


async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc)},
    )
