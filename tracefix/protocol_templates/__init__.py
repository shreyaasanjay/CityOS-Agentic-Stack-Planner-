"""Protocol template registry.

Each template module must export:
  PATTERN_ID: str
  DESCRIPTION: str
  classify(task_lower, agent_count_hint, keywords) -> float
  build_template(params) -> tuple[dict, str]   # (ir_data, Protocol.tla)

Templates may also export TEMPLATE_METADATA to describe whether they are
fixed-shape or parameterized.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Any

from tracefix.pipeline.pipeline.validator import normalize_ir
from tracefix.protocol_templates import (
    attendance_verification,
    fan_in_decision,
    producer_consumer,
    sequential_handoff,
    traffic_signal_coordination,
    verifier_approver,
)


@dataclass(frozen=True)
class TemplateMetadata:
    pattern_id: str
    description: str
    shape: str = "fixed"
    family: str = "generic_coordination"
    mode: str = "fixed"
    supports_partial_repair: bool = False
    supported_variants: list[str] | None = None
    required_inputs: list[str] | None = None
    adaptable_sections: list[str] | None = None
    forbidden_repair_sections: list[str] | None = None
    safety_invariants: list[str] | None = None
    generated_agent_pattern: str = "template-specific agents"
    generated_channel_pattern: str = "template-specific channels"
    generated_resource_pattern: str = "template-specific resources"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["supported_variants"] = payload.get("supported_variants") or []
        payload["required_inputs"] = payload.get("required_inputs") or []
        payload["adaptable_sections"] = payload.get("adaptable_sections") or []
        payload["forbidden_repair_sections"] = payload.get("forbidden_repair_sections") or []
        payload["safety_invariants"] = payload.get("safety_invariants") or []
        return payload


# Ordered list of registered templates. The classifier iterates this list and
# returns the highest-confidence match above threshold.
_TEMPLATES = [
    fan_in_decision,
    sequential_handoff,
    verifier_approver,
    producer_consumer,
    attendance_verification,
    traffic_signal_coordination,
]

_BY_ID: dict[str, object] = {m.PATTERN_ID: m for m in _TEMPLATES}


def list_pattern_ids() -> list[str]:
    return [m.PATTERN_ID for m in _TEMPLATES]


def get_template_metadata(pattern_id: str) -> dict[str, Any]:
    mod = _BY_ID.get(pattern_id)
    if mod is None:
        raise KeyError(f"Unknown coordination pattern: {pattern_id!r}")
    metadata = getattr(mod, "TEMPLATE_METADATA", None)
    if isinstance(metadata, dict):
        return deepcopy(metadata)
    return TemplateMetadata(
        pattern_id=str(getattr(mod, "PATTERN_ID", pattern_id)),
        description=str(getattr(mod, "DESCRIPTION", "")),
        shape="fixed",
    ).to_dict()


def list_template_metadata() -> list[dict[str, Any]]:
    return [get_template_metadata(m.PATTERN_ID) for m in _TEMPLATES]


def classify_all(
    task_lower: str,
    agent_count_hint: int,
    keywords: frozenset[str],
) -> list[tuple[str, float]]:
    """Return sorted list of (pattern_id, confidence) for all templates."""
    results = [
        (m.PATTERN_ID, m.classify(task_lower, agent_count_hint, keywords))
        for m in _TEMPLATES
    ]
    return sorted(results, key=lambda x: x[1], reverse=True)


def build_ir_for_pattern(pattern_id: str, params: dict) -> dict:
    """Return the IR data dict for the given pattern."""
    mod = _BY_ID.get(pattern_id)
    if mod is None:
        raise KeyError(f"Unknown coordination pattern: {pattern_id!r}")
    ir_data, _ = mod.build_template(params)
    return normalize_ir(ir_data)


def build_protocol_for_pattern(pattern_id: str, params: dict) -> str:
    """Return the Protocol.tla string for the given pattern."""
    mod = _BY_ID.get(pattern_id)
    if mod is None:
        raise KeyError(f"Unknown coordination pattern: {pattern_id!r}")
    _, protocol_tla = mod.build_template(params)
    return protocol_tla


def build_template(pattern_id: str, params: dict) -> tuple[dict, str]:
    """Return (ir_data, protocol_tla) for the given pattern."""
    mod = _BY_ID.get(pattern_id)
    if mod is None:
        raise KeyError(f"Unknown coordination pattern: {pattern_id!r}")
    ir_data, protocol_tla = mod.build_template(params)
    return normalize_ir(ir_data), protocol_tla
