"""Data-only protocol template registry.

The fresh classifier architecture keeps built-in templates as stored protocol
assets, but this package no longer exposes scoring, ranking, matching, or
reuse-build helpers. Future mapping code should consume ``Template`` objects
and make its own explicit decisions.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from tracefix.protocol_templates import (
    attendance_verification,
    fan_in_decision,
    producer_consumer,
    sequential_handoff,
    traffic_signal_coordination,
    verifier_approver,
)
from tracefix.protocol_templates.template import Template

_TEMPLATE_MODULES = (
    fan_in_decision,
    sequential_handoff,
    verifier_approver,
    producer_consumer,
    attendance_verification,
    traffic_signal_coordination,
)

_BY_ID: dict[str, object] = {
    str(module.PATTERN_ID): module for module in _TEMPLATE_MODULES
}
_GENERATED_TEMPLATES: dict[str, Template] = {}

_BUILTIN_TEMPLATE_ATTRIBUTES: dict[str, dict[str, Any]] = {
    "attendance_verification": {
        "coordination_patterns": ["Observer", "Verification"],
        "number_of_agents": 2,
        "agent_roles": ["observer", "verifier"],
        "communication_flow": ["observe", "submit_evidence", "verify", "report"],
        "limitations": ["insufficient_evidence_must_be_reported"],
        "number_of_resources": 2,
        "number_of_channels": 2,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "fan_in_decision": {
        "coordination_patterns": ["Split-and-Merge", "Majority Voting"],
        "number_of_agents": None,
        "agent_roles": ["evidence_agent", "decision_agent"],
        "communication_flow": ["submit_result", "wait_for_all_sources", "decide"],
        "limitations": ["decision_waits_for_every_source"],
        "number_of_resources": 0,
        "number_of_channels": None,
        "parameterizable_fields": ["number_of_agents", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "producer_consumer": {
        "coordination_patterns": ["Producer-Consumer"],
        "number_of_agents": 2,
        "agent_roles": ["producer", "consumer"],
        "communication_flow": ["produce", "send", "receive", "consume"],
        "limitations": ["no_acknowledgement_required"],
        "number_of_resources": 1,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "sequential_handoff": {
        "coordination_patterns": ["Sequential Handoff"],
        "number_of_agents": 2,
        "agent_roles": ["upstream_agent", "downstream_agent"],
        "communication_flow": ["work", "handoff", "receive", "continue"],
        "limitations": ["no_acknowledgement_required"],
        "number_of_resources": 2,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "traffic_signal_coordination": {
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Task Prioritization",
            "Queue-Based Scheduling",
            "Reservation",
        ],
        "number_of_agents": None,
        "agent_roles": [],
        "communication_flow": [],
        "limitations": [
            "only_one_resource_holder_at_a_time",
            "priority_requests_must_be_handled",
            "ordinary_requests_must_not_starve",
            "request_before_entering",
            "release_after_completion",
        ],
        "number_of_resources": 1,
        "number_of_channels": None,
        "parameterizable_fields": ["number_of_agents", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "verifier_approver": {
        "coordination_patterns": ["Verification", "Request-Grant"],
        "number_of_agents": 2,
        "agent_roles": ["worker", "verifier"],
        "communication_flow": ["submit", "verify", "approve_or_reject", "return_verdict"],
        "limitations": ["verifier_must_return_verdict"],
        "number_of_resources": 2,
        "number_of_channels": 2,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
}


def list_pattern_ids() -> list[str]:
    """Return registered built-in and in-memory generated template IDs."""

    return list(_BY_ID) + list(_GENERATED_TEMPLATES)


def get_template_metadata(pattern_id: str) -> dict[str, Any]:
    """Return stored metadata for a built-in template.

    This is metadata only. It does not imply the template matched the request or
    is safe to reuse.
    """

    if pattern_id in _GENERATED_TEMPLATES:
        payload = _GENERATED_TEMPLATES[pattern_id].to_dict()
        payload["pattern_id"] = payload["template_id"]
        payload.setdefault("description", payload["name_of_template"])
        return payload

    module = _BY_ID.get(pattern_id)
    if module is None:
        raise KeyError(f"Unknown coordination pattern: {pattern_id!r}")
    metadata = getattr(module, "TEMPLATE_METADATA", None)
    if isinstance(metadata, dict):
        payload = deepcopy(metadata)
    else:
        payload = {
            "pattern_id": str(getattr(module, "PATTERN_ID", pattern_id)),
            "description": str(getattr(module, "DESCRIPTION", "")),
        }
    payload.setdefault("pattern_id", str(getattr(module, "PATTERN_ID", pattern_id)))
    payload.setdefault("description", str(getattr(module, "DESCRIPTION", "")))
    payload.update({**_BUILTIN_TEMPLATE_ATTRIBUTES.get(pattern_id, {}), **payload})
    payload.setdefault("coordination_patterns", [])
    payload.setdefault("number_of_agents", None)
    payload.setdefault("agent_roles", [])
    payload.setdefault("communication_flow", [])
    payload.setdefault("limitations", [])
    payload.setdefault("number_of_resources", None)
    payload.setdefault("number_of_channels", None)
    payload.setdefault("parameterizable_fields", [])
    payload.setdefault("adaptable_fields", [])
    payload.setdefault("fatal_mismatch_fields", ["coordination_patterns"])
    return payload


def list_template_metadata() -> list[dict[str, Any]]:
    """Return metadata for all built-in templates."""

    return [get_template_metadata(pattern_id) for pattern_id in list_pattern_ids()]


def get_template(pattern_id: str) -> Template:
    """Return a data-only Template object for a built-in or generated template."""

    metadata = get_template_metadata(pattern_id)
    return Template(
        template_id=str(metadata.get("pattern_id") or pattern_id),
        name_of_template=str(
            metadata.get("name_of_template")
            or metadata.get("description")
            or pattern_id
        ),
        coordination_patterns=list(metadata.get("coordination_patterns") or []),
        number_of_agents=metadata.get("number_of_agents"),
        agent_roles=list(metadata.get("agent_roles") or []),
        communication_flow=list(metadata.get("communication_flow") or []),
        limitations=list(metadata.get("limitations") or []),
        number_of_resources=metadata.get("number_of_resources"),
        number_of_channels=metadata.get("number_of_channels"),
        parameterizable_fields=list(metadata.get("parameterizable_fields") or []),
        adaptable_fields=list(metadata.get("adaptable_fields") or []),
        fatal_mismatch_fields=list(metadata.get("fatal_mismatch_fields") or ["coordination_patterns"]),
    )


def build_template_from_metadata(metadata: dict[str, Any]) -> Template:
    """Validate generated template metadata and return a Template object.

    Generated IDs are deterministic from the structural metadata unless an
    explicit template_id is already provided by trusted harness code.
    """

    if not isinstance(metadata, dict):
        raise ValueError("generated template metadata must be a mapping")
    template_id = str(metadata.get("template_id") or "").strip() or _generated_template_id(metadata)
    return Template(
        template_id=template_id,
        name_of_template=str(metadata.get("name_of_template") or "").strip(),
        coordination_patterns=list(metadata.get("coordination_patterns") or []),
        number_of_agents=metadata.get("number_of_agents"),
        agent_roles=list(metadata.get("agent_roles") or []),
        communication_flow=list(metadata.get("communication_flow") or []),
        limitations=list(metadata.get("limitations") or []),
        number_of_resources=metadata.get("number_of_resources"),
        number_of_channels=metadata.get("number_of_channels"),
        parameterizable_fields=list(metadata.get("parameterizable_fields") or []),
        adaptable_fields=list(metadata.get("adaptable_fields") or []),
        fatal_mismatch_fields=list(metadata.get("fatal_mismatch_fields") or ["coordination_patterns"]),
    )


def register_template(template: Template) -> None:
    """Register a generated Template in the active in-memory registry."""

    template_id = template.get_template_id()
    if template_id in _BY_ID or template_id in _GENERATED_TEMPLATES:
        raise ValueError(f"template_id already registered: {template_id}")
    _GENERATED_TEMPLATES[template_id] = Template.from_dict(template.to_dict())


def clear_generated_templates_for_tests() -> None:
    """Clear generated templates. Intended for tests only."""

    _GENERATED_TEMPLATES.clear()


def _generated_template_id(metadata: dict[str, Any]) -> str:
    stable_payload = {
        "name_of_template": metadata.get("name_of_template"),
        "coordination_patterns": metadata.get("coordination_patterns") or [],
        "number_of_agents": metadata.get("number_of_agents"),
        "agent_roles": metadata.get("agent_roles") or [],
        "communication_flow": metadata.get("communication_flow") or [],
        "limitations": metadata.get("limitations") or [],
        "number_of_resources": metadata.get("number_of_resources"),
        "number_of_channels": metadata.get("number_of_channels"),
    }
    digest = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"generated_{digest}"


__all__ = [
    "Template",
    "build_template_from_metadata",
    "clear_generated_templates_for_tests",
    "get_template",
    "get_template_metadata",
    "list_pattern_ids",
    "list_template_metadata",
    "register_template",
]
