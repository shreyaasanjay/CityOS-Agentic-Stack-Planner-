"""Capability registry: caches CityOS snapshots and derives query-scoped context.

The registry sits between raw discovery and the planner. It (1) caches capability
snapshots per space with a TTL so discovery is not re-run on every query, and (2)
projects a snapshot onto a single query as a ``RoomCapabilityContext`` — which
sensors are relevant, which context APIs are usable, the governing privacy
policy, and any coverage gaps the planner must respect.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from .cityos_discovery import (
    CityOSDiscoveryProvider,
    MockCityOSDiscoveryProvider,
)
from .schemas import (
    CityOSCapabilitySnapshot,
    CityOSDiscoveryRequestContext,
    PrivacyPolicyCapability,
    QueryAnalysis,
    RelevantSensorCapability,
    RoomCapabilityContext,
    SensorCapability,
    TimeWindow,
)

# Maps an analysis context requirement to the context types a sensor must
# support to be considered relevant to it.
_REQUIREMENT_CONTEXT_TYPES = {
    "occupancy": {"occupancy", "room_state"},
    "motion": {"motion", "room_state"},
    "audio": {"audio"},
    "room_state": {"room_state"},
    "events": {"events"},
    "tracks": {"events", "motion"},
}

DEFAULT_CACHE_TTL_SECONDS = 300.0


class CapabilityRegistry:
    def __init__(
        self,
        discovery_client: Optional[CityOSDiscoveryProvider] = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        time_source=time.monotonic,
    ) -> None:
        self.discovery_client = discovery_client or MockCityOSDiscoveryProvider()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._time_source = time_source
        self._cache: Dict[str, Tuple[CityOSCapabilitySnapshot, float]] = {}
        self._last_provenance: Dict[str, dict] = {}

    # -- snapshot caching --------------------------------------------------

    def get_snapshot(
        self,
        space_id: str,
        *,
        request_context: Optional[CityOSDiscoveryRequestContext] = None,
        force_refresh: bool = False,
    ) -> CityOSCapabilitySnapshot:
        cached = self._cache.get(space_id)
        if cached and not force_refresh and not self._is_stale(cached[1]):
            return cached[0]
        context = request_context or CityOSDiscoveryRequestContext(query_id="snapshot_cache", space_id=space_id)
        snapshot = self.discovery_client.discover_capabilities(context)
        self._cache[space_id] = (snapshot, self._time_source())
        provenance = getattr(self.discovery_client, "last_provenance", None)
        if isinstance(provenance, dict):
            self._last_provenance[space_id] = dict(provenance)
        return snapshot

    def is_cached_fresh(self, space_id: str) -> bool:
        cached = self._cache.get(space_id)
        return bool(cached) and not self._is_stale(cached[1])

    def get_last_provenance(self, space_id: str) -> Optional[dict]:
        provenance = self._last_provenance.get(space_id)
        return dict(provenance) if isinstance(provenance, dict) else None

    def _is_stale(self, fetched_at: float) -> bool:
        return (self._time_source() - fetched_at) > self.cache_ttl_seconds

    # -- query-scoped projection ------------------------------------------

    def get_relevant_context(
        self,
        *,
        query_id: str,
        space_id: str,
        analysis: QueryAnalysis,
        time_window: Optional[TimeWindow] = None,
        force_refresh: bool = False,
    ) -> RoomCapabilityContext:
        request_context = CityOSDiscoveryRequestContext(
            query_id=query_id,
            space_id=space_id,
            user_query=analysis.user_query,
            intent=analysis.intent,
            named_modalities=list(analysis.named_modalities),
            context_requirements=list(analysis.context_requirements),
            time_window=time_window,
        )
        snapshot = self.get_snapshot(
            space_id,
            request_context=request_context,
            force_refresh=force_refresh,
        )
        privacy_policy = _select_privacy_policy(snapshot, space_id)

        wanted_modalities = set(analysis.named_modalities)
        wanted_context_types: set[str] = set()
        for requirement in analysis.context_requirements:
            wanted_context_types |= _REQUIREMENT_CONTEXT_TYPES.get(requirement, set())

        relevant_sensors = [
            _score_sensor(sensor, wanted_modalities, wanted_context_types)
            for sensor in snapshot.sensors
        ]

        available_context_apis = sorted(
            api.api_name
            for api in snapshot.context_apis
            if api.available and not api.raw_access
        )

        coverage_gaps = _coverage_gaps(
            analysis=analysis,
            snapshot=snapshot,
            wanted_context_types=wanted_context_types,
        )

        notes: list[str] = []
        if not any(s.relevance == "primary" and s.available for s in relevant_sensors):
            notes.append("No primary sensor is available for this query; planning relies on general context.")

        return RoomCapabilityContext(
            query_id=query_id,
            space_id=space_id,
            snapshot_id=snapshot.snapshot_id,
            relevant_sensors=relevant_sensors,
            available_context_apis=available_context_apis,
            privacy_policy=privacy_policy,
            coverage_gaps=coverage_gaps,
            time_window=time_window,
            notes=notes,
        )


def _select_privacy_policy(snapshot: CityOSCapabilitySnapshot, space_id: str) -> PrivacyPolicyCapability:
    if snapshot.privacy_policies:
        return snapshot.privacy_policies[0]
    return PrivacyPolicyCapability(policy_id=f"{space_id}_default")


def _score_sensor(
    sensor: SensorCapability,
    wanted_modalities: set[str],
    wanted_context_types: set[str],
) -> RelevantSensorCapability:
    supports_wanted_type = bool(set(sensor.supported_context_types) & wanted_context_types)
    modality_match = sensor.modality in wanted_modalities

    if not sensor.available:
        relevance = "unavailable"
        reason = "Sensor is marked unavailable in the capability snapshot."
    elif modality_match or (supports_wanted_type and wanted_context_types):
        relevance = "primary"
        reason = "Modality or supported context type matches the query requirements."
    elif sensor.modality == "fusion" or "room_state" in sensor.supported_context_types:
        relevance = "supporting"
        reason = "Fused/room-state sensor can corroborate the primary evidence."
    else:
        relevance = "supporting"
        reason = "Not directly targeted but available for corroboration."

    return RelevantSensorCapability(
        sensor_id=sensor.sensor_id,
        modality=sensor.modality,
        relevance=relevance,  # type: ignore[arg-type]
        available=sensor.available,
        status=sensor.status,
        reason=reason,
        supported_context_types=list(sensor.supported_context_types),
        coverage_zones=list(sensor.coverage_zones),
        blind_spots=list(sensor.blind_spots),
        allowed_api_names=list(sensor.allowed_api_names),
        restricted_api_names=list(sensor.restricted_api_names),
        limitations=list(sensor.limitations),
    )


def _coverage_gaps(
    *,
    analysis: QueryAnalysis,
    snapshot: CityOSCapabilitySnapshot,
    wanted_context_types: set[str],
) -> list[str]:
    gaps: list[str] = []
    available_types: set[str] = set()
    available_modalities: set[str] = set()
    for sensor in snapshot.sensors:
        if not sensor.available:
            continue
        available_types |= set(sensor.supported_context_types)
        available_modalities.add(sensor.modality)

    for context_type in sorted(wanted_context_types):
        if context_type not in available_types:
            gaps.append(f"No available sensor supports '{context_type}' context for this space.")

    for modality in analysis.named_modalities:
        if modality not in available_modalities:
            gaps.append(f"Requested modality '{modality}' has no available sensor in this space.")

    lower_query = analysis.user_query.lower()
    if "under the table" in lower_query:
        if not any("under tables" in blind_spot.lower() for sensor in snapshot.sensors for blind_spot in sensor.blind_spots):
            return gaps
        gaps.append("Requested area 'under the table' is a declared camera blind spot in this space.")
    if "who was speaking" in lower_query or "tell me who was speaking" in lower_query:
        gaps.append("Speaker identity is unavailable: microphones expose activity and coarse source zones only.")
    if "raw video" in lower_query:
        gaps.append("Raw video export is restricted by default privacy policy.")
    if "raw audio" in lower_query or "transcript" in lower_query or "speaking" in lower_query and "who" in lower_query:
        gaps.append("Raw audio, unrestricted transcription, and speaker identification are restricted.")
    if "injured" in lower_query or "injury" in lower_query:
        gaps.append("Medical or injury diagnosis is outside the supported derived-context boundary.")

    return gaps
