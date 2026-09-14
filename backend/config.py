"""
Application configuration — reads from environment variables with sensible defaults.
"""

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_positive_float(key: str, default: float) -> float:
    raw = _env(key, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a positive number") from error
    if value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return value


def _env_optional_non_negative_float(key: str) -> float | None:
    raw = _env(key).strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a non-negative number") from error
    if value < 0:
        raise ValueError(f"{key} must be a non-negative number")
    return value


def _env_flag(key: str, default: bool = False) -> bool:
    raw = _env(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# Paths
DATA_DIR: Path = Path(_env("PURRTYPOS_DATA_DIR", ""))

# Server
HOST: str = _env("PURRTYPOS_HOST", "127.0.0.1")
PORT: int = int(_env("PURRTYPOS_PORT", "18321"))
DEV_DIAGNOSTICS_ENABLED: bool = _env_flag("PURRTYPOS_DEV_DIAGNOSTICS")

# Protected calls fail closed after this many seconds without a human decision.
# Keep the default deliberately short so abandoned confirmations fail closed.
AGENT_APPROVAL_TIMEOUT_SECONDS: float = _env_positive_float(
    "PURRTYPOS_AGENT_APPROVAL_TIMEOUT_SECONDS",
    300.0,
)

# Artifact claims are transient and reaped continuously. Durable terminal
# content is retained indefinitely unless an operator explicitly configures a
# retention duration. Open recovery state is never removed by retention GC.
AGENT_ARTIFACT_MAINTENANCE_INTERVAL_SECONDS: float = _env_positive_float(
    "PURRTYPOS_AGENT_ARTIFACT_MAINTENANCE_INTERVAL_SECONDS",
    30.0,
)
AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS: float | None = (
    _env_optional_non_negative_float(
        "PURRTYPOS_AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS"
    )
)

# 注：曾有 Ollama / mem0 / 工具路由意图模型相关配置（OLLAMA_HOST、MEM0_*、
# TOOL_ROUTER_INTENT_MODEL 等）。记忆栈已迁到本地 PurrA 组件、工具路由已废弃
# （由主模型基于全部工具 schema 自行选择），本应用不再依赖任何本地模型。
