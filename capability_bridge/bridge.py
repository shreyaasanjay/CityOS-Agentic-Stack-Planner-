"""Validated external bridge from CityOS capability publisher artifacts to capability_service."""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from capability_service.schemas import CapabilityRegistrationResult, CapabilityStatusUpdateRequest
from tellme_harness.schemas import CityOSCapabilitySnapshot

from .config import BridgeConfig

LOGGER = logging.getLogger(__name__)

SAFE_SUMMARY_FIELDS = ("snapshot_id", "space_id", "schema_version", "capability_count")
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
PROHIBITED_KEY_SNIPPETS = (
    "endpoint",
    "filesystem_path",
    "manifest",
    "path",
    "stream",
    "tracking_id",
    "track_id",
    "url",
    "workspace",
)
PROHIBITED_VALUE_SNIPPETS = (
    "file://",
    "http://",
    "https://",
    "rtsp://",
    "/users/",
    "/var/",
    ".avi",
    ".mov",
    ".mp4",
    ".wav",
)
PROHIBITED_METADATA_SNIPPETS = (
    "face_identity",
    "person_id",
    "person_identifier",
    "speaker_identity",
    "tracking_id",
    "track_id",
    "transcript",
)
SENSOR_MODALITY_MAP = {
    "audio-derived": "audio",
    "context-derived": "context",
    "fusion-derived": "fusion",
    "multi-sensor-derived": "fusion",
    "radar-derived": "radar",
    "vision-derived": "video",
    "wireless-derived": "wifi",
}
OUTPUT_CONTEXT_TYPE_MAP = {
    "entry_event": ["events"],
    "motion_disturbance_state": ["motion"],
    "occupancy_state": ["occupancy", "room_state"],
    "room_state": ["room_state", "occupancy", "motion"],
    "sound_event_candidate": ["audio", "events"],
    "stillness_estimate": ["motion"],
}
OUTPUT_API_NAME_MAP = {
    "motion_disturbance_state": "get_wifi_context",
    "occupancy_state": "get_occupancy_context",
    "room_state": "get_room_state",
    "sound_event_candidate": "get_audio_context",
    "stillness_estimate": "get_radar_context",
}
CAPABILITY_ID_API_NAME_MAP = {
    "radar_stillness_estimate": "get_radar_context",
    "room_occupancy_state": "get_occupancy_context",
    "wifi_motion_disturbance": "get_wifi_context",
}
API_PACKET_TYPE_MAP = {
    "get_audio_context": "audio_context_packet",
    "get_occupancy_context": "occupancy_context_packet",
    "get_radar_context": "radar_context_packet",
    "get_room_state": "room_state_packet",
    "get_wifi_context": "wifi_context_packet",
}


class BridgeRuntimeError(RuntimeError):
    """Raised when bridge execution fails closed."""


class CityOSPublisherSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publisher: str
    origin: str
    approval_ref: Optional[str] = None


class CityOSPublisherPrivacyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    summary: Optional[str] = None
    rules: list[str] = Field(default_factory=list)


class CityOSPublisherCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    space_id: str
    modality: str
    data_level: Literal["derived", "context"]
    raw_access: bool = False
    outputs: list[str] = Field(default_factory=list)
    privacy_constraints: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    availability: Literal["available", "unavailable", "degraded", "stale"] = "available"
    status: str


class CityOSPublisherSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cityos-capability-declaration/v1"]
    snapshot_id: str
    space_id: str
    generated_at: str
    deployment_id: str
    privacy_policies: list[CityOSPublisherPrivacyPolicy] = Field(default_factory=list)
    source: CityOSPublisherSource
    sensors: list[CityOSPublisherCapability] = Field(default_factory=list)
    context_apis: list[CityOSPublisherCapability] = Field(default_factory=list)


@dataclass(frozen=True)
class BridgeRunResult:
    snapshot_id: str
    space_id: str
    schema_version: str
    capability_count: int
    dry_run: bool
    registration_result: Optional[dict[str, Any]]
    status_update_count: int = 0


