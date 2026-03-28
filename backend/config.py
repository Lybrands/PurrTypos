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

# Ollama
OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MEM0_USE_OLLAMA: bool = _env("MEM0_USE_OLLAMA", "1") != "0"
MEM0_EMBED_MODEL: str = _env("MEM0_EMBED_MODEL", "")
MEM0_LLM_MODEL: str = _env("MEM0_LLM_MODEL", "")

# OpenAI (used by embedding / mem0 fallback)
OPENAI_API_KEY: str = _env("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = _env("OPENAI_BASE_URL", "")

# Tool router
TOOL_ROUTER_INTENT_MODEL: str = _env("TOOL_ROUTER_INTENT_MODEL", "qwen2.5:3b")
TOOL_ROUTER_USE_LLM_INTENT: bool = _env("TOOL_ROUTER_USE_LLM_INTENT", "1") != "0"
