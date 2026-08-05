"""Model/host argument boundary for Writing-domain tools.

``bookId`` belongs entirely to the host Agent Run context. For the two
single-chapter tools, an omitted ``chapterId`` selects the host-bound current
chapter; every supplied value remains an explicit chapter selection.

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


def _uses_current_chapter_default(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
    )


def bind_host_writing_arguments(
    context: Mapping[str, Any],
    tool_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind host-owned Writing defaults without rewriting explicit targets.

    ``bookId`` is always overwritten when the run has one.  For the two tools
    whose SKILL contract defaults to the current chapter, only a missing or
    blank ``chapterId`` is replaced. Every supplied value is copied unchanged
    so the normal catalog checks remain authoritative.
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
    return bound


__all__ = [
    "CURRENT_CHAPTER_DEFAULT_TOOLS",
    "bind_host_book_id",
    "bind_host_writing_arguments",
    "copy_parameters_schema",
    "model_visible_writing_parameters",
]
