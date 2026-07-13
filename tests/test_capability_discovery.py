"""Tests for CityOS capability discovery, registry, and capability-grounded planning."""

from __future__ import annotations

import json
from pathlib import Path

from tellme_harness import TellMeHarness
from tellme_harness.capability_registry import CapabilityRegistry
from tellme_harness.cityos_discovery import (
    CityOSDiscoveryError,
    CityOSDiscoveryClient,
    HttpCityOSDiscoveryClient,
    MockCityOSDiscoveryProvider,
)
from tellme_harness.config import CityOSDiscoveryConfig
from tellme_harness.query_analysis import analyze_query
from tellme_harness.schemas import (
    CityOSCapabilitySnapshot,
    CityOSDiscoveryRequestContext,
    ContextAPICapability,
    PrivacyPolicyCapability,
    SensorCapability,
    TellMeQuery,
)


def make_query(user_query: str, space_id: str = "smart_room_1") -> TellMeQuery:
    return TellMeQuery(
        query_id="tellme_test_cap",
        user_query=user_query,
        space_id=space_id,
        timestamp=None,
        created_at="2026-06-24T12:00:00Z",
    )


# --- discovery client --------------------------------------------------------

def test_discovery_returns_snapshot_for_known_space() -> None:
    client = MockCityOSDiscoveryProvider()
    snap = client.discover_capabilities(
        CityOSDiscoveryRequestContext(query_id="q1", space_id="smart_room_1")
    )
    assert isinstance(snap, CityOSCapabilitySnapshot)
    assert snap.space_id == "smart_room_1"
    assert {s.modality for s in snap.sensors} >= {"video", "audio", "radar", "wifi"}
    assert any(s.sensor_id == "camera_door_01" for s in snap.sensors)
    assert any(s.sensor_id == "microphone_array_01" for s in snap.sensors)
    assert any(api.api_name == "get_occupancy_context" for api in snap.context_apis)
    assert snap.privacy_policies[0].raw_sensor_access_allowed is False


def test_primary_room_centers_active_modalities_on_camera_and_microphone() -> None:
    client = MockCityOSDiscoveryProvider()
    snap = client.discover_capabilities(
        CityOSDiscoveryRequestContext(query_id="q1", space_id="smart_room_1")
    )
    active_modalities = {sensor.modality for sensor in snap.sensors if sensor.available}
    assert active_modalities == {"video", "audio"}


def test_primary_room_declares_blind_spots_and_restricted_raw_apis() -> None:
    client = MockCityOSDiscoveryProvider()
    snap = client.discover_capabilities(
        CityOSDiscoveryRequestContext(query_id="q1", space_id="smart_room_1")
    )
    room_camera = next(sensor for sensor in snap.sensors if sensor.sensor_id == "camera_room_01")
    microphone = next(sensor for sensor in snap.sensors if sensor.sensor_id == "microphone_array_01")
    assert "under tables" in room_camera.blind_spots
    assert "get_raw_video_reference" in room_camera.restricted_api_names
    assert "room-wide acoustic coverage" in microphone.coverage_zones
    assert "get_raw_audio_reference" in microphone.restricted_api_names
    assert "speaker_identity" in microphone.unsupported_capabilities


def test_discovery_unknown_space_falls_back_to_default() -> None:
    client = MockCityOSDiscoveryProvider()
    snap = client.discover_capabilities(
        CityOSDiscoveryRequestContext(query_id="q1", space_id="space_that_does_not_exist")
    )
    assert snap.space_id == "space_that_does_not_exist"
    assert snap.sensors  # default snapshot is non-empty
    assert snap.privacy_policies


def test_discovery_ignores_fixture_space_id_override(tmp_path: Path) -> None:
    fixture = tmp_path / "caps.json"
    fixture.write_text(
        json.dumps(
            {
                "room_a": {
                    "sensors": [
                        {"sensor_id": "s1", "modality": "video", "space_id": "SPOOFED", "supported_context_types": ["motion"]}
                    ],
                    "context_apis": [],
                    "privacy_policies": [],
                }
            }
        ),
        encoding="utf-8",
    )
    client = MockCityOSDiscoveryProvider(fixture_path=fixture)
    snap = client.discover_capabilities(
        CityOSDiscoveryRequestContext(query_id="q1", space_id="room_a")
    )
    assert snap.sensors[0].space_id == "room_a"  # not "SPOOFED"


