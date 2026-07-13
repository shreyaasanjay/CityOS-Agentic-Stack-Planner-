"""Tests for the read-only capability discovery service and POST client flow."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from capability_service.app import create_app
from capability_service.config import CapabilityServiceConfig, get_capability_service_config
from capability_service.repository import InMemoryCapabilityRepository
from tellme_harness import TellMeHarness
from tellme_harness.cityos_discovery import CityOSDiscoveryError, HttpCityOSDiscoveryClient
from tellme_harness.config import CityOSDiscoveryConfig
from tellme_harness.schemas import CityOSCapabilitySnapshot, CityOSDiscoveryRequestContext


def _principals() -> list[dict]:
    return [
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
    ]


def _space_wildcard_grants(space_id: str, principal_id: str = "tellme") -> list[dict]:
    return [
        {
            "grant_id": f"grant_{principal_id}_{space_id}",
            "principal_id": principal_id,
            "space_id": space_id,
            "capability_ids": [],
            "allowed_operations": [],
            "allowed_outputs": [],
            "purpose": "answer_smart_room_query",
            "expires_at": None,
            "enabled": True,
        }
    ]


def _service_config(
    token: str = "service-token",
    publisher_token: str = "publisher-token",
) -> CapabilityServiceConfig:
    return CapabilityServiceConfig(
        mode="test",
        bearer_token=token,
        publisher_bearer_token=publisher_token,
        approved_capabilities_path="unused.json",
    )


def _client_config(token: str = "service-token") -> CityOSDiscoveryConfig:
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


def _request(space_id: str = "smart_room_1") -> dict:
    return {
        "query_id": "tellme_query_1",
        "space_id": space_id,
        "user_query": "How many people are in the room right now?",
        "intent": "live_occupancy_count",
        "named_modalities": ["video"],
        "context_requirements": ["occupancy"],
        "time_window": {"start": None, "end": None, "label": "latest"},
    }


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

        def read(self, limit: int | None = None) -> bytes:
            return self._body if limit is None else self._body[:limit]

    def _urlopen(request, timeout=None):
        body = request.data or b""
        headers = dict(request.header_items())
        captured.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": headers,
                "body": body,
            }
        )
        response = client.request(
            request.get_method(),
            request.full_url,
            headers=headers,
            content=body,
        )
        if response.status_code >= 400:
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


def _audit_text(app) -> str:
    return json.dumps(_audit_events(app), sort_keys=True)


def test_valid_authenticated_request_returns_valid_snapshot() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 200
    snapshot = CityOSCapabilitySnapshot(**response.json())
    assert snapshot.space_id == "smart_room_1"
    assert snapshot.context_apis
    assert response.headers["X-Capability-Validation-Outcome"] == "accepted"
    assert response.headers["X-Capability-State-Hash"]


def test_service_production_startup_fails_without_auth(monkeypatch) -> None:
    monkeypatch.setenv("CAPABILITY_SERVICE_MODE", "production")
    monkeypatch.delenv("CAPABILITY_SERVICE_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN", raising=False)
    try:
        get_capability_service_config()
    except ValueError as exc:
        assert "CAPABILITY_SERVICE_BEARER_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected production service config to require a bearer token")


def test_service_production_startup_fails_without_publisher_auth(monkeypatch) -> None:
    monkeypatch.setenv("CAPABILITY_SERVICE_MODE", "production")
    monkeypatch.setenv("CAPABILITY_SERVICE_BEARER_TOKEN", "service-token")
    monkeypatch.delenv("CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN", raising=False)
    try:
        get_capability_service_config()
    except ValueError as exc:
        assert "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected production service config to require a publisher token")


def test_missing_token_returns_401() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post("/v1/discovery/query", json=_request())
    assert response.status_code == 401


def test_invalid_token_returns_401() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_valid_token_succeeds() -> None:
    app = create_app(config=_service_config("abc123"))
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer abc123"},
    )
    assert response.status_code == 200


def test_wrong_space_returns_no_disclosed_capabilities() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(space_id="unknown_room"),
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["space_id"] == "unknown_room"
    assert payload["sensors"] == []
    assert payload["context_apis"] == []


def test_raw_access_capability_is_never_returned() -> None:
    app = create_app(
        config=_service_config(),
        repository=InMemoryCapabilityRepository(
            {
                "smart_room_1": {
                    "snapshot_id": "cap_1",
                    "generated_at": "2026-07-07T12:00:00Z",
                    "schema_version": "1.1",
                    "source": "live",
                    "sensors": [
                        {
                            "sensor_id": "camera_1",
                            "modality": "video",
                            "available": True,
                            "supported_context_types": ["occupancy"],
                            "allowed_api_names": ["get_raw_video_reference"],
                        }
                    ],
                    "context_apis": [
                        {
                            "api_name": "get_raw_video_reference",
                            "modality": "video",
                            "available": True,
                            "raw_access": True,
                            "owner_sensor_ids": ["camera_1"],
                        }
                    ],
                    "privacy_policies": [{"policy_id": "policy_1"}],
                }
            },
            principals=_principals(),
            grants=_space_wildcard_grants("smart_room_1"),
            validate_on_load=False,
        ),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["sensors"] == []
    assert payload["context_apis"] == []


def test_identity_capability_is_never_returned() -> None:
    app = create_app(
        config=_service_config(),
        repository=InMemoryCapabilityRepository(
            {
                "smart_room_1": {
                    "snapshot_id": "cap_1",
                    "generated_at": "2026-07-07T12:00:00Z",
                    "schema_version": "1.1",
                    "source": "live",
                    "sensors": [
                        {
                            "sensor_id": "camera_1",
                            "modality": "video",
                            "available": True,
                            "supported_context_types": ["occupancy"],
                            "supported_capabilities": ["face_identity"],
                        }
                    ],
                    "context_apis": [],
                    "privacy_policies": [{"policy_id": "policy_1"}],
                }
            },
            principals=_principals(),
            grants=_space_wildcard_grants("smart_room_1"),
            validate_on_load=False,
        ),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 200
    assert response.json()["sensors"] == []


def test_hidden_capability_is_not_disclosed() -> None:
    app = create_app(
        config=_service_config(),
        repository=InMemoryCapabilityRepository(
            {
                "smart_room_1": {
                    "snapshot_id": "cap_1",
                    "generated_at": "2026-07-07T12:00:00Z",
                    "schema_version": "1.1",
                    "source": "live",
                    "sensors": [
                        {
                            "sensor_id": "camera_hidden",
                            "modality": "video",
                            "available": True,
                            "hidden": True,
                            "supported_context_types": ["occupancy"],
                        }
                    ],
                    "context_apis": [],
                    "privacy_policies": [{"policy_id": "policy_1"}],
                }
            },
            principals=_principals(),
            grants=_space_wildcard_grants("smart_room_1"),
            validate_on_load=False,
        ),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 200
    assert response.json()["sensors"] == []


def test_unavailable_capability_is_filtered() -> None:
    app = create_app(
        config=_service_config(),
        repository=InMemoryCapabilityRepository(
            {
                "smart_room_1": {
                    "snapshot_id": "cap_1",
                    "generated_at": "2026-07-07T12:00:00Z",
                    "schema_version": "1.1",
                    "source": "live",
                    "sensors": [
                        {
                            "sensor_id": "camera_1",
                            "modality": "video",
                            "available": False,
                            "supported_context_types": ["occupancy"],
                        }
                    ],
                    "context_apis": [],
                    "privacy_policies": [{"policy_id": "policy_1"}],
                }
            },
            principals=_principals(),
            grants=_space_wildcard_grants("smart_room_1"),
            validate_on_load=False,
        ),
    )
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 200
    assert response.json()["sensors"] == []


def test_malformed_request_returns_422() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json={"query_id": "q1"},
        headers={"Authorization": "Bearer service-token"},
    )
    assert response.status_code == 422


def test_service_response_matches_cityoscapabilitysnapshot() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )
    CityOSCapabilitySnapshot(**response.json())


def test_client_sends_post_body_correctly(monkeypatch) -> None:
    app = create_app(config=_service_config())
    service_client = TestClient(app)
    captured: list[dict] = []
    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        _make_urlopen(service_client, captured),
    )
    client = HttpCityOSDiscoveryClient(config=_client_config())
    request_context = CityOSDiscoveryRequestContext(**_request())
    client.discover_capabilities(request_context)
    assert captured[0]["method"] == "POST"
    body = json.loads(captured[0]["body"].decode("utf-8"))
    assert body == request_context.model_dump()


def test_client_sends_auth_header(monkeypatch) -> None:
    app = create_app(config=_service_config("secret-token"))
    service_client = TestClient(app)
    captured: list[dict] = []
    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        _make_urlopen(service_client, captured),
    )
    client = HttpCityOSDiscoveryClient(config=_client_config("secret-token"))
    client.discover_capabilities(CityOSDiscoveryRequestContext(**_request()))
    assert captured[0]["headers"]["Authorization"] == "Bearer secret-token"


def test_client_rejects_unauthorized_response(monkeypatch) -> None:
    app = create_app(config=_service_config("expected"))
    service_client = TestClient(app)
    captured: list[dict] = []
    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        _make_urlopen(service_client, captured),
    )
    client = HttpCityOSDiscoveryClient(config=_client_config("wrong"))
    try:
        client.discover_capabilities(CityOSDiscoveryRequestContext(**_request()))
    except CityOSDiscoveryError as exc:
        assert "unavailable" in str(exc).lower()
    else:
        raise AssertionError("Expected unauthorized response to fail closed")


def test_client_rejects_malformed_snapshot(monkeypatch) -> None:
    class _Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=None):
            return b'{"not":"a snapshot"}'

    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        lambda request, timeout=None: _Response(),
    )
    client = HttpCityOSDiscoveryClient(config=_client_config())
    try:
        client.discover_capabilities(CityOSDiscoveryRequestContext(**_request()))
    except CityOSDiscoveryError:
        pass
    else:
        raise AssertionError("Expected malformed snapshot rejection")


def test_client_rejects_raw_access_snapshot(monkeypatch) -> None:
    payload = {
        "snapshot_id": "cap_prod_1",
        "space_id": "smart_room_1",
        "generated_at": "2026-07-07T12:00:00Z",
        "schema_version": "1.1",
        "source": "cityos_app",
        "sensors": [],
        "context_apis": [{"api_name": "get_occupancy_context", "raw_access": True}],
        "privacy_policies": [{"policy_id": "policy_room_1"}]
    }

    class _Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=None):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        lambda request, timeout=None: _Response(),
    )
    client = HttpCityOSDiscoveryClient(config=_client_config())
    try:
        client.discover_capabilities(CityOSDiscoveryRequestContext(**_request()))
    except CityOSDiscoveryError as exc:
        assert "Raw-access APIs" in str(exc)
    else:
        raise AssertionError("Expected raw-access snapshot rejection")


def test_end_to_end_contract_service_to_client_to_brief(monkeypatch, tmp_path: Path) -> None:
    app = create_app(config=_service_config())
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
    run_dir = Path(answer.raw_outputs["run_dir"])
    brief = json.loads((run_dir / "task_design_brief.json").read_text(encoding="utf-8"))
    assert answer.status == "needs_tracefix"
    assert "agents" not in brief
    assert "channels" not in brief
    assert "dependencies" not in brief
    assert brief["candidate_harnesses"]


def test_discovery_success_writes_sanitized_audit_event() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer service-token"},
    )

    assert response.status_code == 200
    events = _audit_events(app)
    completed = next(event for event in events if event["event_type"] == "discovery_query_completed")
    assert completed["decision"] in {"allowed", "empty"}
    assert completed["endpoint"] == "discovery_query"
    assert "grant_ids_used" in completed
    assert "capability_state_hash" in completed
    assert "grant_state_hash" in completed
    assert "privacy_policy_state_hash" in completed


def test_missing_token_writes_sanitized_auth_failure_audit_event() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)

    response = client.post("/v1/discovery/query", json=_request())

    assert response.status_code == 401
    events = _audit_events(app)
    failure = next(event for event in events if event["event_type"] == "discovery_auth_failure")
    assert failure["decision"] == "unauthenticated"
    assert failure["error_category"] == "missing_bearer_token"
    assert failure["endpoint"] == "discovery_query"


def test_invalid_token_writes_sanitized_auth_failure_audit_event() -> None:
    app = create_app(config=_service_config())
    client = TestClient(app)

    response = client.post(
        "/v1/discovery/query",
        json=_request(),
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    events = _audit_events(app)
    failure = next(event for event in events if event["event_type"] == "discovery_auth_failure")
    assert failure["decision"] == "unauthenticated"
    assert failure["error_category"] == "invalid_bearer_token"


def test_state_hash_is_deterministic() -> None:
    app_a = create_app(config=_service_config())
    app_b = create_app(config=_service_config())

    hashes_a = app_a.state.repository.get_state_hashes()
    hashes_b = app_b.state.repository.get_state_hashes()

    assert hashes_a.capability_state_hash == hashes_b.capability_state_hash
    assert hashes_a.grant_state_hash == hashes_b.grant_state_hash
    assert hashes_a.privacy_policy_state_hash == hashes_b.privacy_policy_state_hash


def test_state_hash_changes_when_capability_metadata_changes() -> None:
    base_repo = InMemoryCapabilityRepository(
        {
            "smart_room_1": {
                "snapshot_id": "cap_1",
                "generated_at": "2026-07-07T12:00:00Z",
                "schema_version": "1.1",
                "source": "live",
                "sensors": [],
                "context_apis": [],
                "privacy_policies": [{"policy_id": "policy_1"}],
            }
        },
        principals=_principals(),
        grants=_space_wildcard_grants("smart_room_1"),
        validate_on_load=False,
    )
    changed_repo = InMemoryCapabilityRepository(
        {
            "smart_room_1": {
                "snapshot_id": "cap_1",
                "generated_at": "2026-07-07T12:00:00Z",
                "schema_version": "1.1",
                "source": "live",
                "sensors": [{"sensor_id": "camera_2", "modality": "video", "supported_context_types": ["occupancy"]}],
                "context_apis": [],
                "privacy_policies": [{"policy_id": "policy_1"}],
            }
        },
        principals=_principals(),
        grants=_space_wildcard_grants("smart_room_1"),
        validate_on_load=False,
    )

    assert (
        base_repo.get_state_hashes().capability_state_hash
        != changed_repo.get_state_hashes().capability_state_hash
    )


def test_state_hash_changes_when_grants_change() -> None:
    repo_a = InMemoryCapabilityRepository(
        {},
        principals=_principals(),
        grants=_space_wildcard_grants("smart_room_1"),
        validate_on_load=False,
    )
    repo_b = InMemoryCapabilityRepository(
        {},
        principals=_principals(),
        grants=_space_wildcard_grants("smart_room_2"),
        validate_on_load=False,
    )

    assert repo_a.get_state_hashes().grant_state_hash != repo_b.get_state_hashes().grant_state_hash


def test_audit_never_contains_bearer_token_or_raw_query_text() -> None:
    app = create_app(config=_service_config(token="secret-token"))
    client = TestClient(app)
    raw_query = "Why did John Doe fall near /Users/private/camera?"

    response = client.post(
        "/v1/discovery/query",
        json={**_request(), "user_query": raw_query},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    audit_text = _audit_text(app)
    assert "secret-token" not in audit_text
    assert raw_query not in audit_text
    assert "/v1/discovery/query" not in audit_text


def test_http_client_captures_safe_provenance_headers(monkeypatch) -> None:
    app = create_app(config=_service_config())
    service_client = TestClient(app)
    captured: list[dict] = []
    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        _make_urlopen(service_client, captured),
    )
    client = HttpCityOSDiscoveryClient(config=_client_config())

    snapshot = client.discover_capabilities(CityOSDiscoveryRequestContext(**_request()))

    assert snapshot.space_id == "smart_room_1"
    assert client.last_provenance is not None
    assert client.last_provenance["request_id"] == "tellme_query_1"
    assert client.last_provenance["validation_outcome"] == "accepted"
    assert client.last_provenance["capability_state_hash"]