class CapabilityBridge:
    def __init__(
        self,
        config: BridgeConfig,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self._urlopen = urlopen
        self._logger = logger or LOGGER

    def run(self) -> BridgeRunResult:
        artifact = self._load_artifact(self.config.snapshot_path)
        adapted = _adapt_snapshot(artifact)
        capability_count = len(adapted.sensors) + len(adapted.context_apis)
        self._log_safe(
            "bridge.validation_succeeded",
            snapshot_id=adapted.snapshot_id,
            space_id=adapted.space_id,
            schema_version=adapted.schema_version,
            capability_count=capability_count,
            destination=_destination_label(self.config.capability_service_base_url),
            mode="dry_run" if self.config.dry_run else "live",
        )

        if self.config.dry_run:
            return BridgeRunResult(
                snapshot_id=adapted.snapshot_id,
                space_id=adapted.space_id,
                schema_version=adapted.schema_version,
                capability_count=capability_count,
                dry_run=True,
                registration_result=None,
                status_update_count=0,
            )

        registration = self._post_json(
            "/v1/capabilities/register",
            adapted.model_dump(),
            request_model=CityOSCapabilitySnapshot,
            response_model=CapabilityRegistrationResult,
        )
        status_update_count = 0
        for capability_id, status_update in _build_status_updates(artifact):
            self._post_json(
                f"/v1/capabilities/{capability_id}/status",
                status_update.model_dump(),
                request_model=CapabilityStatusUpdateRequest,
            )
            status_update_count += 1

        self._log_safe(
            "bridge.publish_succeeded",
            snapshot_id=adapted.snapshot_id,
            space_id=adapted.space_id,
            schema_version=adapted.schema_version,
            capability_count=capability_count,
            status_update_count=status_update_count,
            destination=_destination_label(self.config.capability_service_base_url),
            mode="live",
        )
        return BridgeRunResult(
            snapshot_id=adapted.snapshot_id,
            space_id=adapted.space_id,
            schema_version=adapted.schema_version,
            capability_count=capability_count,
            dry_run=False,
            registration_result=registration,
            status_update_count=status_update_count,
        )

    def _load_artifact(self, path: Path) -> CityOSPublisherSnapshot:
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise BridgeRuntimeError(f"Snapshot artifact does not exist: {path}") from exc
        try:
            raw_payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise BridgeRuntimeError(f"Snapshot artifact is not valid JSON: {path}") from exc
        _assert_safe_payload(raw_payload)
        try:
            artifact = CityOSPublisherSnapshot.model_validate(raw_payload)
        except Exception as exc:
            raise BridgeRuntimeError("Snapshot artifact does not match the approved contract.") from exc
        _validate_artifact_contents(artifact)
        return artifact

    def _post_json(
        self,
        endpoint_path: str,
        payload: dict[str, Any],
        *,
        request_model: type[BaseModel],
        response_model: Optional[type[BaseModel]] = None,
    ) -> dict[str, Any]:
        request_body = json.dumps(payload, sort_keys=True).encode("utf-8")
        url = f"{self.config.capability_service_base_url}{endpoint_path}"
        request = urllib.request.Request(
            url,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.publisher_bearer_token}",
                "Content-Type": "application/json",
            },
        )
        request_model.model_validate(payload)
        last_error: Optional[Exception] = None
        for attempt in range(self.config.retry_count + 1):
            try:
                with self._urlopen(request, timeout=self.config.timeout_seconds) as response:
                    status_code = getattr(response, "status", 200)
                    body = response.read()
                if not 200 <= status_code < 300:
                    raise BridgeRuntimeError(f"Capability service returned HTTP {status_code}.")
                if response_model is None:
                    return {}
                if not body:
                    return {}
                parsed = json.loads(body.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise BridgeRuntimeError("Capability service returned a non-object JSON body.")
                response_model.model_validate(parsed)
                return parsed
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in TRANSIENT_STATUS_CODES or attempt >= self.config.retry_count:
                    raise BridgeRuntimeError(
                        f"Capability service request failed with HTTP {exc.code}."
                    ) from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt >= self.config.retry_count:
                    raise BridgeRuntimeError("Capability service request failed after retries.") from exc
        raise BridgeRuntimeError("Capability service request failed.") from last_error

    def _log_safe(self, message: str, **fields: Any) -> None:
        safe_fields = {key: value for key, value in fields.items() if key not in {"token", "authorization"}}
        self._logger.info("%s %s", message, json.dumps(safe_fields, sort_keys=True))


def _assert_safe_payload(payload: Any) -> None:
    def _walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered_key = str(key).lower()
                if any(snippet in lowered_key for snippet in PROHIBITED_KEY_SNIPPETS):
                    raise BridgeRuntimeError(f"Prohibited field '{path + '.' if path else ''}{key}' present.")
                _walk(nested, f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, list):
            for index, nested in enumerate(value):
                _walk(nested, f"{path}[{index}]")
            return
        if isinstance(value, str):
            lowered = value.lower()
            if any(snippet in lowered for snippet in PROHIBITED_VALUE_SNIPPETS):
                raise BridgeRuntimeError(f"Prohibited value found at '{path}'.")

    _walk(payload, "")


def _validate_artifact_contents(artifact: CityOSPublisherSnapshot) -> None:
    for collection_name, capabilities in (
        ("sensors", artifact.sensors),
        ("context_apis", artifact.context_apis),
    ):
        for capability in capabilities:
            if capability.space_id != artifact.space_id:
                raise BridgeRuntimeError(
                    f"{collection_name} capability '{capability.capability_id}' has mismatched space_id."
                )
            if capability.raw_access:
                raise BridgeRuntimeError(
                    f"{collection_name} capability '{capability.capability_id}' requests raw access."
                )
            _assert_safe_metadata(capability.capability_id, label="capability_id")
            _assert_safe_metadata(capability.status, label="status")
            for value in capability.outputs:
                _assert_safe_metadata(value, label="output")
            for value in capability.privacy_constraints:
                _assert_safe_metadata(value, label="privacy_constraint")
            for value in capability.limitations:
                _assert_safe_metadata(value, label="limitation")


def _assert_safe_metadata(value: str, *, label: str) -> None:
    lowered = value.lower()
    if _is_negated_metadata(lowered):
        return
    if any(snippet in lowered for snippet in PROHIBITED_METADATA_SNIPPETS):
        raise BridgeRuntimeError(f"Prohibited {label} value '{value}' detected.")


def _is_negated_metadata(value: str) -> bool:
    prefixes = ("no_", "no ", "not ", "without_", "without ")
    return any(value.startswith(prefix) for prefix in prefixes)


def _adapt_snapshot(artifact: CityOSPublisherSnapshot) -> CityOSCapabilitySnapshot:
    return CityOSCapabilitySnapshot(
        snapshot_id=artifact.snapshot_id,
        space_id=artifact.space_id,
        generated_at=artifact.generated_at,
        deployment_id=artifact.deployment_id,
        sensors=[_adapt_sensor(item) for item in artifact.sensors],
        context_apis=[_adapt_context_api(item) for item in artifact.context_apis],
        privacy_policies=[_adapt_privacy_policy(item) for item in artifact.privacy_policies],
        source="cityos_app",
        schema_version="1.1",
    )


def _adapt_sensor(item: CityOSPublisherCapability) -> dict[str, Any]:
    modality = _normalize_modality(item.modality)
    related_api_names = sorted(
        {
            api_name
            for output in item.outputs
            for api_name in [_map_output_to_api_name(output)]
            if api_name is not None
        }
    )
    return {
        "sensor_id": item.capability_id,
        "modality": modality,
        "space_id": item.space_id,
        "description": item.status,
        "available": item.availability == "available",
        "status": item.availability,
        "supported_context_types": _map_outputs_to_context_types(item.outputs),
        "limitations": list(item.limitations),
        "supported_capabilities": list(item.outputs),
        "unsupported_capabilities": [],
        "allowed_api_names": related_api_names,
        "restricted_api_names": [],
    }


def _adapt_context_api(item: CityOSPublisherCapability) -> dict[str, Any]:
    api_name = _resolve_context_api_name(item)
    return {
        "api_name": api_name,
        "description": item.status,
        "modality": _normalize_modality(item.modality),
        "returns_packet_type": API_PACKET_TYPE_MAP[api_name],
        "requires_privacy_scope": "cityos_structured_context_only",
        "available": item.availability == "available",
        "data_level": "derived_context" if item.data_level == "derived" else "structured_context",
        "required_arguments": ["space_id", "timestamp"],
        "supported_time_query_modes": ["latest", "point_in_time"],
        "privacy_level": "derived_context",
        "raw_access": False,
        "limitations": list(item.limitations),
        "owner_sensor_ids": [],
    }


def _adapt_privacy_policy(item: CityOSPublisherPrivacyPolicy) -> dict[str, Any]:
    notes = [item.summary] if item.summary else []
    notes.extend(f"rule:{rule}" for rule in item.rules)
    return {
        "policy_id": item.policy_id,
        "privacy_scope": "cityos_structured_context_only",
        "raw_sensor_access_allowed": False,
        "identity_inference_allowed": False,
        "forbidden_inferences": [
            "raw_sensor_access",
            "personal_identity",
            "speaker_identity",
            "tracking_identifier",
        ],
        "notes": notes,
    }


def _build_status_updates(
    artifact: CityOSPublisherSnapshot,
) -> list[tuple[str, CapabilityStatusUpdateRequest]]:
    updates: list[tuple[str, CapabilityStatusUpdateRequest]] = []
    for capability in artifact.sensors:
        updates.append(
            (
                f"sensor:{capability.capability_id}",
                CapabilityStatusUpdateRequest(
                    available=capability.availability == "available",
                    last_status_at=artifact.generated_at,
                ),
            )
        )
    for capability in artifact.context_apis:
        updates.append(
            (
                f"api:{_resolve_context_api_name(capability)}",
                CapabilityStatusUpdateRequest(
                    available=capability.availability == "available",
                    last_status_at=artifact.generated_at,
                ),
            )
        )
    return updates


def _normalize_modality(value: str) -> str:
    normalized = SENSOR_MODALITY_MAP.get(value.lower())
    if normalized is None:
        raise BridgeRuntimeError(f"Unsupported modality '{value}' in sanitized artifact.")
    return normalized


def _map_outputs_to_context_types(outputs: list[str]) -> list[str]:
    context_types: list[str] = []
    for output in outputs:
        mapped = OUTPUT_CONTEXT_TYPE_MAP.get(output)
        if mapped is None:
            raise BridgeRuntimeError(f"Unsupported output '{output}' in sanitized artifact.")
        for context_type in mapped:
            if context_type not in context_types:
                context_types.append(context_type)
    return context_types


def _map_output_to_api_name(output: str) -> Optional[str]:
    return OUTPUT_API_NAME_MAP.get(output)


def _resolve_context_api_name(item: CityOSPublisherCapability) -> str:
    direct = CAPABILITY_ID_API_NAME_MAP.get(item.capability_id)
    if direct is not None:
        return direct
    if len(item.outputs) == 1:
        mapped = _map_output_to_api_name(item.outputs[0])
        if mapped is not None:
            return mapped
    raise BridgeRuntimeError(
        f"Context API capability '{item.capability_id}' cannot be mapped to a supported SmartRoomStack API."
    )


def _destination_label(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.netloc:
        return parsed.netloc
    return parsed.path or "unknown"