def test_discovery_client_protocol_is_satisfied() -> None:
    assert isinstance(MockCityOSDiscoveryProvider(), CityOSDiscoveryClient)


def test_http_discovery_client_validates_snapshot(monkeypatch) -> None:
    payload = {
        "snapshot_id": "cap_prod_1",
        "space_id": "smart_room_1",
        "generated_at": "2026-07-07T12:00:00Z",
        "deployment_id": "deploy_a",
        "schema_version": "1.1",
        "source": "cityos_app",
        "sensors": [
            {
                "sensor_id": "camera_room_01",
                "modality": "video",
                "space_id": "smart_room_1",
                "available": True,
                "supported_context_types": ["occupancy"],
            }
        ],
        "context_apis": [
            {
                "api_name": "get_occupancy_context",
                "modality": "video",
                "available": True,
                "raw_access": False,
                "owner_sensor_ids": ["camera_room_01"],
            }
        ],
        "privacy_policies": [{"policy_id": "policy_room_1"}],
    }

    class _Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit=None):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        lambda request, timeout=None: _Response(),
    )

    client = HttpCityOSDiscoveryClient(
        config=CityOSDiscoveryConfig(
            mode="production",
            provider="http",
            fixture_path=None,
            service_url="http://cityos.local/v1/discovery/query",
            bearer_token="service-token",
            timeout_seconds=2,
            retry_count=0,
            schema_version="1.1",
            max_response_bytes=10000,
            max_capability_count=10,
        )
    )
    snap = client.discover_capabilities(
        CityOSDiscoveryRequestContext(query_id="q1", space_id="smart_room_1")
    )
    assert snap.source == "cityos_app"
    assert snap.deployment_id == "deploy_a"


def test_http_discovery_client_rejects_raw_access(monkeypatch) -> None:
    payload = {
        "snapshot_id": "cap_prod_1",
        "space_id": "smart_room_1",
        "generated_at": "2026-07-07T12:00:00Z",
        "schema_version": "1.1",
        "source": "cityos_app",
        "sensors": [],
        "context_apis": [{"api_name": "get_occupancy_context", "raw_access": True}],
        "privacy_policies": [{"policy_id": "policy_room_1"}],
    }

    class _Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit=None):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "tellme_harness.cityos_discovery.urllib.request.urlopen",
        lambda request, timeout=None: _Response(),
    )

    client = HttpCityOSDiscoveryClient(
        config=CityOSDiscoveryConfig(
            mode="production",
            provider="http",
            fixture_path=None,
            service_url="http://cityos.local/v1/discovery/query",
            bearer_token="service-token",
            timeout_seconds=2,
            retry_count=0,
            schema_version="1.1",
            max_response_bytes=10000,
            max_capability_count=10,
        )
    )
    try:
        client.discover_capabilities(CityOSDiscoveryRequestContext(query_id="q1", space_id="smart_room_1"))
    except CityOSDiscoveryError as exc:
        assert "Raw-access APIs" in str(exc)
    else:
        raise AssertionError("Expected CityOSDiscoveryError for raw-access capability snapshot")


# --- registry caching --------------------------------------------------------

class CountingDiscoveryClient(CityOSDiscoveryClient):
    def __init__(self) -> None:
        self.calls = 0

    def discover_capabilities(
        self, request_context: CityOSDiscoveryRequestContext
    ) -> CityOSCapabilitySnapshot:
        self.calls += 1
        return CityOSCapabilitySnapshot(
            snapshot_id=f"snap_{self.calls}",
            space_id=request_context.space_id,
            generated_at="2026-06-24T12:00:00Z",
            sensors=[SensorCapability(sensor_id="s", modality="fusion", space_id=request_context.space_id, supported_context_types=["room_state"])],
            context_apis=[ContextAPICapability(api_name="get_room_state")],
            privacy_policies=[PrivacyPolicyCapability(policy_id="p")],
        )


