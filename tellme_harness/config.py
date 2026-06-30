"""Configuration + lightweight ``.env`` loading for the TeLLMe harness.

No third-party dependency is introduced: this is a minimal ``KEY=VALUE`` parser
(comments, blank lines, optional ``export`` prefix, and quoted values supported).
Real values are read from ``TELLME_API_KEY`` / ``OPENAI_API_KEY`` and
``TELLME_MODEL`` / ``OPENAI_MODEL``. They are never written back to disk,
logs, prompts, or answer artifacts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

_loaded = False


def parse_env_file(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Dict[str, str]:
    """Load a ``.env`` file into ``os.environ``. Existing vars win unless override.

    Returns the parsed mapping (whether or not it was applied to the environment).
    Missing file is not an error.
    """
    env_path = Path(path) if path else _PROJECT_ROOT / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    parsed = parse_env_file(text)
    for key, value in parsed.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def ensure_loaded() -> None:
    """Load the project ``.env`` once per process (idempotent)."""
    global _loaded
    if _loaded:
        return
    load_dotenv()
    _loaded = True


@dataclass(frozen=True)
class LLMConfig:
    api_key: Optional[str]
    model: str
    base_url: str
    timeout_seconds: int

    @property
    def has_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def get_llm_config() -> LLMConfig:
    """Resolve LLM configuration from the environment (after loading ``.env``)."""
    ensure_loaded()
    tellme_key = (os.getenv("TELLME_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    api_key = tellme_key or openai_key or None
    model = os.getenv("TELLME_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    base_url = os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL
    try:
        timeout_seconds = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    except ValueError:
        timeout_seconds = 30
    return LLMConfig(api_key=api_key, model=model, base_url=base_url, timeout_seconds=timeout_seconds)
