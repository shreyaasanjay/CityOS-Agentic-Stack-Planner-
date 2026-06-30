"""Validation and formatting helpers for future TraceFix integration."""

from __future__ import annotations

from typing import Any

from .schemas import TraceFixTaskSpec

ALLOWED_MODALITIES = {"video", "radar", "wifi", "audio", "fusion", "context"}
ALLOWED_CANDIDATE_HARNESSES = {
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
    "temporal_consistency_harness",
    "cross_modal_consistency_harness",
    "pipeline_diagnostic_harness",
    "answer_synthesis_harness",
}
REQUIRED_OUTPUT_FIELDS = {"answer", "confidence", "evidence_refs", "caveats"}
REQUIRED_OUTPUT_CONTRACT_KEYS = {"required_fields", "field_types"}
REQUIRED_PRIVACY_POLICY_KEYS = {"privacy_scope", "raw_sensor_access_allowed", "identity_inference_allowed"}
REQUIRED_VALIDATION_POLICY_KEYS = {"llm_proposal_untrusted", "bounded_time_windows_required", "schema_validation_required"}


def validate_tracefix_task_spec(spec: TraceFixTaskSpec) -> list[str]:
    errors: list[str] = []

    if not spec.task_id:
        errors.append("task_id is required.")
    if not spec.query_id:
        errors.append("query_id is required.")
    if not spec.user_query:
        errors.append("user_query is required.")
    if not spec.required_modalities:
        errors.append("required_modalities must be non-empty.")
    if not spec.candidate_harnesses:
        errors.append("candidate_harnesses must be non-empty.")
    if not spec.application_goal:
        errors.append("application_goal must be present.")
    if not spec.evidence_plan:
        errors.append("evidence_plan must be present.")
    if not spec.answer_packet_requirements:
        errors.append("answer_packet_requirements must be present.")
    if not spec.privacy_policy:
        errors.append("privacy_policy must be present.")
    if not spec.validation_policy:
        errors.append("validation_policy must be present.")
    if not spec.escalation_conditions:
        errors.append("escalation_conditions must be non-empty.")
    if not spec.forbidden_claims:
        errors.append("forbidden_claims must be non-empty.")
    if "privacy_scope" not in spec.output_contract.get("required_fields", []):
        errors.append("output_contract.required_fields must include privacy_scope.")
    if "limitations" not in spec.output_contract.get("required_fields", []):
        errors.append("output_contract.required_fields must include limitations.")

    contract = spec.output_contract or {}
    missing_contract_keys = sorted(REQUIRED_OUTPUT_CONTRACT_KEYS - set(contract))
    if missing_contract_keys:
        errors.append(
            "output_contract is missing required keys: " + ", ".join(missing_contract_keys) + "."
        )
    else:
        required_fields = contract.get("required_fields")
        field_types = contract.get("field_types")
        if not isinstance(required_fields, list):
            errors.append("output_contract.required_fields must be a list.")
        if not isinstance(field_types, dict):
            errors.append("output_contract.field_types must be an object.")
        if isinstance(required_fields, list):
            missing_required_fields = sorted(REQUIRED_OUTPUT_FIELDS - set(required_fields))
            if missing_required_fields:
                errors.append(
                    "output_contract.required_fields is missing: "
                    + ", ".join(missing_required_fields)
                    + "."
                )
        if isinstance(field_types, dict):
            missing_field_types = sorted(REQUIRED_OUTPUT_FIELDS - set(field_types))
            if missing_field_types:
                errors.append(
                    "output_contract.field_types is missing: "
                    + ", ".join(missing_field_types)
                    + "."
                )

    for harness_name in spec.candidate_harnesses:
        if harness_name not in ALLOWED_CANDIDATE_HARNESSES:
            errors.append(f"Unknown candidate harness: {harness_name}.")

    for modality in spec.required_modalities:
        if modality not in ALLOWED_MODALITIES:
            errors.append(f"Unknown required modality: {modality}.")

    privacy_policy = spec.privacy_policy or {}
    missing_privacy_policy_keys = sorted(REQUIRED_PRIVACY_POLICY_KEYS - set(privacy_policy))
    if missing_privacy_policy_keys:
        errors.append("privacy_policy is missing required keys: " + ", ".join(missing_privacy_policy_keys) + ".")

    validation_policy = spec.validation_policy or {}
    missing_validation_policy_keys = sorted(REQUIRED_VALIDATION_POLICY_KEYS - set(validation_policy))
    if missing_validation_policy_keys:
        errors.append(
            "validation_policy is missing required keys: " + ", ".join(missing_validation_policy_keys) + "."
        )

    return errors


def tracefix_task_to_prompt(spec: TraceFixTaskSpec) -> str:
    time_window_lines = []
    for window in spec.time_windows:
        label = window.label or "time_window"
        time_window_lines.append(f"- {label}: start={window.start or 'n/a'}, end={window.end or 'n/a'}")
    if not time_window_lines:
        time_window_lines.append("- none specified")

    output_contract_lines = _format_output_contract(spec.output_contract)
    application_goal_lines = _format_nested_object(spec.application_goal)
    evidence_plan_lines = _format_nested_object(spec.evidence_plan)
    answer_requirement_lines = _format_nested_object(spec.answer_packet_requirements)
    privacy_policy_lines = _format_nested_object(spec.privacy_policy)
    validation_policy_lines = _format_nested_object(spec.validation_policy)
    caveat_lines = [f"- {caveat}" for caveat in spec.caveats] if spec.caveats else ["- none"]

    return "\n".join(
        [
            "TraceFix Task Stub",
            f"Task ID: {spec.task_id}",
            f"Query ID: {spec.query_id}",
            f"User Query: {spec.user_query}",
            f"Space ID: {spec.space_id or 'n/a'}",
            f"Reason: {spec.reason or 'n/a'}",
            "Required Modalities: " + ", ".join(spec.required_modalities),
            "Candidate Harnesses: " + ", ".join(spec.candidate_harnesses),
            "Time Windows:",
            *time_window_lines,
            "Application Goal:",
            *application_goal_lines,
            "Evidence Plan:",
            *evidence_plan_lines,
            "Answer Packet Requirements:",
            *answer_requirement_lines,
            "Privacy Policy:",
            *privacy_policy_lines,
            "Validation Policy:",
            *validation_policy_lines,
            "Allowed Claims: " + ", ".join(spec.allowed_claims or ["none"]),
            "Forbidden Claims: " + ", ".join(spec.forbidden_claims or ["none"]),
            "Escalation Conditions: " + ", ".join(spec.escalation_conditions or ["none"]),
            "Output Contract:",
            *output_contract_lines,
            "Reasoning Summary: " + (spec.reasoning_summary or "n/a"),
            "Caveats:",
            *caveat_lines,
            f"Target TraceFix Path: {spec.target_tracefix_path}",
            "Execution Note: This adapter does not invoke real TraceFix.",
        ]
    )


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


def _format_nested_object(payload: dict[str, Any]) -> list[str]:
    if not payload:
        return ["- none"]
    lines: list[str] = []
    for key in sorted(payload):
        lines.append(f"- {key}: {payload[key]}")
    return lines
