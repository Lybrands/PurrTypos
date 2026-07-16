"""Model/host argument boundary for Writing-domain tools.

``bookId`` belongs entirely to the host Agent Run context.  ``chapterId`` has
a deliberately narrower dual meaning for the two single-chapter tools:

* omission (or an exact, user-facing current-chapter alias) means the current
  host chapter;
* every other value is an explicit chapter selection and must remain intact
  for the Writing catalog/database scope checks.

The source SKILL schemas retain ``bookId`` for handler/contract documentation,
while model-facing copies hide it.  Callers must validate the unmodified model
arguments first, then bind host defaults immediately before invoking a handler.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


_EMPTY_PARAMETERS = {"type": "object", "properties": {}, "required": []}


CURRENT_CHAPTER_DEFAULT_TOOLS = frozenset({
    "editChapterContent",
    "getChapterContent",
})
CURRENT_CHAPTER_ALIASES = frozenset({"当前章节", "当前章", "本章"})


def _mutable_json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _mutable_json_copy(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_mutable_json_copy(item) for item in value]
    return deepcopy(value)


def copy_parameters_schema(parameters: Any) -> dict[str, Any]:
    """Return a detached mutable copy of a JSON-shaped parameters schema."""

    if not isinstance(parameters, Mapping):
        return deepcopy(_EMPTY_PARAMETERS)
    return _mutable_json_copy(parameters)


def model_visible_writing_parameters(parameters: Any) -> dict[str, Any]:
    """Hide host-owned ``bookId`` from a detached Writing schema copy."""

    visible = copy_parameters_schema(parameters)
    properties = visible.get("properties")
    if isinstance(properties, Mapping):
        visible_properties = deepcopy(dict(properties))
        visible_properties.pop("bookId", None)
        visible["properties"] = visible_properties

    required = visible.get("required")
    if isinstance(required, (list, tuple)):
        visible["required"] = [
            item
            for item in required
            if str(item).strip() != "bookId"
        ]
    return visible


def bind_host_book_id(
    context: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy arguments and overwrite ``bookId`` with host-owned run context."""

    bound = _mutable_json_copy(arguments)
    host_book_id = context.get("bookId")
    if str(host_book_id or "").strip():
        bound["bookId"] = host_book_id
    return bound


def is_current_chapter_alias(value: Any) -> bool:
    """Return whether ``value`` is one of the exact supported UI aliases."""

    return (
        isinstance(value, str)
        and value.strip() in CURRENT_CHAPTER_ALIASES
    )


def _uses_current_chapter_default(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or is_current_chapter_alias(value)
    )


def validate_host_chapter_reference(
    context: Mapping[str, Any],
    tool_name: str,
    arguments: Mapping[str, Any],
) -> str | None:
    """Fail closed when a current-chapter alias has no host chapter to bind.

    Explicit non-current ids are intentionally *not* compared with the current
    chapter.  Their authority comes from the Writing catalog and the
    book-scoped database lookup in the concrete handler.
    """

    if tool_name not in CURRENT_CHAPTER_DEFAULT_TOOLS:
        return None
    if not is_current_chapter_alias(arguments.get("chapterId")):
        return None
    if str(context.get("chapterId") or "").strip():
        return None
    return "The current chapter is unavailable in the current Agent Run scope."


def bind_host_writing_arguments(
    context: Mapping[str, Any],
    tool_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind host-owned Writing defaults without rewriting explicit targets.

    ``bookId`` is always overwritten when the run has one.  For the two tools
    whose SKILL contract defaults to the current chapter, only a missing/blank
    ``chapterId`` or a supported exact alias is replaced.  A real id, an
    unknown id, a chapter title, and every chapter argument on every other tool
    are copied unchanged so the normal catalog checks remain authoritative.
    """

    bound = bind_host_book_id(context, arguments)
    if tool_name not in CURRENT_CHAPTER_DEFAULT_TOOLS:
        return bound

    raw_chapter_id = arguments.get("chapterId")
    if not _uses_current_chapter_default(raw_chapter_id):
        return bound

    host_chapter_id = context.get("chapterId")
    if str(host_chapter_id or "").strip():
        bound["chapterId"] = host_chapter_id
    elif is_current_chapter_alias(raw_chapter_id):
        # Validation should reject this before binding.  Removing the alias is
        # a second fail-closed layer for direct adapter use: a handler can now
        # report a missing locator instead of treating display text as an id.
        bound.pop("chapterId", None)
    return bound


__all__ = [
    "CURRENT_CHAPTER_ALIASES",
    "CURRENT_CHAPTER_DEFAULT_TOOLS",
    "bind_host_book_id",
    "bind_host_writing_arguments",
    "copy_parameters_schema",
    "is_current_chapter_alias",
    "model_visible_writing_parameters",
    "validate_host_chapter_reference",
]
