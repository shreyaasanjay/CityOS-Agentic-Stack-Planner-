"""Protocol template registry.

Each template module must export:
  PATTERN_ID: str
  DESCRIPTION: str
  classify(task_lower, agent_count_hint, keywords) -> float
  build_template(params) -> tuple[dict, str]   # (ir_data, Protocol.tla)
"""
from __future__ import annotations

from tracefix.pipeline.pipeline.validator import normalize_ir
from tracefix.protocol_templates import (
    attendance_verification,
    fan_in_decision,
    producer_consumer,
    sequential_handoff,
    verifier_approver,
)

# Ordered list of registered templates. The classifier iterates this list and
# returns the highest-confidence match above threshold.
_TEMPLATES = [
    fan_in_decision,
    sequential_handoff,
    verifier_approver,
    producer_consumer,
    attendance_verification,
]

_BY_ID: dict[str, object] = {m.PATTERN_ID: m for m in _TEMPLATES}


def list_pattern_ids() -> list[str]:
    return [m.PATTERN_ID for m in _TEMPLATES]


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
