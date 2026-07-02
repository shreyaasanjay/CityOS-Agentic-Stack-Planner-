"""CityOS capability discovery.

This layer answers *what a space can do* — its sensors, structured-context APIs,
and governing privacy policy — as metadata. It is deliberately separate from
``cityos_mock.MockCityOSClient``, which serves runtime *evidence values* (counts,
noise levels, …). Capability metadata may inform planning; runtime values must
not, since those belong to harness execution under TraceFix.

``CityOSDiscoveryClient`` is the protocol a real CityOS discovery integration
would implement later. ``MockCityOSDiscoveryClient`` reads a JSON fixture and
falls back to a built-in default snapshot so the harness stays runnable without
external services.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable
from uuid import uuid4

from .schemas import (
    CityOSCapabilitySnapshot,
    ContextAPICapability,
    PrivacyPolicyCapability,
    SensorCapability,
)


@runtime_checkable
class CityOSDiscoveryClient(Protocol):
    """Interface for discovering a space's CityOS capabilities."""

    def discover(self, space_id: str) -> CityOSCapabilitySnapshot:  # pragma: no cover - protocol
        ...


def _default_privacy_policy(space_id: str) -> PrivacyPolicyCapability:
    return PrivacyPolicyCapability(
        policy_id=f"{space_id}_default",
        privacy_scope="cityos_structured_context_only",
        raw_sensor_access_allowed=False,
        identity_inference_allowed=False,
        forbidden_inferences=[
            "raw_sensor_access",
            "face_identity",
            "personal_identity",
            "medical_diagnosis",
            "unsupported_behavioral_inference",
        ],
        notes=["Only CityOS structured context is exposed; no raw sensor artifacts."],
    )


def _default_snapshot(space_id: str) -> CityOSCapabilitySnapshot:
    """A minimal but coherent snapshot used when no fixture entry exists."""
    return CityOSCapabilitySnapshot(
        snapshot_id=f"cap_{uuid4().hex[:12]}",
        space_id=space_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        sensors=[
            SensorCapability(
                sensor_id="fusion_room_state_01",
                modality="fusion",
                space_id=space_id,
                description="Fused room-state summary.",
                supported_context_types=["room_state", "occupancy", "motion"],
            ),
        ],
        context_apis=[
            ContextAPICapability(api_name="cityos_context_lookup", modality="context", returns_packet_type="general_context_packet"),
            ContextAPICapability(api_name="get_room_state", modality="fusion", returns_packet_type="room_state_packet"),
            ContextAPICapability(api_name="get_occupancy_context", modality="fusion", returns_packet_type="occupancy_context_packet"),
        ],
        privacy_policies=[_default_privacy_policy(space_id)],
        source="mock",
    )


class MockCityOSDiscoveryClient(CityOSDiscoveryClient):
    """Fixture-backed discovery client.

    Loads ``cityos_mock_data/cityos_capabilities.json`` (a mapping of
    ``space_id`` -> capability payload). Unknown spaces get a built-in default
    snapshot rather than an error, so the planner always has *some* capability
    grounding to work from.
    """

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.fixture_path = (
            Path(fixture_path)
            if fixture_path
            else base_dir / "cityos_mock_data" / "cityos_capabilities.json"
        )
        self._fixture = self._load_fixture()

    def _load_fixture(self) -> dict:
        try:
            raw_text = self.fixture_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"CityOS capability fixture at {self.fixture_path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"CityOS capability fixture at {self.fixture_path} must be a JSON object keyed by space_id."
            )
        return payload

    def discover(self, space_id: str) -> CityOSCapabilitySnapshot:
        entry = self._fixture.get(space_id)
        if not isinstance(entry, dict):
            return _default_snapshot(space_id)
        return _snapshot_from_entry(space_id=space_id, entry=entry)


def _snapshot_from_entry(*, space_id: str, entry: dict) -> CityOSCapabilitySnapshot:
    sensors = [
        SensorCapability(space_id=space_id, **_sensor_kwargs(item))
        for item in entry.get("sensors", [])
        if isinstance(item, dict)
    ]
    context_apis = [
        ContextAPICapability(**item)
        for item in entry.get("context_apis", [])
        if isinstance(item, dict)
    ]
    privacy_policies = [
        PrivacyPolicyCapability(**item)
        for item in entry.get("privacy_policies", [])
        if isinstance(item, dict)
    ] or [_default_privacy_policy(space_id)]
    return CityOSCapabilitySnapshot(
        snapshot_id=entry.get("snapshot_id") or f"cap_{uuid4().hex[:12]}",
        space_id=space_id,
        generated_at=entry.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        sensors=sensors,
        context_apis=context_apis,
        privacy_policies=privacy_policies,
        source=entry.get("source", "mock"),
        schema_version=entry.get("schema_version", "1.0"),
    )


def _sensor_kwargs(item: dict) -> dict:
    # space_id is injected by the caller; ignore any fixture-provided value so a
    # snapshot cannot claim sensors belong to a different space.
    return {key: value for key, value in item.items() if key != "space_id"}
