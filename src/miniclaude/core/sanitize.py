"""Bounded redaction for checkpoint and trace persistence."""

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

MAX_PERSISTED_DEPTH = 8
MAX_PERSISTED_ITEMS = 100
MAX_PERSISTED_STRING = 8_000
REDACTED = "[REDACTED]"

_SECRET_FIELD = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|key|token|secret|password|authorization|credential|cookie)(?:$|[_-])",
    re.IGNORECASE,
)
_INLINE_PATTERNS = (
    (
        re.compile(r"\bBearer\s+[^\s'\"]+", re.IGNORECASE),
        f"Bearer {REDACTED}",
    ),
    (
        re.compile(
            r"(?P<prefix>\b(?:api[_-]?key|token|secret|password|authorization|credential|cookie)"
            r"\s*[:=]\s*)(?P<value>[^\s,;]+)",
            re.IGNORECASE,
        ),
        rf"\g<prefix>{REDACTED}",
    ),
    (
        re.compile(
            r"(?P<prefix>--?(?:api[-_]?key|token|secret|password|authorization|credential|cookie)"
            r"(?:=|\s+))(?P<value>[^\s]+)",
            re.IGNORECASE,
        ),
        rf"\g<prefix>{REDACTED}",
    ),
    (
        re.compile(
            r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@",
            re.IGNORECASE,
        ),
        rf"\g<scheme>{REDACTED}@",
    ),
)


def _bounded_text(value: str) -> str:
    text = value
    for pattern, replacement in _INLINE_PATTERNS:
        text = pattern.sub(replacement, text)
    marker = "[TRUNCATED]"
    if len(text) > MAX_PERSISTED_STRING:
        text = text[: MAX_PERSISTED_STRING - len(marker)] + marker
    return text


def _sanitize(
    value: object,
    *,
    depth: int,
    seen: set[int],
    field_name: str,
) -> object:
    if _SECRET_FIELD.search(field_name):
        return REDACTED
    if depth > MAX_PERSISTED_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE_NUMBER]"
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, Path):
        return "[ABSOLUTE_PATH]" if value.is_absolute() else value.as_posix()
    if isinstance(value, datetime | date):
        return value.isoformat()

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return "[CYCLE]"
        seen.add(identity)
        try:
            result: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= MAX_PERSISTED_ITEMS:
                    result["[TRUNCATED_ITEMS]"] = True
                    break
                name = _bounded_text(str(key))
                result[name] = _sanitize(
                    item,
                    depth=depth + 1,
                    seen=seen,
                    field_name=name,
                )
            return result
        finally:
            seen.remove(identity)

    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | memoryview):
        identity = id(value)
        if identity in seen:
            return "[CYCLE]"
        seen.add(identity)
        try:
            result = [
                _sanitize(item, depth=depth + 1, seen=seen, field_name=field_name)
                for item in value[:MAX_PERSISTED_ITEMS]
            ]
            if len(value) > MAX_PERSISTED_ITEMS:
                result.append("[TRUNCATED_ITEMS]")
            return result
        finally:
            seen.remove(identity)

    return f"[{type(value).__name__}]"


def sanitize_for_persistence(value: object) -> object:
    """Return a JSON-compatible, bounded copy without obvious credentials."""

    return _sanitize(value, depth=0, seen=set(), field_name="")
