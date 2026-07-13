"""Privacy-preserving and grant-aware filtering for capability snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import uuid4

from tellme_harness.schemas import (
    CityOSCapabilitySnapshot,
    CityOSDiscoveryRequestContext,
    PrivacyPolicyCapability,
)

from .auth import AuthenticatedPrincipal
from .repository import build_api_capability_id, build_sensor_capability_id
from .schemas import CapabilityGrant, FilteredSnapshotAuditInfo

FORBIDDEN_CAPABILITY_SNIPPETS = (
    "face_identity",
    "identity",
    "speaker_identity",
    "transcript",
    "raw_",
    "person_identifier",
)

DEFAULT_DISCOVERY_PURPOSE = "answer_smart_room_query"


def build_filtered_snapshot(
    record: Optional[dict[str, Any]],
    request_context: CityOSDiscoveryRequestContext,
    *,
    principal: AuthenticatedPrincipal,
    grants: Iterable[CapabilityGrant],
) -> CityOSCapabilitySnapshot:
    snapshot, _audit_info = build_filtered_snapshot_with_audit(
        record,
        request_context,
        principal=principal,
        grants=grants,
    )
    return snapshot


def build_filtered_snapshot_with_audit(
    record: Optional[dict[str, Any]],
    request_context: CityOSDiscoveryRequestContext,
    *,
    principal: AuthenticatedPrincipal,
    grants: Iterable[CapabilityGrant],
) -> tuple[CityOSCapabilitySnapshot, FilteredSnapshotAuditInfo]:
    active_grants = _select_active_grants(
        grants,
        principal=principal,
        request_context=request_context,
    )
    if not isinstance(record, dict) or not active_grants:
        snapshot = _empty_snapshot(
            request_context.space_id,
            record=record,
        )
        return snapshot, FilteredSnapshotAuditInfo(
            snapshot_id=snapshot.snapshot_id,
            schema_version=snapshot.schema_version,
            capability_count=0,
            grant_ids_used=[],
            decision="empty",
        )

    sensors = []
    eligible_sensor_ids: set[str] = set()
    allowed_sensor_ids: set[str] = set()
    used_grant_ids: set[str] = set()
    for sensor in record.get("sensors", []):
        if not isinstance(sensor, dict):
            continue
        if _sensor_base_allowed(sensor, request_context):
            eligible_sensor_ids.add(str(sensor.get("sensor_id", "")))
        grant_ids = _sensor_grant_ids(sensor, request_context, active_grants)
        if grant_ids:
            normalized = {k: v for k, v in sensor.items() if k not in {"space_id", "hidden"}}
            normalized["space_id"] = request_context.space_id
            sensors.append(normalized)
            allowed_sensor_ids.add(normalized["sensor_id"])
            used_grant_ids.update(grant_ids)

    context_apis = []
    for api in record.get("context_apis", []):
        if not isinstance(api, dict):
            continue
        grant_ids = _api_grant_ids(api, eligible_sensor_ids, request_context, active_grants)
        if grant_ids:
            normalized = {
                k: v
                for k, v in api.items()
                if k not in {"hidden", "denied", "internal_endpoint", "stream_name"}
            }
            owners = [owner for owner in normalized.get("owner_sensor_ids", []) if owner in allowed_sensor_ids]
            normalized["owner_sensor_ids"] = owners
            context_apis.append(normalized)
            used_grant_ids.update(grant_ids)

    privacy_policies = [
        policy
        for policy in record.get("privacy_policies", [])
        if isinstance(policy, dict)
    ] or [_default_privacy_policy(request_context.space_id).model_dump()]

    snapshot_payload = {
        "snapshot_id": record.get("snapshot_id") or f"cap_{uuid4().hex[:12]}",
        "space_id": request_context.space_id,
        "generated_at": record.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "deployment_id": record.get("deployment_id"),
        "sensors": sensors,
        "context_apis": context_apis,
        "privacy_policies": privacy_policies,
        "source": record.get("source", "live"),
        "schema_version": record.get("schema_version", "1.1"),
    }
    snapshot = CityOSCapabilitySnapshot(**snapshot_payload)
    capability_count = len(snapshot.sensors) + len(snapshot.context_apis)
    return snapshot, FilteredSnapshotAuditInfo(
        snapshot_id=snapshot.snapshot_id,
        schema_version=snapshot.schema_version,
        capability_count=capability_count,
        grant_ids_used=sorted(used_grant_ids) if capability_count else [],
        decision="allowed" if capability_count else "empty",
    )


def normalize_request_purpose(request_context: CityOSDiscoveryRequestContext) -> str:
    if request_context.intent:
        return DEFAULT_DISCOVERY_PURPOSE
    return DEFAULT_DISCOVERY_PURPOSE


def _select_active_grants(
    grants: Iterable[CapabilityGrant],
    *,
    principal: AuthenticatedPrincipal,
    request_context: CityOSDiscoveryRequestContext,
) -> list[CapabilityGrant]:
    purpose = normalize_request_purpose(request_context)
    now = datetime.now(timezone.utc)
    matched: list[CapabilityGrant] = []
    for grant in grants:
        if grant.principal_id != principal.principal_id:
            continue
        if grant.space_id != request_context.space_id:
            continue
        if not grant.enabled:
            continue
        if grant.purpose != purpose:
            continue
        if grant.expires_at:
            try:
                expires_at = datetime.fromisoformat(grant.expires_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expires_at <= now:
                continue
        matched.append(grant)
    return matched


def _sensor_allowed(
    sensor: dict[str, Any],
    request_context: CityOSDiscoveryRequestContext,
    grants: list[CapabilityGrant],
) -> bool:
    return bool(_sensor_grant_ids(sensor, request_context, grants))


def _sensor_grant_ids(
    sensor: dict[str, Any],
    request_context: CityOSDiscoveryRequestContext,
    grants: list[CapabilityGrant],
) -> list[str]:
    if not _sensor_base_allowed(sensor, request_context):
        return []
    return _sensor_matching_grant_ids(sensor, grants)


def _sensor_base_allowed(
    sensor: dict[str, Any],
    request_context: CityOSDiscoveryRequestContext,
) -> bool:
    if sensor.get("hidden") or not sensor.get("available", True):
        return False
    if _contains_forbidden(sensor.get("supported_capabilities", [])):
        return False
    if _contains_forbidden(sensor.get("allowed_api_names", [])):
        return False
    if request_context.named_modalities and sensor.get("modality") not in set(request_context.named_modalities):
        return False
    if request_context.context_requirements:
        supported_types = set(sensor.get("supported_context_types", []))
        if supported_types and not supported_types.intersection(set(request_context.context_requirements)):
            room_state_backstop = {"occupancy", "motion"} & set(request_context.context_requirements)
            if not (room_state_backstop and "room_state" in supported_types):
                return False
    return True


def _api_allowed(
    api: dict[str, Any],
    eligible_sensor_ids: set[str],
    request_context: CityOSDiscoveryRequestContext,
    grants: list[CapabilityGrant],
) -> bool:
    return bool(_api_grant_ids(api, eligible_sensor_ids, request_context, grants))


def _api_grant_ids(
    api: dict[str, Any],
    eligible_sensor_ids: set[str],
    request_context: CityOSDiscoveryRequestContext,
    grants: list[CapabilityGrant],
) -> list[str]:
    if api.get("hidden") or api.get("denied") or not api.get("available", True):
        return []
    if api.get("raw_access", False):
        return []
    if _contains_forbidden([api.get("api_name", ""), api.get("returns_packet_type", "")]):
        return []
    owner_sensor_ids = list(api.get("owner_sensor_ids", []))
    if owner_sensor_ids and not any(owner in eligible_sensor_ids for owner in owner_sensor_ids):
        return []
    if request_context.named_modalities and api.get("modality") not in set(request_context.named_modalities) | {"context", "fusion"}:
        return []
    if request_context.context_requirements:
        requirement_blob = " ".join(
            str(value).lower()
            for value in (
                api.get("api_name"),
                api.get("returns_packet_type"),
                api.get("description"),
            )
            if value
        )
        if not any(requirement.lower() in requirement_blob for requirement in request_context.context_requirements):
            if api.get("modality") not in {"context", "fusion"}:
                return []

    return _api_matching_grant_ids(api, grants)


def _sensor_permitted_by_grants(sensor: dict[str, Any], grants: list[CapabilityGrant]) -> bool:
    return bool(_sensor_matching_grant_ids(sensor, grants))


def _sensor_matching_grant_ids(sensor: dict[str, Any], grants: list[CapabilityGrant]) -> list[str]:
    capability_id = build_sensor_capability_id(str(sensor.get("sensor_id", "")))
    supported_context_types = {str(value) for value in sensor.get("supported_context_types", [])}
    matched: list[str] = []
    for grant in grants:
        if not grant.capability_ids and not grant.allowed_operations and not grant.allowed_outputs:
            matched.append(grant.grant_id)
            continue
        if capability_id in grant.capability_ids:
            if not grant.allowed_outputs or supported_context_types.intersection(set(grant.allowed_outputs)):
                matched.append(grant.grant_id)
    return matched


def _api_permitted_by_grants(api: dict[str, Any], grants: list[CapabilityGrant]) -> bool:
    return bool(_api_matching_grant_ids(api, grants))


def _api_matching_grant_ids(api: dict[str, Any], grants: list[CapabilityGrant]) -> list[str]:
    capability_id = build_api_capability_id(str(api.get("api_name", "")))
    operation_name = str(api.get("api_name", ""))
    output_tokens = {
        str(token)
        for token in [api.get("returns_packet_type"), *_derive_api_output_aliases(api)]
        if token
    }
    matched: list[str] = []
    for grant in grants:
        if not grant.capability_ids and not grant.allowed_operations and not grant.allowed_outputs:
            matched.append(grant.grant_id)
            continue
        capability_match = capability_id in grant.capability_ids if grant.capability_ids else False
        operation_match = operation_name in grant.allowed_operations if grant.allowed_operations else False
        output_match = bool(output_tokens.intersection(set(grant.allowed_outputs))) if grant.allowed_outputs else False
        if capability_match or operation_match or output_match:
            matched.append(grant.grant_id)
    return matched


def _derive_api_output_aliases(api: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    packet_type = api.get("returns_packet_type")
    if isinstance(packet_type, str) and packet_type.endswith("_packet"):
        aliases.append(packet_type[: -len("_packet")])
    description = api.get("description")
    if isinstance(description, str):
        aliases.append(description.lower().replace(" ", "_"))
    return aliases


def _contains_forbidden(values: list[Any]) -> bool:
    joined = " ".join(str(value).lower() for value in values if value is not None)
    return any(snippet in joined for snippet in FORBIDDEN_CAPABILITY_SNIPPETS)


def _empty_snapshot(space_id: str, *, record: Optional[dict[str, Any]] = None) -> CityOSCapabilitySnapshot:
    privacy_policies = [
        policy
        for policy in (record or {}).get("privacy_policies", [])
        if isinstance(policy, dict)
    ] or [_default_privacy_policy(space_id)]
    return CityOSCapabilitySnapshot(
        snapshot_id=(record or {}).get("snapshot_id") or f"cap_{uuid4().hex[:12]}",
        space_id=space_id,
        generated_at=(record or {}).get("generated_at") or datetime.now(timezone.utc).isoformat(),
        privacy_policies=privacy_policies,
        source=(record or {}).get("source", "live"),
        schema_version=(record or {}).get("schema_version", "1.1"),
    )


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
            "speaker_identification",
            "unrestricted_transcription",
        ],
        notes=["Only approved and granted capability metadata is disclosed."],
    )
