"""
Skill loader — scans ``skills/<name>/SKILL.md`` directories, parses
frontmatter + JSON schema and exposes the resulting skill list to the
rest of the backend.

NOTE: 这里只保留**装载**职责。历史上的 embedding Top-K 检索 / Ollama
意图分类等"路由"逻辑均已废弃（路由由模型自身基于全部工具 schema 决定），
相关代码已删除以避免误导。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

FRONTMATTER_ALLOWED = {"name", "description", "short_description"}

_cached_skills: list[dict] = []
_skills_dir_path: str = ""


def _is_likely_parameters_schema(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("type") == "object" and isinstance(obj.get("properties"), dict):
        return True
    if isinstance(obj.get("properties"), dict):
        return True
    return False


def _extract_parameters_from_body(content: str) -> dict | None:
    src = str(content or "")
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", src, re.IGNORECASE):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            if _is_likely_parameters_schema(obj):
                params = json.loads(json.dumps(obj))
                if "type" not in params:
                    params["type"] = "object"
                return params
        except Exception:
            continue
    return None


def _parse_frontmatter(raw_text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (data, content)."""
    raw_text = raw_text or ""
    if not raw_text.startswith("---"):
        return {}, raw_text
    end = raw_text.find("---", 3)
    if end < 0:
        return {}, raw_text
    fm_text = raw_text[3:end]
    content = raw_text[end + 3:].lstrip("\n")
    try:
        data = yaml.safe_load(fm_text)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return data, content


def set_skills_path(dir_path: str) -> None:
    global _cached_skills, _skills_dir_path
    _skills_dir_path = dir_path or ""
    _cached_skills = []


def _discover_skill_tool_names() -> list[str]:
    if not _skills_dir_path or not os.path.isdir(_skills_dir_path):
        return []
    out: list[str] = []
    for entry in sorted(os.listdir(_skills_dir_path)):
        if entry.startswith("."):
            continue
        full = os.path.join(_skills_dir_path, entry)
        if not os.path.isdir(full):
            continue
        md = os.path.join(full, "SKILL.md")
        if os.path.isfile(md):
            out.append(entry)
    return out


def _load_skill_markdown_only(canonical_name: str) -> dict:
    md_path = os.path.join(_skills_dir_path, canonical_name, "SKILL.md")
    raw = Path(md_path).read_text(encoding="utf-8")
    data, content = _parse_frontmatter(raw)

    if isinstance(data, dict):
        for k in list(data.keys()):
            if k not in FRONTMATTER_ALLOWED:
                logger.warning(
                    "[toolRouter] %s/SKILL.md frontmatter 字段「%s」已忽略", canonical_name, k
                )

    if data.get("name") and str(data["name"]).strip() != canonical_name:
        logger.warning(
            "[toolRouter] %s/SKILL.md frontmatter name 与目录名不一致，已以目录名为准", canonical_name
        )

    desc = str(data.get("description") or data.get("short_description") or "").strip()
    if not desc:
        raise ValueError("frontmatter 缺少 description / short_description")

    params = _extract_parameters_from_body(content)
    if not params:
        raise ValueError("正文缺少合法的 ```json 代码块（须为 OpenAI 兼容的 parameters JSON Schema）")

    return {"name": canonical_name, "description": desc, "parameters": params}


def _load_skills_from_disk() -> None:
    global _cached_skills
    if _cached_skills:
        logger.info("[toolRouter] skills already cached (%d)", len(_cached_skills))
        return
    try:
        _cached_skills = []
        if not _skills_dir_path:
            logger.warning("[toolRouter] skillsDirPath 未设置，工具列表为空")
            return
        logger.info("[toolRouter] scanning %s ...", _skills_dir_path)
        names = _discover_skill_tool_names()
        logger.info("[toolRouter] discovered %d skill dirs: %s", len(names), names)
        for tool_name in names:
            try:
                skill = _load_skill_markdown_only(tool_name)
                _cached_skills.append(skill)
            except Exception as exc:
                logger.warning("[toolRouter] 跳过 %s: %s", tool_name, exc)
        logger.info("[toolRouter] loaded %d skills", len(_cached_skills))
        if not _cached_skills:
            logger.warning("[toolRouter] 未加载到任何 SKILL.md，请检查 skills 目录")
    except Exception as exc:
        logger.error("[toolRouter] loadSkillsFromDisk failed: %s", exc)


def ensure_skills_loaded() -> None:
    _load_skills_from_disk()


def get_api_skill_items() -> list[dict]:
    ensure_skills_loaded()
    return [dict(s) for s in _cached_skills]
