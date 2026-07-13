"""Configuration loading for the external CityOS capability bridge."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class BridgeConfigError(ValueError):
    """Raised when the bridge configuration is incomplete or invalid."""


@dataclass(frozen=True)
class BridgeConfig:
    snapshot_path: Path
    capability_service_base_url: str
    publisher_bearer_token: str
    timeout_seconds: float = 5.0
    retry_count: int = 1
    dry_run: bool = False

    def validate(self) -> None:
        if not str(self.snapshot_path).strip():
            raise BridgeConfigError("CITYOS_CAPABILITY_SNAPSHOT_PATH is required.")
        if not self.capability_service_base_url.strip():
            raise BridgeConfigError("CAPABILITY_SERVICE_BASE_URL is required.")
        if not self.publisher_bearer_token.strip():
            raise BridgeConfigError("CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN is required.")
        if self.timeout_seconds <= 0:
            raise BridgeConfigError("BRIDGE_TIMEOUT_SECONDS must be positive.")
        if self.retry_count < 0:
            raise BridgeConfigError("BRIDGE_RETRY_COUNT must be zero or greater.")


def load_bridge_config(argv: Optional[list[str]] = None) -> BridgeConfig:
    parser = argparse.ArgumentParser(description="Publish a sanitized CityOS capability snapshot.")
    parser.add_argument("--snapshot-path", default=os.getenv("CITYOS_CAPABILITY_SNAPSHOT_PATH"))
    parser.add_argument("--base-url", default=os.getenv("CAPABILITY_SERVICE_BASE_URL"))
    parser.add_argument(
        "--bearer-token",
        default=os.getenv("CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN"),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("BRIDGE_TIMEOUT_SECONDS") or "5"),
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=int(os.getenv("BRIDGE_RETRY_COUNT") or "1"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=_env_flag("BRIDGE_DRY_RUN"),
    )
    args = parser.parse_args(argv)
    config = BridgeConfig(
        snapshot_path=Path(args.snapshot_path or ""),
        capability_service_base_url=(args.base_url or "").rstrip("/"),
        publisher_bearer_token=args.bearer_token or "",
        timeout_seconds=args.timeout_seconds,
        retry_count=args.retry_count,
        dry_run=bool(args.dry_run),
    )
    config.validate()
    return config


def _env_flag(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}
