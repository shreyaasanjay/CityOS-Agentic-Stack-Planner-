"""Configuration for the read-only capability discovery service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tellme_harness.config import ensure_loaded


@dataclass(frozen=True)
class CapabilityServiceConfig:
    mode: str
    bearer_token: Optional[str]
    publisher_bearer_token: Optional[str]
    approved_capabilities_path: str
    principal_token_values: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.mode != "production":
            return
        if not self.bearer_token:
            raise ValueError("CAPABILITY_SERVICE_BEARER_TOKEN is required in production mode.")
        if not self.publisher_bearer_token:
            raise ValueError(
                "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN is required in production mode."
            )

    def get_token_for_env(self, env_name: str) -> Optional[str]:
        direct = self.principal_token_values.get(env_name)
        if direct:
            return direct
        if env_name == "CAPABILITY_SERVICE_BEARER_TOKEN":
            return self.bearer_token
        if env_name == "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN":
            return self.publisher_bearer_token
        return os.getenv(env_name) or None


def get_capability_service_config() -> CapabilityServiceConfig:
    ensure_loaded()
    default_path = Path(__file__).resolve().parent / "approved_capabilities.json"
    config = CapabilityServiceConfig(
        mode=(os.getenv("CAPABILITY_SERVICE_MODE") or "test").strip().lower(),
        bearer_token=os.getenv("CAPABILITY_SERVICE_BEARER_TOKEN") or None,
        publisher_bearer_token=os.getenv("CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN") or None,
        approved_capabilities_path=os.getenv("CAPABILITY_SERVICE_APPROVED_CAPABILITIES_PATH")
        or str(default_path),
        principal_token_values={
            key: value
            for key, value in {
                "CAPABILITY_SERVICE_BEARER_TOKEN": os.getenv("CAPABILITY_SERVICE_BEARER_TOKEN") or "",
                "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN": os.getenv("CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN")
                or "",
            }.items()
            if value
        },
    )
    config.validate()
    return config
