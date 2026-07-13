"""CityOS capability discovery providers and validation.

This layer answers *what a space can do* as privacy-safe metadata only.
Providers may read a deterministic local fixture (tests) or fetch the latest
sanitized capability-app output (production). Providers never inspect CityOS
repositories, manifests, Docker state, or raw sensor artifacts directly.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable
from uuid import uuid4

from .config import CityOSDiscoveryConfig
from .schemas import (
    CityOSCapabilitySnapshot,
    CityOSDiscoveryRequestContext,
    ContextAPICapability,
    PrivacyPolicyCapability,
    SensorCapability,
)

SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1"}
SUPPORTED_MODALITIES = {"video", "radar", "wifi", "audio", "fusion", "context"}
SUPPORTED_CONTEXT_API_DATA_LEVELS = {None, "derived_context", "structured_context", "summary", "metadata"}
PROHIBITED_FIELD_NAMES = {
    "acl",
    "acls",
    "credential",
    "credentials",
    "filesystem_path",
    "internal_acl",
    "media_url",
    "path",
    "person_id",
    "raw_audio_url",
    "raw_video_url",
    "subject_id",
    "token",
    "user_id",
}
PROHIBITED_STRING_SNIPPETS = (
    "file://",
    "rtsp://",
    "s3://",
    "/users/",
    "/var/",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
)
SUPPORTED_CONTEXT_APIS = {
    "cityos_context_lookup",
    "get_acoustic_event_context",
    "get_anonymous_track_context",
    "get_audio_context",
    "get_audio_level_context",
    "get_audio_source_zone_context",
    "get_camera_coverage_metadata",
    "get_camera_occupancy_context",
    "get_context_by_time_window",
    "get_distress_keyword_context",
    "get_entry_event_context",
    "get_event_context",
    "get_exit_event_context",
    "get_impact_sound_context",
    "get_microphone_coverage_metadata",
    "get_motion_context",
    "get_motion_event_context",
    "get_occupancy_context",
    "get_posture_candidate_context",
    "get_radar_context",
    "get_room_state",
    "get_speech_activity_context",
    "get_wifi_context",
}


class CityOSDiscoveryError(RuntimeError):
    """Raised when a capability snapshot is unavailable or violates policy."""


@runtime_checkable
class CityOSDiscoveryProvider(Protocol):
    """Interface for retrieving privacy-safe CityOS capability snapshots."""

    def discover_capabilities(
        self, request_context: CityOSDiscoveryRequestContext
    ) -> CityOSCapabilitySnapshot:  # pragma: no cover - protocol
        ...


# Backward-compatible alias retained for older tests/imports.
CityOSDiscoveryClient = CityOSDiscoveryProvider


def build_discovery_provider(config: CityOSDiscoveryConfig) -> CityOSDiscoveryProvider:
    if config.provider == "mock":
        return MockCityOSDiscoveryProvider(fixture_path=config.fixture_path)
    if config.provider == "http":
        return HttpCityOSDiscoveryClient(config=config)
    raise CityOSDiscoveryError(f"Unsupported discovery provider: {config.provider}")


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
                available=True,
                status="online",
            ),
        ],
        context_apis=[
            ContextAPICapability(
                api_name="cityos_context_lookup",
                modality="context",
                returns_packet_type="general_context_packet",
                available=True,
                data_level="derived_context",
                privacy_level="derived_context",
            ),
            ContextAPICapability(
                api_name="get_room_state",
                modality="fusion",
                returns_packet_type="room_state_packet",
                available=True,
                data_level="derived_context",
                privacy_level="derived_context",
            ),
            ContextAPICapability(
                api_name="get_occupancy_context",
                modality="fusion",
                returns_packet_type="occupancy_context_packet",
                available=True,
                data_level="derived_context",
                privacy_level="derived_context",
            ),
        ],
        privacy_policies=[_default_privacy_policy(space_id)],
        source="mock",
        schema_version="1.1",
    )


class MockCityOSDiscoveryProvider(CityOSDiscoveryProvider):
    """Deterministic offline discovery provider backed by a local JSON fixture."""

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.fixture_path = (
            Path(fixture_path)
            if fixture_path
            else base_dir / "cityos_mock_data" / "cityos_capabilities.json"
        )
        self._fixture = self._load_fixture()

    def _load_fixture(self) -> dict[str, Any]:
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

    def discover_capabilities(
        self, request_context: CityOSDiscoveryRequestContext
    ) -> CityOSCapabilitySnapshot:
        entry = self._fixture.get(request_context.space_id)
        if not isinstance(entry, dict):
            return _default_snapshot(request_context.space_id)
        payload = _sanitize_mock_entry(entry)
        payload["space_id"] = request_context.space_id
        payload.setdefault("schema_version", "1.1")
        payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
        payload.setdefault("snapshot_id", f"cap_{uuid4().hex[:12]}")
        payload["sensors"] = [
            dict(sensor, space_id=request_context.space_id)
            for sensor in payload.get("sensors", [])
            if isinstance(sensor, dict)
        ]
        snapshot = _validate_payload(
            payload,
            expected_space_id=request_context.space_id,
            schema_version=payload.get("schema_version", "1.1"),
            max_capability_count=256,
        )
        return snapshot.model_copy(update={"source": "mock"})


# Backward-compatible alias retained for older imports.
MockCityOSDiscoveryClient = MockCityOSDiscoveryProvider


class HttpCityOSDiscoveryClient(CityOSDiscoveryProvider):
    """Production discovery client that consumes CityOS capability-app output."""

    def __init__(self, *, config: CityOSDiscoveryConfig) -> None:
        self.config = config
        self.last_provenance: dict[str, Any] | None = None

    def discover_capabilities(
        self, request_context: CityOSDiscoveryRequestContext
    ) -> CityOSCapabilitySnapshot:
        payload_bytes, response_headers = self._fetch_with_retries(request_context)
        if len(payload_bytes) > self.config.max_response_bytes:
            raise CityOSDiscoveryError("Capability snapshot exceeded maximum response size.")
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CityOSDiscoveryError("Capability snapshot response was not valid JSON.") from exc
        snapshot = _validate_payload(
            payload,
            expected_space_id=request_context.space_id,
            schema_version=self.config.schema_version,
            max_capability_count=self.config.max_capability_count,
        )
        header_map = _header_map(response_headers)
        privacy_summary = [
            "Structured context only.",
            "Raw media disallowed.",
            "Identity inference disallowed.",
        ]
        self.last_provenance = {
            "discovery_source": header_map.get("x-capability-discovery-source") or "cityos_app",
            "request_id": header_map.get("x-capability-request-id") or request_context.query_id,
            "snapshot_id": header_map.get("x-capability-snapshot-id") or snapshot.snapshot_id,
            "schema_version": header_map.get("x-capability-schema-version") or snapshot.schema_version,
            "capability_count": _coerce_int(
                header_map.get("x-capability-count"),
                len(snapshot.sensors) + len(snapshot.context_apis),
            ),
            "validation_outcome": header_map.get("x-capability-validation-outcome") or "accepted",
            "policy_version": header_map.get("x-capability-policy-version"),
            "capability_state_hash": header_map.get("x-capability-state-hash"),
            "grant_state_hash": header_map.get("x-capability-grant-state-hash"),
            "privacy_policy_state_hash": header_map.get("x-capability-privacy-policy-state-hash"),
            "production_discovery_used": self.config.provider == "http" and self.config.mode == "production",
            "privacy_summary": privacy_summary,
        }
        return snapshot.model_copy(update={"source": "cityos_app"})

    def _fetch_with_retries(
        self, request_context: CityOSDiscoveryRequestContext
    ) -> tuple[bytes, Mapping[str, str] | Any]:
        last_error: Exception | None = None
        for _attempt in range(self.config.retry_count + 1):
            try:
                return self._fetch_once(request_context)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                last_error = exc
        raise CityOSDiscoveryError("Capability discovery unavailable.") from last_error

    def _fetch_once(
        self, request_context: CityOSDiscoveryRequestContext
    ) -> tuple[bytes, Mapping[str, str] | Any]:
        url = self.config.build_query_url()
        encoded_request = json.dumps(request_context.model_dump()).encode("utf-8")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "SmartRoomStack/TeLLMe-CityOSDiscovery",
                **(
                    {"Authorization": "Bearer " + self.config.bearer_token}
                    if self.config.bearer_token
                    else {}
                ),
            },
            data=encoded_request,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            response_body = response.read(self.config.max_response_bytes + 1)
            response_headers = getattr(response, "headers", None)
            if response_headers is None and hasattr(response, "info"):
                response_headers = response.info()
            return response_body, response_headers or {}


def _header_map(headers: Mapping[str, str] | Any) -> dict[str, str]:
    if isinstance(headers, Mapping):
        return {str(key).lower(): str(value) for key, value in headers.items()}
    items = getattr(headers, "items", None)
    if callable(items):
        return {str(key).lower(): str(value) for key, value in items()}
    return {}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _validate_payload(
    payload: Any,
    *,
    expected_space_id: str,
    schema_version: str,
    max_capability_count: int,
) -> CityOSCapabilitySnapshot:
    if not isinstance(payload, dict):
        raise CityOSDiscoveryError("Capability snapshot must be a JSON object.")

    _scan_for_prohibited_content(payload)

    snapshot_schema_version = payload.get("schema_version")
    if snapshot_schema_version != schema_version:
        raise CityOSDiscoveryError(
            f"Unsupported capability snapshot schema version: {snapshot_schema_version!r}."
        )
    if snapshot_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise CityOSDiscoveryError("Capability snapshot schema version is not supported by this harness.")

    if payload.get("space_id") != expected_space_id:
        raise CityOSDiscoveryError("Capability snapshot space_id did not match the requested space.")

    _validate_timestamp(payload.get("generated_at"), field_name="generated_at")

    sensors = payload.get("sensors", [])
    context_apis = payload.get("context_apis", [])
    if not isinstance(sensors, list) or not isinstance(context_apis, list):
        raise CityOSDiscoveryError("Capability snapshot sensors/context_apis must be arrays.")
    if len(sensors) + len(context_apis) > max_capability_count:
        raise CityOSDiscoveryError("Capability snapshot exceeded maximum capability count.")

    sensor_ids: set[str] = set()
    for sensor in sensors:
        if not isinstance(sensor, dict):
            raise CityOSDiscoveryError("Every sensor capability must be an object.")
        sensor_id = sensor.get("sensor_id")
        if not isinstance(sensor_id, str) or not sensor_id.strip():
            raise CityOSDiscoveryError("Every sensor capability must declare a non-empty sensor_id.")
        if sensor_id in sensor_ids:
            raise CityOSDiscoveryError("Duplicate sensor_id found in capability snapshot.")
        sensor_ids.add(sensor_id)
        modality = sensor.get("modality")
        if modality not in SUPPORTED_MODALITIES:
            raise CityOSDiscoveryError(f"Unsupported sensor modality: {modality!r}.")
        if "space_id" in sensor and sensor["space_id"] != expected_space_id:
            raise CityOSDiscoveryError("Sensor capability declared a mismatched space_id.")
        for api_name in sensor.get("restricted_api_names", []) or []:
            if api_name in SUPPORTED_CONTEXT_APIS:
                raise CityOSDiscoveryError("Restricted raw/internal APIs must not appear as supported public APIs.")

    api_names: set[str] = set()
    for api in context_apis:
        if not isinstance(api, dict):
            raise CityOSDiscoveryError("Every context API capability must be an object.")
        api_name = api.get("api_name")
        if api_name not in SUPPORTED_CONTEXT_APIS:
            raise CityOSDiscoveryError(f"Unsupported context API in capability snapshot: {api_name!r}.")
        if api_name in api_names:
            raise CityOSDiscoveryError("Duplicate api_name found in capability snapshot.")
        api_names.add(api_name)
        modality = api.get("modality", "context")
        if modality not in SUPPORTED_MODALITIES:
            raise CityOSDiscoveryError(f"Unsupported context API modality: {modality!r}.")
        if api.get("data_level") not in SUPPORTED_CONTEXT_API_DATA_LEVELS:
            raise CityOSDiscoveryError("Unsupported context API data_level in capability snapshot.")
        if api.get("raw_access") is True:
            raise CityOSDiscoveryError("Raw-access APIs are prohibited in the privacy-safe capability snapshot.")
        for owner_sensor_id in api.get("owner_sensor_ids", []) or []:
            if owner_sensor_id not in sensor_ids:
                raise CityOSDiscoveryError("Context API referenced an unknown owner_sensor_id.")

    policies = payload.get("privacy_policies", [])
    if not isinstance(policies, list):
        raise CityOSDiscoveryError("Capability snapshot privacy_policies must be an array.")
    for policy in policies:
        if not isinstance(policy, dict):
            raise CityOSDiscoveryError("Every privacy policy entry must be an object.")
        if policy.get("raw_sensor_access_allowed") is True:
            raise CityOSDiscoveryError("Capability snapshot may not authorize raw sensor access.")
        if policy.get("identity_inference_allowed") is True:
            raise CityOSDiscoveryError("Capability snapshot may not authorize person-level identity inference.")

    try:
        return CityOSCapabilitySnapshot(**payload)
    except Exception as exc:  # noqa: BLE001
        raise CityOSDiscoveryError("Capability snapshot failed strict schema validation.") from exc


def _scan_for_prohibited_content(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower()
            if normalized_key in PROHIBITED_FIELD_NAMES:
                raise CityOSDiscoveryError(f"Capability snapshot contained prohibited field: {path}.{key}")
            _scan_for_prohibited_content(nested, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_for_prohibited_content(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(snippet in lowered for snippet in PROHIBITED_STRING_SNIPPETS):
            raise CityOSDiscoveryError(f"Capability snapshot contained prohibited string content at {path}.")


def _validate_timestamp(value: Any, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CityOSDiscoveryError(f"Capability snapshot field {field_name} must be a non-empty timestamp string.")
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CityOSDiscoveryError(f"Capability snapshot field {field_name} was not valid ISO-8601.") from exc


def _sanitize_mock_entry(entry: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(entry))
    payload["context_apis"] = [
        api
        for api in payload.get("context_apis", [])
        if isinstance(api, dict) and not api.get("raw_access", False)
    ]
    for sensor in payload.get("sensors", []):
        if isinstance(sensor, dict):
            sensor.pop("space_id", None)
    return payload
