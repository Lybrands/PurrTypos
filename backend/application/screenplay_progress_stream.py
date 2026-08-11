"""Incrementally project approved JSON string fields into a visible work log."""

from __future__ import annotations

from collections.abc import Mapping


_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_STRING_CLOSED = object()
VISIBLE_STREAM_CHUNK_CHARS = 1


def visible_stream_chunks(
    value: object,
    *,
    max_chars: int = VISIBLE_STREAM_CHUNK_CHARS,
) -> tuple[str, ...]:
    """Bound public text deltas without inventing content or timing."""

    text = str(value or "")
    size = max(1, int(max_chars))
    return tuple(text[index:index + size] for index in range(0, len(text), size))


def visible_execution_progress(value: object) -> str:
    """Keep public work logs concise and reject artifact-shaped content."""

    text = " ".join(str(value or "").split())
    if not text or any(marker in text for marker in (
        "```",
        "{",
        "}",
        "sceneText",
        "contentText",
        "contentJson",
    )):
        return ""
    return text[:600]


class JsonStringFieldProjector:
    """Read selected JSON string values without exposing the JSON envelope."""

    def __init__(
        self,
        prefixes: Mapping[str, str],
        *,
        max_value_chars: int = 600,
    ) -> None:
        self._prefixes = dict(prefixes)
        self._max_value_chars = max(1, int(max_value_chars))
        self._in_string = False
        self._escaped = False
        self._unicode_digits: str | None = None
        self._scanned: list[str] = []
        self._candidate_key: str | None = None
        self._value_key: str | None = None
        self._capturing_key: str | None = None
        self._captured_chars = 0

    def feed(self, fragment: str) -> str:
        output: list[str] = []
        for char in fragment:
            if self._in_string:
                decoded = self._string_char(char)
                if decoded is _STRING_CLOSED:
                    self._in_string = False
                    if self._capturing_key is not None:
                        if self._captured_chars > 0:
                            output.append("\n")
                        self._capturing_key = None
                        self._captured_chars = 0
                    else:
                        scanned = "".join(self._scanned)
                        self._candidate_key = (
                            scanned if scanned in self._prefixes else None
                        )
                    self._scanned.clear()
                elif isinstance(decoded, str):
                    if self._capturing_key is not None:
                        if self._captured_chars < self._max_value_chars:
                            safe = _safe_visible_char(decoded)
                            if safe:
                                if self._captured_chars == 0:
                                    output.append(
                                        self._prefixes[self._capturing_key]
                                    )
                                output.append(safe)
                                self._captured_chars += len(safe)
                    elif len(self._scanned) <= 64:
                        self._scanned.append(decoded)
                continue

            if self._candidate_key is not None:
                if char.isspace():
                    continue
                candidate = self._candidate_key
                self._candidate_key = None
                if char == ":":
                    self._value_key = candidate
                    continue

            if self._value_key is not None:
                if char.isspace():
                    continue
                value_key = self._value_key
                self._value_key = None
                if char == '"':
                    self._begin_string(capturing_key=value_key)
                    continue

            if char == '"':
                self._begin_string()
        return "".join(output)

    def _begin_string(self, *, capturing_key: str | None = None) -> None:
        self._in_string = True
        self._escaped = False
        self._unicode_digits = None
        self._scanned.clear()
        self._capturing_key = capturing_key
        self._captured_chars = 0

    def _string_char(self, char: str) -> str | object | None:
        if self._unicode_digits is not None:
            self._unicode_digits += char
            if len(self._unicode_digits) < 4:
                return None
            digits = self._unicode_digits
            self._unicode_digits = None
            try:
                return chr(int(digits, 16))
            except ValueError:
                return ""
        if self._escaped:
            self._escaped = False
            if char == "u":
                self._unicode_digits = ""
                return None
            return _ESCAPES.get(char, char)
        if char == "\\":
            self._escaped = True
            return None
        if char == '"':
            return _STRING_CLOSED
        return char


def _safe_visible_char(value: str) -> str:
    if value in {"{", "}", "`"}:
        return ""
    if value in {"\b", "\f", "\r", "\t"}:
        return " "
    return value


__all__ = [
    "JsonStringFieldProjector",
    "VISIBLE_STREAM_CHUNK_CHARS",
    "visible_execution_progress",
    "visible_stream_chunks",
]
