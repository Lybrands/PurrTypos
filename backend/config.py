"""
Application configuration — reads from environment variables with sensible defaults.
"""

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    value = _env(key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


# Paths
DATA_DIR: Path = Path(_env("PURRTYPOS_DATA_DIR", ""))
SKILLS_DIR: Path = Path(_env("PURRTYPOS_SKILLS_DIR", ""))

# Server
HOST: str = _env("PURRTYPOS_HOST", "127.0.0.1")
PORT: int = int(_env("PURRTYPOS_PORT", "18321"))

# Agent release metadata. Packaging or deployment should override these so
# every persisted run can be attributed to the code version/cohort that made it.
AGENT_RELEASE_VERSION: str = _env("PURRTYPOS_AGENT_RELEASE_VERSION", "development")
AGENT_ROLLOUT_COHORT: str = _env("PURRTYPOS_AGENT_ROLLOUT_COHORT", "local")

# Stage-3 compatibility switch. Owner: application runtime composition.
# Keep false until both legacy/new runtime tapes and the real-tool pilot pass;
# delete with the old routers.ai loop after the staged rollout is complete.
AGENT_CORE_RUNTIME_ENABLED: bool = _env_bool(
    "PURRTYPOS_AGENT_CORE_RUNTIME_ENABLED",
    False,
)

# 注：曾有 Ollama / mem0 / 工具路由意图模型相关配置（OLLAMA_HOST、MEM0_*、
# TOOL_ROUTER_INTENT_MODEL 等）。记忆栈已迁 SQLite FTS、工具路由已废弃
# （由主模型基于全部工具 schema 自行选择），本应用不再依赖任何本地模型。
