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


# Paths
DATA_DIR: Path = Path(_env("PURRTYPOS_DATA_DIR", ""))
SKILLS_DIR: Path = Path(_env("PURRTYPOS_SKILLS_DIR", ""))

# Server
HOST: str = _env("PURRTYPOS_HOST", "127.0.0.1")
PORT: int = int(_env("PURRTYPOS_PORT", "18321"))

# Protected calls fail closed after this many seconds without a human decision.
# Keep the default deliberately short so abandoned confirmations fail closed.
AGENT_APPROVAL_TIMEOUT_SECONDS: float = _env_positive_float(
    "PURRTYPOS_AGENT_APPROVAL_TIMEOUT_SECONDS",
    300.0,
)

# 注：曾有 Ollama / mem0 / 工具路由意图模型相关配置（OLLAMA_HOST、MEM0_*、
# TOOL_ROUTER_INTENT_MODEL 等）。记忆栈已迁 SQLite FTS、工具路由已废弃
# （由主模型基于全部工具 schema 自行选择），本应用不再依赖任何本地模型。
