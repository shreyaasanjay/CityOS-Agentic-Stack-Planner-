"""Build the compact TeLLMe-to-TraceFix task projection.

The full ``TraceFixTaskSpec`` remains the TeLLMe audit artifact. TraceFix receives
only the planning fields it needs, plus a query-scoped capability summary.
"""

from __future__ import annotations

from typing import Any


def build_tracefix_task_projection(
    task_spec: dict[str, Any],
    *,
    execution_brief: dict[str, Any] | None = None,
    discovery_snapshot: dict[str, Any] | None = None,
    discovery_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the compact, topology-neutral payload handed to TraceFix."""
    brief = execution_brief if isinstance(execution_brief, dict) else {}
    room_context = brief.get("room_capability_context")
    room_context = room_context if isinstance(room_context, dict) else {}
    constraints = task_spec.get("constraints")
    constraints = constraints if isinstance(constraints, dict) else {}
    policy = task_spec.get("privacy_policy")
    policy = policy if isinstance(policy, dict) else {}
    evidence_plan = task_spec.get("evidence_plan")
    evidence_plan = evidence_plan if isinstance(evidence_plan, dict) else {}
    goal = task_spec.get("application_goal")
    goal = goal if isinstance(goal, dict) else {}
    output_contract = task_spec.get("output_contract")
    output_contract = output_contract if isinstance(output_contract, dict) else {}
    answer_requirements = task_spec.get("answer_packet_requirements")
    answer_requirements = answer_requirements if isinstance(answer_requirements, dict) else {}

    return {
        "task_id": task_spec.get("task_id"),
        "query_id": task_spec.get("query_id"),
        "user_query": task_spec.get("user_query", ""),
        "intent": task_spec.get("intent", ""),
        "space_id": task_spec.get("space_id"),
        "route": task_spec.get("route"),
        "tracefix_reason": task_spec.get("tracefix_reason", ""),
        "objective": {
            "goal_type": goal.get("goal_type"),
            "success_condition": goal.get("success_condition"),
            "failure_condition": goal.get("failure_condition"),
        },
        "capabilities": _capability_summary(
            task_spec=task_spec,
            room_context=room_context,
            discovery_snapshot=discovery_snapshot,
            discovery_provenance=discovery_provenance,
        ),
        "required_modalities": list(task_spec.get("required_modalities") or []),
        "time_windows": list(task_spec.get("time_windows") or []),
        "privacy_constraints": _privacy_constraints(constraints, policy),
        "evidence_requirements": {
            "primary": list(evidence_plan.get("primary_evidence") or []),
            "supporting": list(evidence_plan.get("supporting_evidence") or []),
            "minimum_sufficient": list(evidence_plan.get("minimum_sufficient_evidence") or []),
            "conflicting_checks": list(evidence_plan.get("conflicting_evidence_checks") or []),
            "require_evidence_refs": bool(constraints.get("require_evidence_refs", True)),
        },
        "success_criteria": list(task_spec.get("success_criteria") or []),
        "claim_limits": {
            "allowed": list(task_spec.get("allowed_claims") or answer_requirements.get("allowed_claims") or []),
            "forbidden": list(task_spec.get("forbidden_claims") or answer_requirements.get("forbidden_claims") or []),
        },
        "output_contract": {
            "name": task_spec.get("output_contract_name") or "answer_packet_v1",
            "answer_type": output_contract.get("answer_type") or answer_requirements.get("answer_type"),
            "fallback_answer_type": output_contract.get("fallback_answer_type")
            or answer_requirements.get("fallback_answer_type"),
            "required_fields": list(output_contract.get("required_fields") or []),
        },
        # TraceFix uses these semantic harness labels for classification; they do
        # not assign agents, channels, or workflow ownership.
        "candidate_harnesses": list(task_spec.get("candidate_harnesses") or []),
    }


def _capability_summary(
    *,
    task_spec: dict[str, Any],
    room_context: dict[str, Any],
    discovery_snapshot: dict[str, Any] | None,
    discovery_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    available_apis = _unique_strings(room_context.get("available_context_apis"))
    selected_sensors: list[dict[str, Any]] = []
    sensor_api_names: set[str] = set()
    for sensor in room_context.get("relevant_sensors") or []:
        if not isinstance(sensor, dict) or not sensor.get("available", True):
            continue
        sensor_apis = [api for api in _unique_strings(sensor.get("allowed_api_names")) if api in available_apis]
        sensor_api_names.update(sensor_apis)
        selected_sensors.append(
            {
                "sensor_id": sensor.get("sensor_id"),
                "modality": sensor.get("modality"),
                "context_apis": sensor_apis,
            }
        )

    selected_apis = sorted(sensor_api_names) or available_apis
    snapshot = discovery_snapshot if isinstance(discovery_snapshot, dict) else {}
    provenance = discovery_provenance if isinstance(discovery_provenance, dict) else {}
    provenance_summary = {
        "snapshot_id": room_context.get("snapshot_id") or snapshot.get("snapshot_id"),
        "source": provenance.get("discovery_source") or snapshot.get("source"),
        "schema_version": provenance.get("schema_version") or snapshot.get("schema_version"),
        "validation_outcome": provenance.get("validation_outcome"),
    }
    provenance_summary = {key: value for key, value in provenance_summary.items() if value not in (None, "")}

    return {
        "required": list(task_spec.get("required_capabilities") or []),
        "context_apis": selected_apis,
        "sensors": selected_sensors,
        "coverage_gaps": _unique_strings(room_context.get("coverage_gaps")),
        "provenance": provenance_summary,
    }


def _privacy_constraints(constraints: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    forbidden = _unique_strings(policy.get("forbidden_inferences"))
    for claim in ("personal_identity", "face_identity", "speaker_identity", "unrestricted_transcription"):
        if claim not in forbidden:
            forbidden.append(claim)
    return {
        "scope": constraints.get("privacy_scope") or policy.get("privacy_scope") or "cityos_structured_context_only",
        "raw_media": "allowed" if constraints.get("raw_media_allowed", False) else "disallowed",
        "identity_inference": "allowed" if constraints.get("identity_inference_allowed", False) else "disallowed",
        "transcript_and_speaker_identity": "disallowed",
        "forbidden_claim_types": forbidden,
    }


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result
