"""Build bounded context windows for single-agent execution."""

from __future__ import annotations

from .cityos_mock import MockCityOSClient
from .schemas import ContextWindow, RouteDecision, TellMeQuery

PRIVACY_CONSTRAINTS = [
    "Use only CityOS-approved structured context, not raw sensor data.",
    "Prefer timestamped JSON context objects over raw artifacts.",
    "Return evidence references for factual claims when available.",
    "Do not claim identity unless the context explicitly supports identity or track continuity.",
    "Escalate to TraceFix if the query requires multiple agents, multiple modalities, long temporal reasoning, diagnostic recovery, or cross-agent coordination.",
    "Do not overstate confidence.",
]


def build_context_window(
    query: TellMeQuery,
    route_decision: RouteDecision,
    cityos_client: MockCityOSClient,
) -> ContextWindow:
    available_context_objects = cityos_client.preview_context(
        space_id=query.space_id,
        context_type=_context_type_for_tool(route_decision.selected_tool),
        timestamp=query.timestamp or (route_decision.time_window.start if route_decision.time_window else None),
        limit=3,
    )
    time_windows = [route_decision.time_window] if route_decision.time_window else []
    return ContextWindow(
        query_id=query.query_id,
        user_query=query.user_query,
        space_id=query.space_id,
        selected_intent=route_decision.intent,
        relevant_time_windows=time_windows,
        available_tools=cityos_client.available_tools(),
        routing_decision=route_decision,
        privacy_constraints=PRIVACY_CONSTRAINTS,
        instructions=PRIVACY_CONSTRAINTS,
        available_context_objects=available_context_objects,
    )


def _context_type_for_tool(tool_name: str | None) -> str | None:
    if tool_name == "get_occupancy_context":
        return "occupancy"
    if tool_name == "get_motion_context":
        return "motion"
    if tool_name == "get_audio_context":
        return "audio"
    if tool_name == "get_room_state":
        return "room_state"
    return None
