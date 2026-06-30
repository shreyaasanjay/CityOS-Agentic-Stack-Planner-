"""Validation for structured mock CityOS context objects."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .agent_protocol import ValidationResult


def validate_cityos_context(
    output: Dict[str, Any],
    expected_context_types: List[str],
    query_space_id: Optional[str],
    requested_timestamp: Optional[str],
) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(output, dict):
        return ValidationResult(valid=False, errors=["Context output must be a dict."])

    context_type = output.get("context_type")
    if not context_type:
        errors.append("context_type is required.")
    elif expected_context_types and context_type not in expected_context_types:
        errors.append(
            "context_type must be one of: " + ", ".join(expected_context_types) + "."
        )

    space_id = output.get("space_id")
    if not space_id:
        errors.append("space_id is required.")
    elif query_space_id and space_id != query_space_id:
        errors.append("space_id does not match the requested space.")

    timestamp = output.get("timestamp")
    if not timestamp:
        errors.append("timestamp is required.")
    elif requested_timestamp and requested_timestamp not in str(timestamp):
        observed_at = output.get("value", {}).get("observed_at") if isinstance(output.get("value"), dict) else None
        if requested_timestamp != observed_at:
            warnings.append("Context timestamp does not exactly match the requested timestamp.")

    confidence = output.get("confidence")
    if not isinstance(confidence, (int, float)):
        errors.append("confidence must be numeric.")
    else:
        if confidence < 0 or confidence > 1:
            errors.append("confidence must be between 0 and 1.")
        elif confidence < 0.6:
            warnings.append("confidence is below 0.6.")

    evidence_refs = output.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        errors.append("evidence_refs must be a list.")
    elif not evidence_refs:
        warnings.append("evidence_refs is empty.")

    if not output.get("privacy_scope"):
        errors.append("privacy_scope is required.")

    value = output.get("value")
    if not isinstance(value, dict):
        errors.append("value must be a dict.")
    elif context_type == "occupancy" and "count" in value:
        count = value.get("count")
        if not isinstance(count, int) or count < 0:
            errors.append("occupancy value.count must be a nonnegative integer.")

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        normalized_output=output if not errors else None,
    )
