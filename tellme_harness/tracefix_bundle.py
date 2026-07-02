"""Dry-run TraceFix bundle generation for complex queries."""

from __future__ import annotations

from typing import Any

from .schemas import TraceFixTaskSpec
from .tracefix_adapter import validate_tracefix_task_spec

TOOL_REGISTRY = {
    "video_context_harness": ["get_video_context"],
    "radar_context_harness": ["get_radar_context"],
    "wifi_context_harness": ["get_wifi_context"],
    "audio_context_harness": ["get_audio_context"],
    "occupancy_context_harness": ["get_occupancy_context"],
    "motion_context_harness": ["get_motion_context"],
    "room_state_context_harness": ["get_room_state"],
    "general_context_harness": ["cityos_context_lookup"],
    "timestamp_retrieval_harness": ["get_context_by_time_window"],
    "event_retrieval_harness": ["get_event_context"],
    "entry_event_harness": ["get_event_context"],
    "identity_free_tracking_harness": ["get_context_by_time_window"],
    "fall_detection_harness": ["get_event_context"],
    "radar_motion_harness": ["get_radar_context"],
    "wifi_presence_harness": ["get_wifi_context"],
    "temporal_consistency_harness": ["compare_time_windows"],
    "cross_modal_consistency_harness": ["compare_modalities"],
    "pipeline_diagnostic_harness": ["inspect_query_logs"],
    "answer_synthesis_harness": ["synthesize_answer_packet"],
}
MODALITY_HARNESSES = {
    "video_context_harness",
    "radar_context_harness",
    "wifi_context_harness",
    "audio_context_harness",
    "occupancy_context_harness",
    "motion_context_harness",
    "room_state_context_harness",
    "general_context_harness",
    "timestamp_retrieval_harness",
    "event_retrieval_harness",
    "entry_event_harness",
    "identity_free_tracking_harness",
    "fall_detection_harness",
    "radar_motion_harness",
    "wifi_presence_harness",
}
CONSISTENCY_HARNESSES = {
    "temporal_consistency_harness",
    "cross_modal_consistency_harness",
}
MODALITY_CHANNEL_LABELS = {
    "video_context_harness": "video_findings",
    "radar_context_harness": "radar_findings",
    "wifi_context_harness": "wifi_findings",
    "audio_context_harness": "audio_findings",
    "occupancy_context_harness": "occupancy_findings",
    "motion_context_harness": "motion_findings",
    "room_state_context_harness": "room_state_findings",
    "general_context_harness": "general_context_findings",
    "timestamp_retrieval_harness": "timestamp_findings",
    "event_retrieval_harness": "event_findings",
    "entry_event_harness": "entry_event_findings",
    "identity_free_tracking_harness": "tracking_findings",
    "fall_detection_harness": "fall_findings",
    "radar_motion_harness": "radar_motion_findings",
    "wifi_presence_harness": "wifi_presence_findings",
}
EXTRA_CHANNEL_LABELS = {
    "pipeline_diagnostic_harness": "diagnostic_findings",
}
PRIVACY_NOTES = [
    "Use only CityOS-approved structured context.",
    "Do not use or request raw sensor data.",
    "All inputs are CityOS structured context summaries, not raw video, audio, radar, or Wi-Fi artifacts.",
]


def build_tracefix_bundle(spec: TraceFixTaskSpec) -> dict:
    errors = validate_tracefix_task_spec(spec)
    if errors:
        raise ValueError("Invalid TraceFixTaskSpec: " + "; ".join(errors))

    agent_names = sorted(set(spec.candidate_harnesses) | {"answer_synthesis_harness"})
    agents = [{"name": agent_name, "role": _describe_agent_role(agent_name)} for agent_name in agent_names]
    channels = _build_channels(agent_names)
    tool_manifest = {agent_name: TOOL_REGISTRY[agent_name] for agent_name in agent_names}
    resources = _build_resources(spec)
    task_description = _build_task_description(spec, agent_names)
    notes = [
        "Dry-run bundle only; do not invoke real TraceFix-main.",
        "This bundle is an inspectable artifact representing future integration inputs.",
        *PRIVACY_NOTES,
    ]

    return {
        "query_id": spec.query_id,
        "task_id": spec.task_id,
        "user_query": spec.user_query,
        "task_description": task_description,
        "agents": agents,
        "resources": resources,
        "channels": channels,
        "tool_manifest": tool_manifest,
        "output_contract": spec.output_contract,
        "notes": notes,
    }


def _build_task_description(spec: TraceFixTaskSpec, agent_names: list[str]) -> str:
    time_windows = []
    for window in spec.time_windows:
        label = window.label or "time_window"
        time_windows.append(f"- {label}: start={window.start or 'n/a'}, end={window.end or 'n/a'}")
    if not time_windows:
        time_windows.append("- none specified")

    return "\n".join(
        [
            "# TraceFix Dry-Run Task",
            "",
            "## User Query",
            spec.user_query,
            "",
            "## Scope",
            f"- space_id: {spec.space_id or 'n/a'}",
            "- privacy boundary: CityOS structured context only",
            "- raw sensor policy: no raw sensor data may be used; inputs are CityOS structured context, not raw sensor data",
            "",
            "## Time Windows",
            *time_windows,
            "",
            "## Required Modalities",
            "- " + ", ".join(spec.required_modalities),
            "",
            "## Candidate Harnesses",
            *[f"- {name}" for name in agent_names],
            "",
            "## Application Goal",
            *_format_generic_section(spec.application_goal),
            "",
            "## Evidence Plan",
            *_format_generic_section(spec.evidence_plan),
            "",
            "## Answer Packet Requirements",
            *_format_generic_section(spec.answer_packet_requirements),
            "",
            "## Allowed Claims",
            "- " + ", ".join(spec.allowed_claims or ["none"]),
            "",
            "## Forbidden Claims",
            "- " + ", ".join(spec.forbidden_claims or ["none"]),
            "",
            "## Expected Output Contract",
            *_format_output_contract(spec.output_contract),
            "",
            "## Privacy Constraints",
            *[f"- {note}" for note in PRIVACY_NOTES],
        ]
    )


