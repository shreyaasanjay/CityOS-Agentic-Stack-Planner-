from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from capability_bridge import BridgeConfig, BridgeConfigError, BridgeRuntimeError, CapabilityBridge
from capability_service.app import create_app
from capability_service.config import CapabilityServiceConfig
from tellme_harness.schemas import CityOSCapabilitySnapshot


def _fixture_payload() -> dict[str, Any]:
    fixture_path = Path("tests/fixtures/cityos_capability_publisher_example.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _config(path: Path, *, dry_run: bool = False, retry_count: int = 1, token: str = "publisher-token") -> BridgeConfig:
    return BridgeConfig(
        snapshot_path=path,
        capability_service_base_url="http://capability.service.test",
        publisher_bearer_token=token,
        timeout_seconds=1.0,
        retry_count=retry_count,
        dry_run=dry_run,
    )


def _service_config(registry_path: Path) -> CapabilityServiceConfig:
    return CapabilityServiceConfig(
        mode="test",
        bearer_token="discovery-token",
        publisher_bearer_token="publisher-token",
        approved_capabilities_path=str(registry_path),
        principal_token_values={
            "CAPABILITY_SERVICE_BEARER_TOKEN": "discovery-token",
            "CAPABILITY_SERVICE_PUBLISHER_BEARER_TOKEN": "publisher-token",
        },
    )


def _registry_payload() -> dict[str, Any]:
    return {
        "spaces": {},
        "capability_status": {},
        "principals": [
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
        "grants": [],
    }


def _make_urlopen(
    client: TestClient,
    captured: list[dict[str, Any]],
    *,
    fail_count: int = 0,
    http_error_code: Optional[int] = None,
):
    class _Response:
        def __init__(
            self,
            body: bytes,
            status_code: int = 200,
            headers: Optional[dict[str, Any]] = None,
        ) -> None:
            self._body = body
            self.status = status_code
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=None):
            return self._body if limit is None else self._body[:limit]

    attempts = {"count": 0}

    def _urlopen(request, timeout=None):
        attempts["count"] += 1
        if attempts["count"] <= fail_count:
            raise URLError("temporary bridge test failure")
        if http_error_code is not None:
            raise HTTPError(request.full_url, http_error_code, "error", hdrs={}, fp=None)
        body = request.data or b""
        headers = dict(request.header_items())
        captured.append({"url": request.full_url, "headers": headers, "body": body})
        response = client.request(request.get_method(), request.full_url, headers=headers, content=body)
        if response.status_code >= 400:
            raise HTTPError(request.full_url, response.status_code, response.text, hdrs=response.headers, fp=None)
        return _Response(response.content, response.status_code, dict(response.headers))

    return _urlopen


def test_valid_artifact_is_posted_to_registration_endpoint(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    registry_path = tmp_path / "approved_capabilities.json"
    _write_artifact(registry_path, _registry_payload())
    client = TestClient(create_app(config=_service_config(registry_path)))
    captured: list[dict[str, Any]] = []

    result = CapabilityBridge(
        _config(artifact_path),
        urlopen=_make_urlopen(client, captured),
    ).run()

    assert result.registration_result is not None
    assert any(entry["url"].endswith("/v1/capabilities/register") for entry in captured)
    request_body = json.loads(next(entry["body"] for entry in captured if entry["url"].endswith("/v1/capabilities/register")))
    CityOSCapabilitySnapshot(**request_body)
    assert request_body["source"] == "cityos_app"
    assert request_body["context_apis"][0]["api_name"] == "get_occupancy_context"


def test_missing_token_fails_closed(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    config = BridgeConfig(
        snapshot_path=artifact_path,
        capability_service_base_url="http://capability.service.test",
        publisher_bearer_token="",
        timeout_seconds=1.0,
        retry_count=0,
        dry_run=False,
    )

    with pytest.raises(BridgeConfigError):
        config.validate()


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    bridge = CapabilityBridge(_config(tmp_path / "missing.json", dry_run=True))

    with pytest.raises(BridgeRuntimeError):
        bridge.run()


def test_malformed_artifact_fails_closed(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    artifact_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(BridgeRuntimeError):
        CapabilityBridge(_config(artifact_path, dry_run=True)).run()


def test_raw_access_artifact_is_rejected(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["context_apis"][0]["raw_access"] = True
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, payload)

    with pytest.raises(BridgeRuntimeError):
        CapabilityBridge(_config(artifact_path, dry_run=True)).run()


def test_url_or_path_artifact_is_rejected(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["source"]["origin"] = "file:///Users/example/raw-output.json"
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, payload)

    with pytest.raises(BridgeRuntimeError):
        CapabilityBridge(_config(artifact_path, dry_run=True)).run()


def test_stream_name_artifact_is_rejected(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["source"]["stream_name"] = "hidden.camera.stream"
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, payload)

    with pytest.raises(BridgeRuntimeError):
        CapabilityBridge(_config(artifact_path, dry_run=True)).run()


def test_identity_transcript_person_tracking_metadata_is_rejected(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["context_apis"][0]["outputs"] = ["speaker_identity"]
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, payload)

    with pytest.raises(BridgeRuntimeError):
        CapabilityBridge(_config(artifact_path, dry_run=True)).run()


def test_bridge_reads_only_configured_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    seen_paths: list[Path] = []
    original_read_text = Path.read_text

    def _read_text(self: Path, *args, **kwargs):
        seen_paths.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    CapabilityBridge(_config(artifact_path, dry_run=True)).run()

    assert seen_paths == [artifact_path]


def test_bridge_does_not_scan_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())

    def _fail(*args, **kwargs):
        raise AssertionError("directory scanning is not allowed")

    monkeypatch.setattr(Path, "iterdir", _fail)
    monkeypatch.setattr(Path, "glob", _fail)
    monkeypatch.setattr(Path, "rglob", _fail)

    CapabilityBridge(_config(artifact_path, dry_run=True)).run()


def test_token_is_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    caplog.set_level(logging.INFO)

    CapabilityBridge(_config(artifact_path, dry_run=True, token="super-secret-token")).run()

    assert "super-secret-token" not in caplog.text


def test_dry_run_validates_but_does_not_post(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())

    def _unexpected(*args, **kwargs):
        raise AssertionError("dry run must not publish")

    result = CapabilityBridge(_config(artifact_path, dry_run=True), urlopen=_unexpected).run()

    assert result.dry_run is True
    assert result.registration_result is None


def test_retry_behavior_works_for_transient_failures(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    registry_path = tmp_path / "approved_capabilities.json"
    _write_artifact(registry_path, _registry_payload())
    client = TestClient(create_app(config=_service_config(registry_path)))
    captured: list[dict[str, Any]] = []

    result = CapabilityBridge(
        _config(artifact_path, retry_count=1),
        urlopen=_make_urlopen(client, captured, fail_count=1),
    ).run()

    assert result.registration_result is not None
    assert len(captured) >= 1


def test_non_2xx_registration_response_fails_closed(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    registry_path = tmp_path / "approved_capabilities.json"
    _write_artifact(registry_path, _registry_payload())
    client = TestClient(create_app(config=_service_config(registry_path)))

    with pytest.raises(BridgeRuntimeError):
        CapabilityBridge(
            _config(artifact_path, retry_count=0),
            urlopen=_make_urlopen(client, [], http_error_code=400),
        ).run()


def test_status_updates_are_bounded_if_emitted(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    registry_path = tmp_path / "approved_capabilities.json"
    _write_artifact(registry_path, _registry_payload())
    client = TestClient(create_app(config=_service_config(registry_path)))
    captured: list[dict[str, Any]] = []

    result = CapabilityBridge(
        _config(artifact_path),
        urlopen=_make_urlopen(client, captured),
    ).run()

    status_calls = [entry for entry in captured if "/status" in entry["url"]]
    assert result.status_update_count == len(status_calls)
    assert status_calls
    for entry in status_calls:
        body = json.loads(entry["body"])
        assert sorted(body.keys()) == ["available", "last_status_at"]


def test_produced_request_matches_capability_service_contract(tmp_path: Path) -> None:
    artifact_path = tmp_path / "snapshot.json"
    _write_artifact(artifact_path, _fixture_payload())
    registry_path = tmp_path / "approved_capabilities.json"
    _write_artifact(registry_path, _registry_payload())
    client = TestClient(create_app(config=_service_config(registry_path)))
    captured: list[dict[str, Any]] = []

    CapabilityBridge(_config(artifact_path), urlopen=_make_urlopen(client, captured)).run()

    request_body = json.loads(next(entry["body"] for entry in captured if entry["url"].endswith("/v1/capabilities/register")))
    snapshot = CityOSCapabilitySnapshot(**request_body)
    assert snapshot.context_apis[0].api_name == "get_occupancy_context"
    assert snapshot.context_apis[1].api_name == "get_wifi_context"
    assert snapshot.context_apis[2].api_name == "get_radar_context"


def test_contract_fixture_round_trip_succeeds(tmp_path: Path) -> None:
    source_fixture = Path("tests/fixtures/cityos_capability_publisher_example.json")
    artifact_path = tmp_path / "snapshot.json"
    artifact_path.write_text(source_fixture.read_text(encoding="utf-8"), encoding="utf-8")
    registry_path = tmp_path / "approved_capabilities.json"
    _write_artifact(registry_path, _registry_payload())
    client = TestClient(create_app(config=_service_config(registry_path)))
    captured: list[dict[str, Any]] = []

    result = CapabilityBridge(_config(artifact_path), urlopen=_make_urlopen(client, captured)).run()

    assert result.registration_result is not None
    assert result.registration_result["capability_count"] == 5
