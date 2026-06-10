"""
Application configuration — reads from environment variables with sensible defaults.
"""

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# Paths
DATA_DIR: Path = Path(_env("PURRTYPOS_DATA_DIR", ""))
SKILLS_DIR: Path = Path(_env("PURRTYPOS_SKILLS_DIR", ""))

# Server
HOST: str = _env("PURRTYPOS_HOST", "127.0.0.1")
PORT: int = int(_env("PURRTYPOS_PORT", "18321"))

# 注：曾有 Ollama / mem0 / 工具路由意图模型相关配置（OLLAMA_HOST、MEM0_*、
# TOOL_ROUTER_INTENT_MODEL 等）。记忆栈已迁 SQLite FTS、工具路由已废弃
# （由主模型基于全部工具 schema 自行选择），本应用不再依赖任何本地模型。
