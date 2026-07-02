"""Encoding-tolerant text readers for TraceFix workspace artifacts."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any


_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252")


def safe_read_text(path: str | Path, *, default: str = "") -> str:
    """Read a workspace text file without crashing on common Windows encodings.

    TraceFix workspaces contain user-authored markdown, generated prompts, JSON,
    and TLA files. Most are UTF-8, but pasted Windows text can arrive as CP1252
    (for example byte 0x97 for an em dash). Try the expected encodings first,
    then fall back to replacement characters with a warning.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return default

    encodings = _TEXT_ENCODINGS
    if data.startswith(b"\xef\xbb\xbf"):
        encodings = ("utf-8-sig", "utf-8", "cp1252")

    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "cp1252":
            warnings.warn(
                f"Decoded {path} using CP1252 fallback after UTF-8 failed.",
                UnicodeWarning,
                stacklevel=2,
            )
        return text

    warnings.warn(
        f"Could not decode {path} as UTF-8/UTF-8-BOM/CP1252; using replacement characters.",
        UnicodeWarning,
        stacklevel=2,
    )
    return data.decode("utf-8", errors="replace")


def safe_read_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON through :func:`safe_read_text`.

    JSON syntax errors are still real errors for callers that want strictness;
    this helper is for places that already treated missing/malformed JSON as a
    default value.
    """
    try:
        return json.loads(safe_read_text(path))
    except (OSError, json.JSONDecodeError):
        return default
