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
DEFAULT_OPENAI_TIMEOUT_SECONDS = 120

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
    timeout_raw = (
        os.getenv("OPENAI_TIMEOUT_SECONDS")
        or os.getenv("TELLME_LLM_TIMEOUT_SECONDS")
        or str(DEFAULT_OPENAI_TIMEOUT_SECONDS)
    )
    try:
        timeout_seconds = int(timeout_raw)
    except ValueError:
        timeout_seconds = DEFAULT_OPENAI_TIMEOUT_SECONDS
    return LLMConfig(api_key=api_key, model=model, base_url=base_url, timeout_seconds=timeout_seconds)


@dataclass(frozen=True)
class CityOSDiscoveryConfig:
    mode: str
    provider: str
    fixture_path: Optional[str]
    service_url: Optional[str]
    bearer_token: Optional[str]
    timeout_seconds: int
    retry_count: int
    schema_version: str
    max_response_bytes: int
    max_capability_count: int

    def build_query_url(self) -> str:
        if not self.service_url:
            raise ValueError(
                "CityOS discovery service URL is not configured. "
                "Set CITYOS_DISCOVERY_SERVICE_URL."
            )
        return self.service_url.rstrip("/")

    def validate_production(self) -> None:
        if self.mode != "production":
            return
        if self.provider == "http":
            if not self.service_url:
                raise ValueError("CITYOS_DISCOVERY_SERVICE_URL is required in production http mode.")
            if not self.bearer_token:
                raise ValueError("CITYOS_DISCOVERY_BEARER_TOKEN is required in production http mode.")


def get_cityos_discovery_config() -> CityOSDiscoveryConfig:
    ensure_loaded()
    mode = (os.getenv("CITYOS_DISCOVERY_MODE") or "test").strip().lower()
    provider = (os.getenv("CITYOS_DISCOVERY_PROVIDER") or ("mock" if mode == "test" else "http")).strip().lower()
    fixture_path = os.getenv("CITYOS_DISCOVERY_FIXTURE_PATH") or None
    service_url = os.getenv("CITYOS_DISCOVERY_SERVICE_URL") or None
    bearer_token = os.getenv("CITYOS_DISCOVERY_BEARER_TOKEN") or None
    try:
        timeout_seconds = int(os.getenv("CITYOS_DISCOVERY_TIMEOUT_SECONDS", "5"))
    except ValueError:
        timeout_seconds = 5
    try:
        retry_count = int(os.getenv("CITYOS_DISCOVERY_RETRY_COUNT", "1"))
    except ValueError:
        retry_count = 1
    try:
        max_response_bytes = int(os.getenv("CITYOS_DISCOVERY_MAX_RESPONSE_BYTES", "250000"))
    except ValueError:
        max_response_bytes = 250000
    try:
        max_capability_count = int(os.getenv("CITYOS_DISCOVERY_MAX_CAPABILITY_COUNT", "256"))
    except ValueError:
        max_capability_count = 256
    schema_version = (os.getenv("CITYOS_DISCOVERY_SCHEMA_VERSION") or "1.1").strip()
    config = CityOSDiscoveryConfig(
        mode=mode,
        provider=provider,
        fixture_path=fixture_path,
        service_url=service_url,
        bearer_token=bearer_token,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        schema_version=schema_version,
        max_response_bytes=max_response_bytes,
        max_capability_count=max_capability_count,
    )
    config.validate_production()
    return config
