"""Phase 3 tests for principal and grant-based discovery authorization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi.testclient import TestClient

from capability_service.app import create_app
from capability_service.config import CapabilityServiceConfig
from capability_service.repository import InMemoryCapabilityRepository
from tellme_harness import TellMeHarness
from tellme_harness.cityos_discovery import HttpCityOSDiscoveryClient
from tellme_harness.config import CityOSDiscoveryConfig
from tellme_harness.schemas import CityOSDiscoveryRequestContext


def _service_config(
    registry_path: Optional[Path] = None,
    *,
    discovery_token: str = "discovery-token",
    publisher_token: str = "publisher-token",
) -> CapabilityServiceConfig:
    return CapabilityServiceConfig(
        mode="test",
        bearer_token=discovery_token,
        publisher_bearer_token=publisher_token,
        approved_capabilities_path=str(registry_path or Path("unused.json")),
        principal_token_values={
            "CAPABILITY_SERVICE_BEARER_TOKEN": discovery_token,
            "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN": publisher_token,
        },
    )


def _principals(*, tellme_status: str = "enabled", publisher_discovery: bool = False) -> list[dict]:
    return [
        {
            "principal_id": "tellme",
            "principal_type": "application",
            "status": tellme_status,
            "auth_token_env": "CAPABILITY_SERVICE_BEARER_TOKEN",
            "allowed_endpoints": ["discovery"],
        },
        {
            "principal_id": "capability_publisher",
            "principal_type": "application",
            "status": "enabled",
            "auth_token_env": "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN",
            "allowed_endpoints": ["publisher"] + (["discovery"] if publisher_discovery else []),
        },
    ]


def _space_record() -> dict:
    return {
        "smart_room_1": {
            "snapshot_id": "cap_phase3_room_1",
            "generated_at": "2026-07-08T12:00:00Z",
            "schema_version": "1.1",
            "source": "live",
            "sensors": [
                {
                    "sensor_id": "camera_door_01",
                    "modality": "video",
                    "available": True,
                    "status": "online",
                    "supported_context_types": ["occupancy", "motion", "events"],
                    "supported_capabilities": ["anonymous_track_creation"],
                    "allowed_api_names": ["get_entry_event_context"],
                },
                {
                    "sensor_id": "microphone_01",
                    "modality": "audio",
                    "available": True,
                    "status": "online",
                    "supported_context_types": ["audio", "events", "impact_sound"],
                    "supported_capabilities": ["impact_sound_candidate_detection"],
                    "allowed_api_names": ["get_impact_sound_context"],
                },
            ],
            "context_apis": [
                {
                    "api_name": "get_entry_event_context",
                    "modality": "video",
                    "available": True,
                    "returns_packet_type": "entry_event_packet",
                    "raw_access": False,
                    "data_level": "derived_context",
                    "owner_sensor_ids": ["camera_door_01"],
                },
                {
                    "api_name": "get_impact_sound_context",
                    "modality": "audio",
                    "available": True,
                    "returns_packet_type": "impact_sound_packet",
                    "raw_access": False,
                    "data_level": "derived_context",
                    "owner_sensor_ids": ["microphone_01"],
                },
            ],
            "privacy_policies": [{"policy_id": "policy_1"}],
        }
    }


def _request(
    *,
    space_id: str = "smart_room_1",
    named_modalities: Optional[list[str]] = None,
    context_requirements: Optional[list[str]] = None,
) -> dict:
    return {
        "query_id": "tellme_query_phase3",
        "space_id": space_id,
        "user_query": "What is the latest room state?",
        "intent": "live_state",
        "named_modalities": named_modalities or [],
        "context_requirements": context_requirements or [],
        "time_window": {"label": "latest"},
    }


def _grant(
    *,
    grant_id: str = "grant_tellme_room_1",
    principal_id: str = "tellme",
    space_id: str = "smart_room_1",
    capability_ids: Optional[list[str]] = None,
    allowed_operations: Optional[list[str]] = None,
    allowed_outputs: Optional[list[str]] = None,
    purpose: str = "answer_smart_room_query",
    expires_at: Optional[str] = None,
    enabled: bool = True,
) -> dict:
    return {
        "grant_id": grant_id,
        "principal_id": principal_id,
        "space_id": space_id,
        "capability_ids": capability_ids or [],
        "allowed_operations": allowed_operations or [],
        "allowed_outputs": allowed_outputs or [],
        "purpose": purpose,
        "expires_at": expires_at,
        "enabled": enabled,
    }


def _repository(
    *,
    records: Optional[dict] = None,
    principals: Optional[list[dict]] = None,
    grants: Optional[list[dict]] = None,
) -> InMemoryCapabilityRepository:
    return InMemoryCapabilityRepository(
        records or _space_record(),
        principals=principals or _principals(),
        grants=grants or [],
        validate_on_load=False,
    )


def _client_config(token: str = "discovery-token") -> CityOSDiscoveryConfig:
    return CityOSDiscoveryConfig(
        mode="production",
        provider="http",
        fixture_path=None,
        service_url="http://capability.service.test/v1/discovery/query",
        bearer_token=token,
        timeout_seconds=2,
        retry_count=0,
        schema_version="1.1",
        max_response_bytes=10000,
        max_capability_count=64,
    )


def _make_urlopen(client: TestClient, captured: list[dict]) -> object:
    class _Response:
        def __init__(self, body: bytes, status_code: int = 200, headers: dict | None = None) -> None:
            self._body = body
            self.status = status_code
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=None):
            return self._body if limit is None else self._body[:limit]

    def _urlopen(request, timeout=None):
        body = request.data or b""
        headers = dict(request.header_items())
        captured.append({"headers": headers, "body": body, "url": request.full_url})
        response = client.request(
            request.get_method(),
            request.full_url,
            headers=headers,
            content=body,
        )
        if response.status_code >= 400:
            from urllib.error import HTTPError

            raise HTTPError(
                request.full_url,
                response.status_code,
                response.text,
                hdrs=response.headers,
                fp=None,
            )
        return _Response(response.content, response.status_code, dict(response.headers))

    return _urlopen


def _audit_events(app) -> list[dict]:
    return [event.model_dump(exclude_none=True) for event in app.state.repository.get_audit_events()]


def test_unknown_token_returns_401() -> None:
    client = TestClient(create_app(config=_service_config(), repository=_repository()))
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer unknown-token"},
    )
    assert response.status_code == 401


def test_disabled_principal_returns_403() -> None:
    app = create_app(
        config=_service_config(),
        repository=_repository(principals=_principals(tellme_status="disabled")),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 403
    event = next(event for event in _audit_events(app) if event["event_type"] == "discovery_auth_failure")
    assert event["decision"] == "forbidden"
    assert event["error_category"] == "principal_disabled"


def test_discovery_token_cannot_register() -> None:
    client = TestClient(create_app(config=_service_config(), repository=_repository()))
    response = client.post(
        "/v1/capabilities/register",
        json={
            "snapshot_id": "cap_1",
            "space_id": "smart_room_2",
            "generated_at": "2026-07-08T12:00:00Z",
            "schema_version": "1.1",
            "source": "live",
            "sensors": [],
            "context_apis": [],
            "privacy_policies": [{"policy_id": "p1"}],
        },
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 403


def test_discovery_token_cannot_update_status() -> None:
    client = TestClient(create_app(config=_service_config(), repository=_repository()))
    response = client.post(
        "/v1/capabilities/sensor:camera_door_01/status",
        json={"available": False, "last_status_at": "2026-07-08T12:30:00Z"},
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 403


def test_publisher_token_cannot_call_discovery_without_grant() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(principals=_principals(publisher_discovery=True)),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer publisher-token"},
    )
    assert response.status_code == 200
    assert response.json()["sensors"] == []
    assert response.json()["context_apis"] == []


def test_no_grant_returns_empty_snapshot() -> None:
    app = create_app(config=_service_config(), repository=_repository())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 200
    assert response.json()["sensors"] == []
    assert response.json()["context_apis"] == []
    event = next(event for event in _audit_events(app) if event["event_type"] == "discovery_query_completed")
    assert event["grant_ids_used"] == []
    assert event["decision"] == "empty"
    assert "camera_door_01" not in json.dumps(event)


def test_wrong_space_grant_returns_empty_snapshot() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(grants=[_grant(space_id="smart_room_2")]),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 200
    assert response.json()["sensors"] == []


def test_one_capability_grant_does_not_reveal_another() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(grants=[_grant(capability_ids=["sensor:camera_door_01"])]),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert [sensor["sensor_id"] for sensor in payload["sensors"]] == ["camera_door_01"]
    assert payload["context_apis"] == []


def test_one_api_grant_does_not_reveal_all_apis() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(grants=[_grant(allowed_operations=["get_entry_event_context"])]),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    payload = response.json()
    assert [api["api_name"] for api in payload["context_apis"]] == ["get_entry_event_context"]
    assert payload["sensors"] == []


def test_expired_and_disabled_grants_return_empty_snapshot() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(
                grants=[
                    _grant(grant_id="expired", expires_at="2020-01-01T00:00:00Z"),
                    _grant(grant_id="disabled", enabled=False),
                ]
            ),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 200
    assert response.json()["context_apis"] == []


def test_matching_grant_returns_only_allowed_capabilities() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(
                grants=[
                    _grant(
                        capability_ids=["sensor:camera_door_01", "api:get_entry_event_context"],
                    )
                ]
            ),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    payload = response.json()
    assert [sensor["sensor_id"] for sensor in payload["sensors"]] == ["camera_door_01"]
    assert [api["api_name"] for api in payload["context_apis"]] == ["get_entry_event_context"]


def test_allowed_outputs_and_request_filters_further_narrow_results() -> None:
    client = TestClient(
        create_app(
            config=_service_config(),
            repository=_repository(
                grants=[_grant(allowed_outputs=["impact_sound_packet", "audio"])],
            ),
        )
    )
    response = client.post(
        "/v1/discovery/query",
        json=_request(named_modalities=["audio"], context_requirements=["impact_sound"]),
        headers={"Authorization": "Bearer discovery-token"},
    )
    payload = response.json()
    assert [api["api_name"] for api in payload["context_apis"]] == ["get_impact_sound_context"]
    assert payload["sensors"] == []


def test_newly_registered_capability_is_hidden_until_granted(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(
        json.dumps(
            {
                "spaces": {},
                "capability_status": {},
                "principals": _principals(),
                "grants": [],
            }
        ),
        encoding="utf-8",
    )
    config = _service_config(registry_path)
    client = TestClient(create_app(config=config))
    response = client.post(
        "/v1/capabilities/register",
        json={
            "snapshot_id": "cap_new",
            "space_id": "smart_room_new",
            "generated_at": "2026-07-08T12:00:00Z",
            "schema_version": "1.1",
            "source": "live",
            "sensors": [
                {
                    "sensor_id": "camera_new_01",
                    "modality": "video",
                    "space_id": "smart_room_new",
                    "available": True,
                    "supported_context_types": ["occupancy"],
                    "allowed_api_names": ["get_occupancy_context"],
                }
            ],
            "context_apis": [
                {
                    "api_name": "get_occupancy_context",
                    "modality": "video",
                    "available": True,
                    "returns_packet_type": "occupancy_context_packet",
                    "raw_access": False,
                    "data_level": "derived_context",
                    "owner_sensor_ids": ["camera_new_01"],
                }
            ],
            "privacy_policies": [{"policy_id": "p_new"}],
        },
        headers={"Authorization": "Bearer publisher-token"},
    )
    assert response.status_code == 200

    discovery = TestClient(create_app(config=config)).post(
        "/v1/discovery/query",
        json=_request(space_id="smart_room_new"),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert discovery.status_code == 200
    assert discovery.json()["sensors"] == []


def test_once_granted_registered_capability_appears(tmp_path: Path) -> None:
    registry_path = tmp_path / "approved_capabilities.json"
    registry_path.write_text(
        json.dumps(
            {
                "spaces": {},
                "capability_status": {},
                "principals": _principals(),
                "grants": [
                    _grant(
                        space_id="smart_room_new",
                        capability_ids=["sensor:camera_new_01", "api:get_occupancy_context"],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    config = _service_config(registry_path)
    client = TestClient(create_app(config=config))
    client.post(
        "/v1/capabilities/register",
        json={
            "snapshot_id": "cap_new",
            "space_id": "smart_room_new",
            "generated_at": "2026-07-08T12:00:00Z",
            "schema_version": "1.1",
            "source": "live",
            "sensors": [
                {
                    "sensor_id": "camera_new_01",
                    "modality": "video",
                    "space_id": "smart_room_new",
                    "available": True,
                    "supported_context_types": ["occupancy"],
                    "allowed_api_names": ["get_occupancy_context"],
                }
            ],
            "context_apis": [
                {
                    "api_name": "get_occupancy_context",
                    "modality": "video",
                    "available": True,
                    "returns_packet_type": "occupancy_context_packet",
                    "raw_access": False,
                    "data_level": "derived_context",
                    "owner_sensor_ids": ["camera_new_01"],
                }
            ],
            "privacy_policies": [{"policy_id": "p_new"}],
        },
        headers={"Authorization": "Bearer publisher-token"},
    )
    discovery = TestClient(create_app(config=config)).post(
        "/v1/discovery/query",
        json=_request(space_id="smart_room_new"),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert [sensor["sensor_id"] for sensor in discovery.json()["sensors"]] == ["camera_new_01"]


def test_unavailable_raw_and_identity_capabilities_remain_hidden_even_if_granted() -> None:
    repository = InMemoryCapabilityRepository(
        {
            "smart_room_1": {
                "snapshot_id": "cap_privacy",
                "generated_at": "2026-07-08T12:00:00Z",
                "schema_version": "1.1",
                "source": "live",
                "sensors": [
                    {
                        "sensor_id": "camera_hidden",
                        "modality": "video",
                        "available": False,
                        "supported_context_types": ["occupancy"],
                    },
                    {
                        "sensor_id": "camera_identity",
                        "modality": "video",
                        "available": True,
                        "supported_context_types": ["occupancy"],
                        "supported_capabilities": ["face_identity"],
                    },
                ],
                "context_apis": [
                    {
                        "api_name": "get_raw_video_reference",
                        "modality": "video",
                        "available": True,
                        "returns_packet_type": "raw_video_packet",
                        "raw_access": True,
                        "data_level": "derived_context",
                        "owner_sensor_ids": ["camera_hidden"],
                    }
                ],
                "privacy_policies": [{"policy_id": "policy_1"}],
            }
        },
        principals=_principals(),
        grants=[
            _grant(
                capability_ids=[
                    "sensor:camera_hidden",
                    "sensor:camera_identity",
                    "api:get_raw_video_reference",
                ]
            )
        ],
        validate_on_load=False,
    )
    client = TestClient(create_app(config=_service_config(), repository=repository))
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer discovery-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["sensors"] == []
    assert payload["context_apis"] == []
    assert "denied" not in json.dumps(payload).lower()
    assert "why" not in json.dumps(payload).lower()


def test_http_discovery_client_and_harness_remain_compatible_with_grants(monkeypatch, tmp_path: Path) -> None:
    repository = _repository(
        grants=[
            _grant(
                capability_ids=["sensor:camera_door_01", "api:get_entry_event_context"],
            )
        ]
    )
    app = create_app(config=_service_config(), repository=repository)
    service_client = TestClient(app)
    captured: list[dict] = []
    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        _make_urlopen(service_client, captured),
    )

    discovery_provider = HttpCityOSDiscoveryClient(config=_client_config())
    harness = TellMeHarness(
        runs_root=tmp_path / ".runs" / "tellme",
        discovery_provider=discovery_provider,
    )
    answer = harness.handle_query("How many people are in this room right now?")
    brief = json.loads(
        (Path(answer.raw_outputs["run_dir"]) / "task_design_brief.json").read_text(encoding="utf-8")
    )

    assert captured[0]["headers"]["Authorization"] == "Bearer discovery-token"
    assert answer.status == "needs_tracefix"
    assert "agents" not in brief
    assert "channels" not in brief
    assert "dependencies" not in brief
    assert discovery_provider.last_provenance is not None
    assert discovery_provider.last_provenance["grant_state_hash"]


def test_audit_never_contains_denied_capability_ids_or_internal_paths() -> None:
    repository = _repository(grants=[_grant(capability_ids=["sensor:camera_door_01"])])
    app = create_app(config=_service_config(), repository=repository)
    client = TestClient(app)

    response = client.post(
        "/v1/discovery/query",
        json=_request(named_modalities=["audio"], context_requirements=["impact_sound"]),
        headers={"Authorization": "Bearer discovery-token"},
    )

    assert response.status_code == 200
    audit_text = json.dumps(_audit_events(app), sort_keys=True)
    assert "sensor:microphone_01" not in audit_text
    assert "api:get_impact_sound_context" not in audit_text
    assert "/v1/discovery/query" not in audit_text
    assert "camera_door_01" not in audit_text
