"""Phase 2 tests for trusted capability registration and bounded status updates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi.testclient import TestClient

from capability_service.app import create_app
from capability_service.audit import AUDIT_LOG_FILENAME
from capability_service.config import CapabilityServiceConfig


def _registry_payload(
    *,
    grants: Optional[list[dict]] = None,
    principals: Optional[list[dict]] = None,
) -> dict:
    return {
        "spaces": {},
        "capability_status": {},
        "principals": principals
        or [
            {
                "principal_id": "tellme",
                "principal_type": "application",
                "status": "enabled",
                "auth_token_env": "CAPABILITY_SERVICE_BEARER_TOKEN",
                "allowed_endpoints": ["discovery"],
            },
            {
                "principal_id": "capability_publisher",
                "principal_type": "application",
                "status": "enabled",
                "auth_token_env": "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN",
                "allowed_endpoints": ["publisher"],
            },
        ],
        "grants": grants or [],
    }


def _grant_for_registered_space() -> list[dict]:
    return [
        {
            "grant_id": "grant_tellme_registered_space",
            "principal_id": "tellme",
            "space_id": "smart_room_register",
            "capability_ids": [],
            "allowed_operations": [],
            "allowed_outputs": [],
            "purpose": "answer_smart_room_query",
            "expires_at": None,
            "enabled": True,
        }
    ]


def _service_config(
    registry_path: Path,
    *,
    discovery_token: str = "discovery-token",
    publisher_token: str = "publisher-token",
) -> CapabilityServiceConfig:
    return CapabilityServiceConfig(
        mode="test",
        bearer_token=discovery_token,
        publisher_bearer_token=publisher_token,
        approved_capabilities_path=str(registry_path),
    )


def _query(space_id: str) -> dict:
    return {
        "query_id": "tellme_query_phase2",
        "space_id": space_id,
        "user_query": "What is the latest room state?",
        "intent": "live_state",
        "named_modalities": ["video"],
        "context_requirements": ["occupancy"],
        "time_window": {"label": "latest"},
    }


def _snapshot(space_id: str = "smart_room_register") -> dict:
    return {
        "snapshot_id": "cap_register_1",
        "space_id": space_id,
        "generated_at": "2026-07-08T12:00:00Z",
        "schema_version": "1.1",
        "source": "live",
        "deployment_id": "phase2-test",
        "sensors": [
            {
                "sensor_id": "camera_register_01",
                "modality": "video",
                "space_id": space_id,
                "description": "Derived doorway occupancy camera.",
                "available": True,
                "status": "online",
                "supported_context_types": ["occupancy", "motion"],
                "limitations": ["Anonymous occupancy only."],
                "supported_capabilities": ["occupancy_estimation"],
                "unsupported_capabilities": ["identity_inference"],
                "allowed_api_names": ["get_occupancy_context"],
                "restricted_api_names": [],
            }
        ],
        "context_apis": [
            {
                "api_name": "get_occupancy_context",
                "description": "Derived occupancy summaries.",
                "modality": "video",
                "returns_packet_type": "occupancy_context_packet",
                "requires_privacy_scope": "cityos_structured_context_only",
                "available": True,
                "data_level": "derived_context",
                "required_arguments": ["space_id", "timestamp"],
                "supported_time_query_modes": ["latest", "point_in_time"],
                "privacy_level": "derived_context",
                "raw_access": False,
                "limitations": ["Anonymous occupancy only."],
                "owner_sensor_ids": ["camera_register_01"],
            }
        ],
        "privacy_policies": [
            {
                "policy_id": f"{space_id}_policy",
                "privacy_scope": "cityos_structured_context_only",
                "raw_sensor_access_allowed": False,
                "identity_inference_allowed": False,
                "forbidden_inferences": ["raw_sensor_access", "personal_identity"],
                "notes": ["Approved metadata only."],
            }
        ],
    }


def _audit_events(app) -> list[dict]:
    return [event.model_dump(exclude_none=True) for event in app.state.repository.get_audit_events()]


def test_register_requires_publisher_token(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)

    response = client.post("/v1/capabilities/register", json=_snapshot())

    assert response.status_code == 401


def test_register_rejects_discovery_token(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)

    response = client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer discovery-token"},
    )

    assert response.status_code == 403
    rejected = next(event for event in _audit_events(app) if event["event_type"] == "registration_rejected")
    assert rejected["decision"] == "forbidden"
    assert rejected["endpoint"] == "capabilities_register"


def test_register_snapshot_persists_but_discovery_requires_grant(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    config = _service_config(registry_path)

    app = create_app(config=config)
    client = TestClient(app)
    response = client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert response.status_code == 200
    assert response.json()["capability_count"] == 2

    reloaded_app = create_app(config=config)
    reloaded_client = TestClient(reloaded_app)
    discovery = reloaded_client.post(
        "/v1/discovery/query",
        json=_query("smart_room_register"),
        headers={"Authorization": "Bearer discovery-token"},
    )

    assert discovery.status_code == 200
    payload = discovery.json()
    assert payload["space_id"] == "smart_room_register"
    assert payload["sensors"] == []
    assert payload["context_apis"] == []


def test_register_rejects_raw_access_metadata(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)
    payload = _snapshot()
    payload["context_apis"][0]["raw_access"] = True

    response = client.post(
        "/v1/capabilities/register",
        json=payload,
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert response.status_code == 422
    assert "raw access" in response.text.lower()
    rejected = next(event for event in _audit_events(app) if event["event_type"] == "registration_rejected")
    assert rejected["error_category"] == "validation_error"


def test_register_rejects_identity_metadata(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)
    payload = _snapshot()
    payload["sensors"][0]["supported_capabilities"] = ["face_identity"]

    response = client.post(
        "/v1/capabilities/register",
        json=payload,
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert response.status_code == 422
    assert "forbidden" in response.text.lower()


def test_status_update_persists_and_hides_unavailable_capability(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(
        json.dumps(_registry_payload(grants=_grant_for_registered_space())),
        encoding="utf-8",
    )
    config = _service_config(registry_path)
    client = TestClient(create_app(config=config))
    client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer publisher-token"},
    )

    status_response = client.post(
        "/v1/capabilities/sensor:camera_register_01/status",
        json={"available": False, "last_status_at": "2026-07-08T12:30:00Z"},
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["capability_id"] == "sensor:camera_register_01"

    reloaded_client = TestClient(create_app(config=config))
    discovery = reloaded_client.post(
        "/v1/discovery/query",
        json=_query("smart_room_register"),
        headers={"Authorization": "Bearer discovery-token"},
    )

    assert discovery.status_code == 200
    assert discovery.json()["sensors"] == []
    assert discovery.json()["context_apis"] == []


def test_status_update_unknown_capability_returns_404(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)

    response = client.post(
        "/v1/capabilities/sensor:not_found/status",
        json={"available": False, "last_status_at": "2026-07-08T12:30:00Z"},
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert response.status_code == 404


def test_status_update_request_is_bounded(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    config = _service_config(registry_path)
    client = TestClient(create_app(config=config))
    client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer publisher-token"},
    )

    response = client.post(
        "/v1/capabilities/api:get_occupancy_context/status",
        json={
            "available": False,
            "last_status_at": "2026-07-08T12:45:00Z",
            "status": "offline",
        },
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert response.status_code == 422


def test_registration_success_writes_sanitized_audit_event(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)

    response = client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert response.status_code == 200
    event = next(event for event in _audit_events(app) if event["event_type"] == "capability_registered")
    assert event["decision"] == "allowed"
    assert event["endpoint"] == "capabilities_register"
    assert event["capability_count"] == 2


def test_status_update_success_and_rejection_write_sanitized_audit_events(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)
    client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer publisher-token"},
    )

    success = client.post(
        "/v1/capabilities/sensor:camera_register_01/status",
        json={"available": False, "last_status_at": "2026-07-08T12:30:00Z"},
        headers={"Authorization": "Bearer publisher-token"},
    )
    rejected = client.post(
        "/v1/capabilities/sensor:missing/status",
        json={"available": False, "last_status_at": "2026-07-08T12:31:00Z"},
        headers={"Authorization": "Bearer publisher-token"},
    )

    assert success.status_code == 200
    assert rejected.status_code == 404
    events = _audit_events(app)
    success_event = next(event for event in events if event["event_type"] == "capability_status_updated")
    rejected_event = next(event for event in events if event["event_type"] == "status_update_rejected")
    assert success_event["decision"] == "allowed"
    assert rejected_event["error_category"] == "capability_not_found"


def test_audit_log_file_is_append_only_jsonl(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    app = create_app(config=_service_config(registry_path))
    client = TestClient(app)

    client.post(
        "/v1/capabilities/register",
        json=_snapshot(),
        headers={"Authorization": "Bearer publisher-token"},
    )
    client.post(
        "/v1/discovery/query",
        json=_query("smart_room_register"),
        headers={"Authorization": "Bearer discovery-token"},
    )

    audit_path = registry_path.with_name(AUDIT_LOG_FILENAME)
    assert audit_path.exists()
    lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 2
    parsed = [json.loads(line) for line in lines]
    assert all("event_id" in event for event in parsed)
