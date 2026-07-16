"""Instance-scoped loader for model-visible Writing ``SKILL.md`` files.

Each catalog owns one skills directory and one immutable snapshot. Calling
:meth:`load` refreshes that snapshot; :meth:`skill_items` lazily loads it and
always returns defensive copies suitable for the domain catalog builder.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)

FRONTMATTER_ALLOWED = frozenset({"name", "description"})


class WritingSkillCatalog:
    """Load Writing tool declarations without process-global state."""

    def __init__(self, skills_dir: Path):
        self._skills_dir = Path(skills_dir)
        self._items: tuple[dict[str, Any], ...] | None = None

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def load(self) -> list[dict[str, Any]]:
        """Refresh this instance's snapshot from disk.

        Invalid declarations are logged and skipped independently. Directory
        names define the canonical tool names and are processed in lexical
        order.
        """

        loaded: list[dict[str, Any]] = []
        try:
            if not self._skills_dir.is_dir():
                logger.warning(
                    "[writingSkillCatalog] skills directory is unavailable: %s",
                    self._skills_dir,
                )
                self._items = ()
                return []

            names = self._discover_skill_names()
            logger.info(
                "[writingSkillCatalog] discovered %d skill dirs: %s",
                len(names),
                names,
            )
            for canonical_name in names:
                try:
                    loaded.append(self._load_skill(canonical_name))
                except Exception as exc:
                    logger.warning(
                        "[writingSkillCatalog] skipping %s: %s",
                        canonical_name,
                        exc,
                    )
        except Exception as exc:
            logger.error("[writingSkillCatalog] loading failed: %s", exc)
            loaded = []

        self._items = tuple(loaded)
        if not self._items:
            logger.warning(
                "[writingSkillCatalog] no valid SKILL.md declarations loaded"
            )
        return self.skill_items()

    def skill_items(self) -> list[dict[str, Any]]:
        """Return defensive copies of this instance's loaded declarations."""

        if self._items is None:
            return self.load()
        return [_copy_skill_item(item) for item in self._items]

    def _discover_skill_names(self) -> list[str]:
        names: list[str] = []
        for entry in sorted(self._skills_dir.iterdir(), key=lambda path: path.name):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if not (entry / "SKILL.md").is_file():
                logger.warning(
                    "[writingSkillCatalog] ignoring %s: missing SKILL.md",
                    entry.name,
                )
                continue
            names.append(entry.name)
        return names

    def _load_skill(self, canonical_name: str) -> dict[str, Any]:
        raw = (self._skills_dir / canonical_name / "SKILL.md").read_text(
            encoding="utf-8"
        )
        data, content = _parse_frontmatter(raw)

        for key in data:
            if key not in FRONTMATTER_ALLOWED:
                logger.warning(
                    "[writingSkillCatalog] %s/SKILL.md frontmatter field %r ignored",
                    canonical_name,
                    key,
                )

        if data.get("name") and str(data["name"]).strip() != canonical_name:
            logger.warning(
                "[writingSkillCatalog] %s/SKILL.md frontmatter name does not "
                "match its directory; using the directory name",
                canonical_name,
            )

        description = str(data.get("description") or "").strip()
        if not description:
            raise ValueError("frontmatter is missing description")

        parameters = _extract_parameters_from_body(content)
        if not parameters:
            raise ValueError(
                "body is missing a valid fenced JSON parameters schema"
            )

        return {
            "name": canonical_name,
            "description": description,
            "parameters": parameters,
        }


def _copy_skill_item(item: dict[str, Any]) -> dict[str, Any]:
    """Copy the JSON-shaped declaration without exposing the snapshot."""

    return json.loads(json.dumps(item))


def _is_likely_parameters_schema(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("type") == "object" and isinstance(obj.get("properties"), dict):
        return True
    return isinstance(obj.get("properties"), dict)


def _extract_parameters_from_body(content: str) -> dict[str, Any] | None:
    source = str(content or "")
    for match in re.finditer(
        r"```(?:json)?\s*([\s\S]*?)```",
        source,
        re.IGNORECASE,
    ):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if not _is_likely_parameters_schema(parsed):
            continue
        parameters = json.loads(json.dumps(parsed))
        if "type" not in parameters:
            parameters["type"] = "object"
        return parameters
    return None


def _parse_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    """Parse the supported permissive YAML frontmatter shape."""

    source = raw_text or ""
    if not source.startswith("---"):
        return {}, source
    end = source.find("---", 3)
    if end < 0:
        return {}, source
    frontmatter = source[3:end]
    content = source[end + 3 :].lstrip("\n")
    try:
        data = yaml.safe_load(frontmatter)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, content


__all__ = ["WritingSkillCatalog"]