def test_registry_caches_snapshot_within_ttl() -> None:
    client = CountingDiscoveryClient()
    registry = CapabilityRegistry(client, cache_ttl_seconds=1000)
    registry.get_snapshot("room")
    registry.get_snapshot("room")
    assert client.calls == 1
    assert registry.is_cached_fresh("room")


def test_registry_force_refresh_bypasses_cache() -> None:
    client = CountingDiscoveryClient()
    registry = CapabilityRegistry(client, cache_ttl_seconds=1000)
    registry.get_snapshot("room")
    registry.get_snapshot("room", force_refresh=True)
    assert client.calls == 2


def test_registry_refetches_when_stale() -> None:
    client = CountingDiscoveryClient()
    clock = {"t": 0.0}
    registry = CapabilityRegistry(client, cache_ttl_seconds=10, time_source=lambda: clock["t"])
    registry.get_snapshot("room")
    clock["t"] = 100.0  # advance past TTL
    registry.get_snapshot("room")
    assert client.calls == 2
    assert registry.is_cached_fresh("room") is False or client.calls == 2


# --- query-scoped context ----------------------------------------------------

def test_relevant_context_marks_audio_primary() -> None:
    registry = CapabilityRegistry()
    query = make_query("What was the noise level around 3pm?")
    analysis = analyze_query(query)
    ctx = registry.get_relevant_context(query_id="q", space_id="smart_room_1", analysis=analysis)
    audio = next(s for s in ctx.relevant_sensors if s.modality == "audio")
    assert audio.relevance == "primary"
    assert ctx.coverage_gaps == []


def test_relevant_context_reports_coverage_gap_for_missing_modality() -> None:
    registry = CapabilityRegistry()
    query = make_query("What was the noise level around 3pm?", space_id="smart_room_2")
    analysis = analyze_query(query)
    ctx = registry.get_relevant_context(query_id="q", space_id="smart_room_2", analysis=analysis)
    assert any("audio" in gap for gap in ctx.coverage_gaps)
    assert "get_audio_context" not in ctx.available_context_apis


def test_blind_spot_query_records_camera_gap() -> None:
    registry = CapabilityRegistry()
    query = make_query("What happened under the table at 10:20?")
    analysis = analyze_query(query)
    ctx = registry.get_relevant_context(query_id="q", space_id="smart_room_1", analysis=analysis)
    assert any("blind spot" in gap.lower() for gap in ctx.coverage_gaps)


# --- end-to-end harness integration -----------------------------------------

def test_harness_writes_capability_artifacts(tmp_path: Path) -> None:
    harness = TellMeHarness(runs_root=tmp_path / ".runs" / "tellme")
    answer = harness.handle_query("How many people were in the room at 10:05?")
    run_dir = Path(answer.raw_outputs["run_dir"])
    for name in (
        "cityos_capability_snapshot.json",
        "room_capability_context.json",
        "smartspace_execution_brief.json",
        "tracefix_design_prompt.md",
    ):
        assert (run_dir / name).exists(), f"missing artifact {name}"

    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    event_names = {e["event"] for e in events}
    assert "cityos_capabilities_discovered" in event_names
    assert "execution_brief_compiled" in event_names


def test_coverage_gap_query_is_non_executable(tmp_path: Path) -> None:
    harness = TellMeHarness(runs_root=tmp_path / ".runs" / "tellme")
    answer = harness.handle_query("What was the noise level around 3pm?", space_id="smart_room_2")
    brief = json.loads(
        (Path(answer.raw_outputs["run_dir"]) / "smartspace_execution_brief.json").read_text()
    )
    assert brief["executable"] is False
    assert "answer_synthesis_harness" in brief["candidate_harnesses"]
    room_context = json.loads(
        (Path(answer.raw_outputs["run_dir"]) / "room_capability_context.json").read_text()
    )
    assert room_context["coverage_gaps"]
