"""Versioned loader for the repository-owned writing Skill Creator."""

from __future__ import annotations

import hashlib
from pathlib import Path


CREATOR_SKILL_ID = "purrtypos-writing-skill-creator"
CREATOR_SKILL_VERSION = 2
# 与 application.builtin_skills.py 共用 agents/builtin_skills 下的同一份源文件。
_ROOT = Path(__file__).resolve().parents[1] / "builtin_skills" / CREATOR_SKILL_ID


def creator_skill_files() -> dict[str, str]:
    files = {
        str(path.relative_to(_ROOT)).replace("\\", "/"): path.read_text(encoding="utf-8")
        for path in sorted(_ROOT.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    }
    if "SKILL.md" not in files:
        raise RuntimeError("built-in writing Skill Creator has no SKILL.md")
    return files


def creator_skill_digest() -> str:
    digest = hashlib.sha256()
    for path, content in creator_skill_files().items():
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def creator_skill_instructions() -> str:
    files = creator_skill_files()
    ordered = ["SKILL.md", *(path for path in files if path != "SKILL.md")]
    return "\n\n---\n\n".join(
        f"<!-- {path} -->\n{files[path]}" for path in ordered
    )


__all__ = [
    "CREATOR_SKILL_ID",
    "CREATOR_SKILL_VERSION",
    "creator_skill_digest",
    "creator_skill_files",
    "creator_skill_instructions",
]
