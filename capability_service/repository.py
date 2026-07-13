"""Mutable capability metadata repository with bounded status updates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, Tuple, Union
from uuid import uuid4

from tellme_harness.schemas import CityOSCapabilitySnapshot

from .audit import AUDIT_LOG_FILENAME, POLICY_VERSION, opaque_state_hash, stable_json_dumps
from .schemas import (
    CapabilityAuditEvent,
    CapabilityGrant,
    CapabilityStateHashes,
    CapabilityStatusUpdateRequest,
    CapabilityStatusUpdateResult,
    ServicePrincipal,
)


class CapabilityRepository(Protocol):
    def get_space_record(self, space_id: str) -> Optional[dict[str, Any]]:
        ...

    def get_principals(self) -> list[ServicePrincipal]:
        ...

    def get_grants(self) -> list[CapabilityGrant]:
        ...

    def register_snapshot(self, snapshot: CityOSCapabilitySnapshot) -> dict[str, Any]:
        ...

    def update_capability_status(
        self,
        capability_id: str,
        update: CapabilityStatusUpdateRequest,
    ) -> CapabilityStatusUpdateResult:
        ...

    def get_state_hashes(self) -> CapabilityStateHashes:
        ...

    def append_audit_event(self, event: CapabilityAuditEvent) -> None:
        ...

    def get_audit_events(self) -> list[CapabilityAuditEvent]:
        ...


class InMemoryCapabilityRepository:
    def __init__(
        self,
        records: dict[str, Any],
        *,
        validate_on_load: bool = True,
        capability_status: Optional[dict[str, Any]] = None,
        principals: Optional[list[dict[str, Any]]] = None,
        grants: Optional[list[dict[str, Any]]] = None,
        persist_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._capability_status: dict[str, dict[str, Any]] = {}
        self._principals: list[ServicePrincipal] = []
        self._grants: list[CapabilityGrant] = []
        self._persist_path = Path(persist_path) if persist_path is not None else None
        self._audit_events: list[CapabilityAuditEvent] = []
        self._audit_log_path = _derive_audit_log_path(self._persist_path)

        for space_id, record in records.items():
            if isinstance(record, dict):
                stored = json.loads(json.dumps(record))
                if validate_on_load:
                    _validate_snapshot_record(space_id, stored)
                self._records[space_id] = stored

        status_records = capability_status or {}
        for capability_id, status_payload in status_records.items():
            if isinstance(status_payload, dict):
                self._capability_status[capability_id] = json.loads(json.dumps(status_payload))

        for principal_payload in principals or []:
            if isinstance(principal_payload, dict):
                self._principals.append(ServicePrincipal(**principal_payload))

        for grant_payload in grants or []:
            if isinstance(grant_payload, dict):
                self._grants.append(CapabilityGrant(**grant_payload))

    def get_space_record(self, space_id: str) -> Optional[dict[str, Any]]:
        record = self._records.get(space_id)
        if not isinstance(record, dict):
            return None

        hydrated = json.loads(json.dumps(record))
        for sensor in hydrated.get("sensors", []):
            if not isinstance(sensor, dict):
                continue
            capability_id = build_sensor_capability_id(sensor.get("sensor_id", ""))
            status_payload = self._capability_status.get(capability_id)
            if status_payload is not None and "available" in status_payload:
                sensor["available"] = bool(status_payload["available"])

        for api in hydrated.get("context_apis", []):
            if not isinstance(api, dict):
                continue
            capability_id = build_api_capability_id(api.get("api_name", ""))
            status_payload = self._capability_status.get(capability_id)
            if status_payload is not None and "available" in status_payload:
                api["available"] = bool(status_payload["available"])

        return hydrated

    def get_principals(self) -> list[ServicePrincipal]:
        return [principal.model_copy() for principal in self._principals]

    def get_grants(self) -> list[CapabilityGrant]:
        return [grant.model_copy() for grant in self._grants]

    def register_snapshot(self, snapshot: CityOSCapabilitySnapshot) -> dict[str, Any]:
        payload = snapshot.model_dump()
        _validate_registration_snapshot(snapshot)
        self._records[snapshot.space_id] = json.loads(json.dumps(payload))

        current_ids = set(_snapshot_capability_ids(payload))
        stale_ids = [
            capability_id
            for capability_id, status_payload in self._capability_status.items()
            if status_payload.get("space_id") == snapshot.space_id and capability_id not in current_ids
        ]
        for capability_id in stale_ids:
            self._capability_status.pop(capability_id, None)

        generated_at = payload.get("generated_at") or datetime.now(timezone.utc).isoformat()
        for sensor in payload.get("sensors", []):
            if not isinstance(sensor, dict):
                continue
            capability_id = build_sensor_capability_id(sensor.get("sensor_id", ""))
            self._capability_status.setdefault(
                capability_id,
                {
                    "space_id": snapshot.space_id,
                    "available": bool(sensor.get("available", True)),
                    "last_status_at": generated_at,
                },
            )
        for api in payload.get("context_apis", []):
            if not isinstance(api, dict):
                continue
            capability_id = build_api_capability_id(api.get("api_name", ""))
            self._capability_status.setdefault(
                capability_id,
                {
                    "space_id": snapshot.space_id,
                    "available": bool(api.get("available", True)),
                    "last_status_at": generated_at,
                },
            )

        self._persist()
        return self.get_space_record(snapshot.space_id) or payload

    def update_capability_status(
        self,
        capability_id: str,
        update: CapabilityStatusUpdateRequest,
    ) -> CapabilityStatusUpdateResult:
        matched_space_id, _ = self._find_capability_space(capability_id)
        if matched_space_id is None:
            raise KeyError(capability_id)

        self._capability_status[capability_id] = {
            "space_id": matched_space_id,
            "available": update.available,
            "last_status_at": update.last_status_at,
        }
        self._persist()
        return CapabilityStatusUpdateResult(
            capability_id=capability_id,
            space_id=matched_space_id,
            available=update.available,
            last_status_at=update.last_status_at,
        )

    def get_state_hashes(self) -> CapabilityStateHashes:
        capability_payload = {
            "spaces": self._records,
            "capability_status": self._capability_status,
        }
        grant_payload = {
            "principals": [principal.model_dump() for principal in self._principals],
            "grants": [grant.model_dump() for grant in self._grants],
        }
        privacy_payload = {
            space_id: list(record.get("privacy_policies", []))
            for space_id, record in sorted(self._records.items())
        }
        return CapabilityStateHashes(
            policy_version=POLICY_VERSION,
            capability_state_hash=opaque_state_hash(capability_payload),
            grant_state_hash=opaque_state_hash(grant_payload),
            privacy_policy_state_hash=opaque_state_hash(privacy_payload),
        )

    def append_audit_event(self, event: CapabilityAuditEvent) -> None:
        self._audit_events.append(event)
        if self._audit_log_path is None:
            return
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(stable_json_dumps(event.model_dump(exclude_none=True)) + "\n")

    def get_audit_events(self) -> list[CapabilityAuditEvent]:
        return [event.model_copy() for event in self._audit_events]

    def _find_capability_space(self, capability_id: str) -> Tuple[Optional[str], Optional[str]]:
        if capability_id.startswith("sensor:"):
            target_id = capability_id[len("sensor:") :]
            for space_id, record in self._records.items():
                sensors = record.get("sensors", [])
                if any(
                    isinstance(sensor, dict) and sensor.get("sensor_id") == target_id
                    for sensor in sensors
                ):
                    return space_id, "sensor"
            return None, None

        if capability_id.startswith("api:"):
            target_id = capability_id[len("api:") :]
            for space_id, record in self._records.items():
                apis = record.get("context_apis", [])
                if any(isinstance(api, dict) and api.get("api_name") == target_id for api in apis):
                    return space_id, "api"
            return None, None

        return None, None

    def _persist(self) -> None:
        if self._persist_path is None:
            return

        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "spaces": self._records,
            "capability_status": self._capability_status,
            "principals": [principal.model_dump() for principal in self._principals],
            "grants": [grant.model_dump() for grant in self._grants],
        }
        temp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self._persist_path)


class ApprovedCapabilitiesRepository(InMemoryCapabilityRepository):
    def __init__(self, fixture_path: Union[str, Path]) -> None:
        path = Path(fixture_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        records, capability_status, principals, grants = _normalize_repository_payload(payload)
        super().__init__(
            records,
            validate_on_load=True,
            capability_status=capability_status,
            principals=principals,
            grants=grants,
            persist_path=path,
        )


def build_sensor_capability_id(sensor_id: str) -> str:
    return f"sensor:{sensor_id}"


def build_api_capability_id(api_name: str) -> str:
    return f"api:{api_name}"


def _normalize_repository_payload(
    payload: Any,
) -> Tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("Approved capability fixture must be a JSON object.")
    if "spaces" in payload:
        spaces = payload.get("spaces")
        capability_status = payload.get("capability_status", {})
        principals = payload.get("principals", [])
        grants = payload.get("grants", [])
        if not isinstance(spaces, dict):
            raise ValueError("Approved capability fixture 'spaces' must be a JSON object.")
        if not isinstance(capability_status, dict):
            raise ValueError("Approved capability fixture 'capability_status' must be a JSON object.")
        if not isinstance(principals, list):
            raise ValueError("Approved capability fixture 'principals' must be an array.")
        if not isinstance(grants, list):
            raise ValueError("Approved capability fixture 'grants' must be an array.")
        return spaces, capability_status, principals, grants
    return payload, {}, [], []


def _snapshot_capability_ids(record: dict[str, Any]) -> list[str]:
    capability_ids: list[str] = []
    for sensor in record.get("sensors", []):
        if isinstance(sensor, dict) and sensor.get("sensor_id"):
            capability_ids.append(build_sensor_capability_id(str(sensor["sensor_id"])))
    for api in record.get("context_apis", []):
        if isinstance(api, dict) and api.get("api_name"):
            capability_ids.append(build_api_capability_id(str(api["api_name"])))
    return capability_ids


def _derive_audit_log_path(persist_path: Optional[Path]) -> Optional[Path]:
    if persist_path is None:
        return None
    return persist_path.with_name(AUDIT_LOG_FILENAME)


def _validate_snapshot_record(space_id: str, record: dict[str, Any]) -> None:
    payload = dict(record)
    payload.setdefault("snapshot_id", f"cap_{uuid4().hex[:12]}")
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    payload.setdefault("schema_version", "1.1")
    payload.setdefault("source", "live")
    payload["space_id"] = space_id
    payload["sensors"] = [
        dict(sensor, space_id=space_id)
        for sensor in payload.get("sensors", [])
        if isinstance(sensor, dict)
    ]
    CityOSCapabilitySnapshot(**payload)


def _validate_registration_snapshot(snapshot: CityOSCapabilitySnapshot) -> None:
    errors: list[str] = []

    for sensor in snapshot.sensors:
        if _contains_forbidden(sensor.supported_capabilities):
            errors.append(f"Sensor {sensor.sensor_id} contains forbidden supported capabilities.")
        if _contains_forbidden(sensor.allowed_api_names):
            errors.append(f"Sensor {sensor.sensor_id} contains forbidden allowed API names.")
        if _contains_internal_reference(sensor.description):
            errors.append(f"Sensor {sensor.sensor_id} contains an internal reference.")
        if _contains_internal_reference_list(sensor.limitations):
            errors.append(f"Sensor {sensor.sensor_id} limitations contain an internal reference.")

    for api in snapshot.context_apis:
        if api.raw_access:
            errors.append(f"API {api.api_name} requests raw access.")
        if _contains_forbidden([api.api_name, api.returns_packet_type or "", api.description or ""]):
            errors.append(f"API {api.api_name} contains forbidden metadata.")
        if _contains_internal_reference(api.documentation_reference):
            errors.append(f"API {api.api_name} documentation reference is internal.")
        if _contains_internal_reference_list(api.limitations):
            errors.append(f"API {api.api_name} limitations contain an internal reference.")

    for policy in snapshot.privacy_policies:
        if policy.raw_sensor_access_allowed:
            errors.append(f"Privacy policy {policy.policy_id} permits raw sensor access.")
        if policy.identity_inference_allowed:
            errors.append(f"Privacy policy {policy.policy_id} permits identity inference.")

    if errors:
        raise ValueError("; ".join(errors))


FORBIDDEN_CAPABILITY_SNIPPETS = (
    "face_identity",
    "identity",
    "speaker_identity",
    "transcript",
    "raw_",
    "person_identifier",
)

FORBIDDEN_INTERNAL_REFERENCE_SNIPPETS = (
    "/users/",
    "file://",
    "rtsp://",
    "localhost",
    "127.0.0.1",
)


def _contains_forbidden(values: list[Any]) -> bool:
    joined = " ".join(str(value).lower() for value in values if value is not None)
    return any(snippet in joined for snippet in FORBIDDEN_CAPABILITY_SNIPPETS)


def _contains_internal_reference(value: Optional[str]) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return any(snippet in lowered for snippet in FORBIDDEN_INTERNAL_REFERENCE_SNIPPETS)


def _contains_internal_reference_list(values: list[Any]) -> bool:
    return any(_contains_internal_reference(str(value)) for value in values if value is not None)