def _build_resources(spec: TraceFixTaskSpec) -> list[dict]:
    resources = [
        {
            "name": "cityos_structured_context",
            "type": "structured_context_bundle",
            "modalities": spec.required_modalities,
            "space_id": spec.space_id,
            "time_windows": [window.model_dump() for window in spec.time_windows],
        },
        {
            "name": "tellme_query_logs",
            "type": "local_query_run_directory",
            "query_id": spec.query_id,
        },
    ]
    return resources


def _build_channels(agent_names: list[str]) -> list[dict]:
    modality_agents = [name for name in agent_names if name in MODALITY_HARNESSES]
    # Non-modality, non-consistency workers (e.g. pipeline diagnostics) that still
    # need a path to synthesis so their findings are not stranded.
    other_workers = [
        name
        for name in agent_names
        if name not in MODALITY_HARNESSES
        and name not in CONSISTENCY_HARNESSES
        and name != "answer_synthesis_harness"
    ]

    if "cross_modal_consistency_harness" in agent_names:
        target = "cross_modal_consistency_harness"
    elif "temporal_consistency_harness" in agent_names:
        target = "temporal_consistency_harness"
    else:
        target = "answer_synthesis_harness"

    channels: list[dict] = []

    # Modality harnesses feed the consistency stage when present, else synthesis directly.
    for agent_name in modality_agents:
        channels.append({"from": agent_name, "to": target, "label": _channel_label(agent_name)})

    # The chosen consistency harness forwards its assessment to synthesis.
    if target != "answer_synthesis_harness":
        channels.append(
            {"from": target, "to": "answer_synthesis_harness", "label": "consistency_assessment"}
        )

    # A temporal harness alongside a cross-modal target also reports to synthesis.
    if "temporal_consistency_harness" in agent_names and target == "cross_modal_consistency_harness":
        channels.append(
            {
                "from": "temporal_consistency_harness",
                "to": "answer_synthesis_harness",
                "label": "consistency_assessment",
            }
        )

    # Diagnostic / other worker harnesses report directly to synthesis.
    for agent_name in other_workers:
        channels.append(
            {"from": agent_name, "to": "answer_synthesis_harness", "label": _channel_label(agent_name)}
        )

    return _dedupe_channels(channels)


def _dedupe_channels(channels: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for channel in channels:
        if channel["from"] == channel["to"]:
            continue  # never emit self-loops
        key = (channel["from"], channel["to"], channel["label"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(channel)
    return deduped


def _channel_label(agent_name: str) -> str:
    return (
        MODALITY_CHANNEL_LABELS.get(agent_name)
        or EXTRA_CHANNEL_LABELS.get(agent_name)
        or f"{agent_name}_findings"
    )


def _describe_agent_role(agent_name: str) -> str:
    role_map = {
        "video_context_harness": "Summarize structured video-derived scene context.",
        "radar_context_harness": "Summarize structured radar-derived motion and presence context.",
        "wifi_context_harness": "Summarize structured Wi-Fi-derived occupancy context.",
        "audio_context_harness": "Summarize structured audio-derived event context.",
        "timestamp_retrieval_harness": "Retrieve context slices aligned to relevant time windows.",
        "event_retrieval_harness": "Retrieve structured event summaries from query context.",
        "entry_event_harness": "Retrieve privacy-bounded entry event summaries.",
        "identity_free_tracking_harness": "Track continuity without making identity claims.",
        "fall_detection_harness": "Retrieve fall-like event summaries.",
        "radar_motion_harness": "Retrieve radar motion summaries for corroboration.",
        "wifi_presence_harness": "Retrieve Wi-Fi presence summaries for corroboration.",
        "occupancy_context_harness": "Retrieve occupancy-oriented structured context.",
        "motion_context_harness": "Retrieve motion-oriented structured context.",
        "room_state_context_harness": "Retrieve room-state structured context.",
        "general_context_harness": "Retrieve general CityOS structured context.",
        "temporal_consistency_harness": "Compare findings across time windows for continuity and sequencing.",
        "cross_modal_consistency_harness": "Compare findings across modalities for agreement and disagreement.",
        "pipeline_diagnostic_harness": "Inspect local query logs for diagnostic failures or missing outputs.",
        "answer_synthesis_harness": "Synthesize a final answer packet with caveats and evidence references.",
    }
    return role_map[agent_name]


def _format_output_contract(contract: dict[str, Any]) -> list[str]:
    required_fields = contract.get("required_fields", [])
    field_types = contract.get("field_types", {})
    lines = [
        "- required_fields: " + ", ".join(required_fields),
        "- field_types:",
    ]
    for field_name in sorted(field_types):
        lines.append(f"  - {field_name}: {field_types[field_name]}")
    return lines


def _format_generic_section(payload: dict[str, Any]) -> list[str]:
    if not payload:
        return ["- none"]
    return [f"- {key}: {payload[key]}" for key in sorted(payload)]
