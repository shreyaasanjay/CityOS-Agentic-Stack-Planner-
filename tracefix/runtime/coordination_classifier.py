"""Coordination-pattern classifier.

Examines a TraceFix task description and optional TeLLMe spec to determine if
a known deterministic coordination pattern applies. Returns a CoordinationPatternDecision
that includes:
  - Whether a pattern was identified (pattern_id != None)
  - Pre-built IR and Protocol.tla ready to drop into spec/
  - Template params used (for diagnostic Trial A/B/C output)
  - Failure reason when no pattern matched (for transparent fallback logging)

Classifier is FAIL-CLOSED: if anything is uncertain, it returns pattern_id=None
and falls through to full OpenCode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tracefix.protocol_templates import classify_all, build_template, get_template_metadata
from tracefix.protocol_templates.fan_in_decision import (
    detect_evidence_sources,
    has_decision_language,
)

_CONFIDENCE_THRESHOLD = 0.95
_PARTIAL_CONFIDENCE_THRESHOLD = 0.50

_SUPPORTED_PATTERN_IDS = frozenset({
    "sequential_handoff",
    "verifier_approver",
    "producer_consumer",
    "attendance_verification",
    "fan_in_decision",
    "traffic_signal_coordination",
})

# Common role / function words that hint at agent IDs in free text.
_ROLE_SPLITTER = re.compile(r"\band\b|\bthen\b|\bto\b|\bwith\b|,|;", re.I)
_AGENT_NOUN_RE = re.compile(
    r"\b(agent|worker|producer|consumer|verifier|reviewer|checker|approver|"
    r"processor|generator|sender|receiver|submitter|validator|auditor)\b",
    re.I,
)
_COORDINATION_CUES = frozenset({
    "coordinate", "coordination", "handoff", "producer", "consumer",
    "review", "verify", "approve", "reconcile", "traffic", "intersection",
    "signal", "conflict", "emergency", "all-red",
})


@dataclass
class CoordinationPatternDecision:
    """Result of assess_coordination_pattern."""

    # Core decision fields
    considered: bool = False
    """True if deterministic templates were scored for this task."""
    pattern_id: str | None = None
    """Matched pattern ID, or None if no pattern matched above threshold."""
    template_match_type: str = "none"
    pattern: str | None = None
    confidence: float = 0.0
    reason: str = ""
    fallback_reason: str = ""

    # Template outputs — set only when pattern_id is not None
    ir_data: dict[str, Any] = field(default_factory=dict)
    protocol_tla: str = ""
    template_params: dict[str, Any] = field(default_factory=dict)
    template_parameters: dict[str, Any] = field(default_factory=dict)
    partial_repair_reason: str = ""
    template_variant: str = ""
    template_metadata: dict[str, Any] = field(default_factory=dict)

    # Convenience fields extracted from ir_data
    agents: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)

    # Diagnostics
    all_scores: list[tuple[str, float]] = field(default_factory=list)
    evidence_sources_detected: list[str] = field(default_factory=list)
    evidence_source_count: int = 0
    decision_agent_id: str | None = None
    template_priority_reason: str = ""
    app_agent_count: int = 0
    monitor_count: int = 1


def assess_coordination_pattern(
    task: str,
    tellme_spec: dict[str, Any] | None = None,
) -> CoordinationPatternDecision:
    """Classify the coordination pattern for a task.

    Args:
        task: The raw task description string (from TeLLMe IR or CLI).
        tellme_spec: Optional TeLLMe structured spec dict with keys like
                     'agents', 'resources', 'channels'. When present, used to
                     derive agent count and IDs precisely instead of guessing.

    Returns:
        CoordinationPatternDecision.  If pattern_id is None, the caller should
        fall through to OpenCode.
    """
    decision = CoordinationPatternDecision()

    # --- 1. Derive agent count and IDs ------------------------------------------
    agents: list[dict] = []
    tellme_route = ""
    if tellme_spec:
        tellme_route = str(tellme_spec.get("route") or "").strip().lower()
        if isinstance(tellme_spec.get("agents"), list):
            agents = [a for a in tellme_spec["agents"] if isinstance(a, dict)]
        # Derive synthetic agents from candidate_harnesses when no explicit agents
        if not agents:
            agents = _agents_from_harnesses(tellme_spec.get("candidate_harnesses") or [])

    classify_text = _classification_text(task, tellme_spec)
    task_lower = classify_text.lower()
    evidence_sources = detect_evidence_sources(task_lower)
    decision.evidence_sources_detected = [
        source["name"] for source in evidence_sources
    ]
    decision.evidence_source_count = len(evidence_sources)

    agent_count = len(agents)
    if agent_count == 0:
        # Estimate from task text as fallback
        agent_count = _estimate_agent_count(task)

    # TeLLMe multi_agent route is a hard guarantee of ≥2 agents; don't fail-close
    # just because the task text has no agent nouns (e.g. generic placeholder text).
    if agent_count == 0 and tellme_route == "multi_agent":
        agent_count = 2

    if len(evidence_sources) >= 3 and has_decision_language(task_lower):
        agent_count = len(evidence_sources) + 1

    if tellme_route == "single_agent":
        decision.fallback_reason = "TeLLMe route is explicitly single_agent"
        return decision

    # --- 2. Extract keywords — prefer user_query over the full wrapper text ------
    # The full TeLLMe task text includes a JSON preamble that dilutes signals.
    # When a user_query is available, classify against it directly.
    keywords = _extract_keywords(task_lower)

    # --- 3. Score all templates -------------------------------------------------
    scores = classify_all(
        task_lower,
        agent_count_hint=agent_count,
        keywords=keywords,
    )
    scores = _apply_structured_score_hints(scores, tellme_spec)
    decision.all_scores = scores

    best_id, best_conf = scores[0] if scores else (None, 0.0)
    has_coordination_cue = any(cue in task_lower for cue in _COORDINATION_CUES)
    decision.considered = bool(
        tellme_route == "multi_agent"
        or agent_count >= 2
        or len(evidence_sources) >= 3
        or has_coordination_cue
        or best_conf > 0.0
    )
    if not decision.considered:
        decision.fallback_reason = (
            "no multi-agent route, coordination cues, or template score detected"
        )
        return decision

    if best_id is None or best_conf < _PARTIAL_CONFIDENCE_THRESHOLD:
        decision.fallback_reason = (
            f"best pattern {best_id!r} scored {best_conf:.2f} "
            f"(partial threshold {_PARTIAL_CONFIDENCE_THRESHOLD}); falling back to OpenCode"
        )
        return decision

    if best_id not in _SUPPORTED_PATTERN_IDS:
        decision.fallback_reason = (
            f"pattern {best_id!r} is not in supported set; falling back to OpenCode"
        )
        return decision

    metadata = get_template_metadata(best_id)
    match_type = "parameterized" if metadata.get("shape") == "parameterized" else "exact"
    if best_conf <= _CONFIDENCE_THRESHOLD:
        if metadata.get("supports_partial_repair"):
            match_type = "partial"
            decision.partial_repair_reason = (
                f"pattern family {best_id!r} scored {best_conf:.2f}, not above exact "
                f"threshold {_CONFIDENCE_THRESHOLD} but above partial threshold "
                f"{_PARTIAL_CONFIDENCE_THRESHOLD}"
            )
        else:
            decision.fallback_reason = (
                f"best pattern {best_id!r} scored {best_conf:.2f}; template does "
                "not support partial repair, falling back to OpenCode"
            )
            return decision

    # --- 4. Build template params from agent IDs --------------------------------
    try:
        params = _build_params(best_id, agents, task_lower, tellme_spec)
    except Exception as exc:
        decision.fallback_reason = (
            f"param extraction failed for pattern {best_id!r}: {exc}; "
            "falling back to OpenCode"
        )
        return decision

    # --- 5. Instantiate template ------------------------------------------------
    try:
        ir_data, protocol_tla = build_template(best_id, params)
    except Exception as exc:
        decision.fallback_reason = (
            f"template build failed for pattern {best_id!r}: {exc}; "
            "falling back to OpenCode"
        )
        return decision

    # --- 6. Populate decision ---------------------------------------------------
    decision.pattern_id = best_id
    decision.pattern = best_id
    decision.template_match_type = match_type
    decision.confidence = best_conf
    decision.reason = (
        f"matched pattern {best_id!r} with confidence {best_conf:.2f}"
    )
    decision.ir_data = ir_data
    decision.protocol_tla = protocol_tla
    decision.template_params = params
    decision.template_parameters = params
    decision.template_variant = str(params.get("variant_name") or "")
    decision.template_metadata = metadata
    if decision.template_variant:
        decision.template_metadata["selected_variant"] = decision.template_variant
    decision.agents = ir_data.get("agents", [])
    decision.resources = ir_data.get("resources", [])
    decision.channels = ir_data.get("channels", [])
    decision.app_agent_count = len(decision.agents)
    if best_id == "fan_in_decision":
        decision.decision_agent_id = str(params["decision_agent_id"])
        decision.template_priority_reason = (
            f"{len(evidence_sources)} independent evidence sources and explicit "
            "reconciliation/decision language outrank two-agent templates"
        )
    else:
        decision.template_priority_reason = (
            "two-agent workflow retained because fewer than three independent "
            "evidence sources were detected"
        )

    return decision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_agent_count(task_lower: str) -> int:
    """Rough heuristic: count agent nouns or comma-separated named entities."""
    nouns = _AGENT_NOUN_RE.findall(task_lower)
    if len(nouns) >= 2:
        return 2
    # Look for "X and Y" patterns with capitalized words
    cap_words = re.findall(r"\b[A-Z][a-z]+\b", task_lower)
    if len(cap_words) == 2:
        return 2
    return 0  # unknown → fail-closed


def _extract_keywords(task_lower: str) -> frozenset[str]:
    words = re.findall(r"[a-z]+", task_lower)
    return frozenset(words)


def _build_params(
    pattern_id: str,
    agents: list[dict],
    task_lower: str,
    tellme_spec: dict | None,
) -> dict:
    """Derive template params from agent list and task text."""
    channel_bound = 3
    if tellme_spec and isinstance(tellme_spec.get("channel_bound"), int):
        channel_bound = tellme_spec["channel_bound"]

    if pattern_id == "fan_in_decision":
        evidence_sources = detect_evidence_sources(task_lower)
        if len(evidence_sources) < 3:
            raise ValueError("fan_in_decision requires 3+ explicit evidence sources")
        decision_agent_id = "decision_reconciliation"
        if tellme_spec and tellme_spec.get("decision_agent_id"):
            decision_agent_id = _normalize_id(
                str(tellme_spec["decision_agent_id"])
            )
        return {
            "evidence_sources": evidence_sources,
            "decision_agent_id": decision_agent_id,
            "channel_bound": channel_bound,
        }

    if pattern_id == "sequential_handoff":
        a_id, a_role, b_id, b_role = _extract_two_agent_roles(agents, task_lower, 0, 1)
        return {
            "agent_a_id": a_id,
            "agent_b_id": b_id,
            "agent_a_role": a_role,
            "agent_b_role": b_role,
            "channel_bound": channel_bound,
        }

    if pattern_id == "verifier_approver":
        # Worker first (index 0), verifier second (index 1) by convention.
        # If the task mentions "verif" nearer the second agent name, we swap.
        w_id, w_role, v_id, v_role = _extract_two_agent_roles(agents, task_lower, 0, 1)
        return {
            "worker_id": w_id,
            "verifier_id": v_id,
            "worker_role": w_role,
            "verifier_role": v_role,
            "channel_bound": channel_bound,
        }

    if pattern_id == "producer_consumer":
        p_id, p_role, c_id, c_role = _extract_two_agent_roles(agents, task_lower, 0, 1)
        return {
            "producer_id": p_id,
            "consumer_id": c_id,
            "producer_role": p_role,
            "consumer_role": c_role,
            "channel_bound": channel_bound,
        }

    if pattern_id == "attendance_verification":
        o_id, o_role, v_id, v_role = _extract_two_agent_roles(agents, task_lower, 0, 1)
        return {
            "observer_id": o_id,
            "verifier_id": v_id,
            "observer_role": o_role,
            "verifier_role": v_role,
            "channel_bound": channel_bound,
        }

    if pattern_id == "traffic_signal_coordination":
        return _extract_traffic_params(task_lower, tellme_spec, channel_bound)

    raise ValueError(f"No param builder for pattern {pattern_id!r}")



_NUMBER_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}


def _extract_traffic_params(
    task_lower: str,
    tellme_spec: dict | None,
    channel_bound: int,
) -> dict:
    """Infer a supported traffic template variant, fail-closed if ambiguous."""
    text = _classification_text(task_lower, tellme_spec).lower()
    unsupported_cues = (
        "roundabout",
        "freeway",
        "highway network",
        "citywide network",
        "city-wide network",
        "multiple intersections",
        "corridor",
        "rail crossing",
        "train crossing",
    )
    if any(cue in text for cue in unsupported_cues):
        raise ValueError("unsupported or network-level traffic variant")

    ambiguous_cues = (
        "unknown number",
        "variable number",
        "arbitrary number",
        "dynamic number",
        "many intersections",
    )
    if any(cue in text for cue in ambiguous_cues):
        raise ValueError("ambiguous traffic variant requires OpenCode fallback")

    approach_count = _traffic_approach_count(text)
    include_emergency = _traffic_has_emergency(text)
    include_pedestrian = _traffic_has_pedestrian(text)
    if approach_count < 2 or approach_count > 8:
        raise ValueError("traffic template supports only 2 to 8 approaches")

    approach_ids = _traffic_approach_ids(approach_count)
    variant_name = _traffic_variant_name(
        approach_count,
        approach_ids,
        include_emergency,
        include_pedestrian,
    )
    return {
        "approach_ids": approach_ids,
        "approach_count": approach_count,
        "controller_id": "signal_controller",
        "include_emergency_detector": include_emergency,
        "include_pedestrian_agent": include_pedestrian,
        "emergency_detector_id": "emergency_detector",
        "pedestrian_agent_id": "pedestrian_crossing_agent",
        "variant_name": variant_name,
        "channel_bound": channel_bound,
    }


def _traffic_approach_count(text: str) -> int:
    for word, count in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s*[- ]?(?:way|approach|approaches|leg|legs)\b", text):
            return count
    numeric = re.search(r"\b([2-8])\s*[- ]?(?:way|approach|approaches|leg|legs)\b", text)
    if numeric:
        return int(numeric.group(1))
    if "t-junction" in text or "t junction" in text or "three-way" in text:
        return 3
    return 4


def _traffic_approach_ids(count: int) -> list[str]:
    if count == 4:
        return ["north_approach", "east_approach", "south_approach", "west_approach"]
    return [f"approach_{index}" for index in range(1, count + 1)]


def _traffic_has_emergency(text: str) -> bool:
    if re.search(r"\b(no|without)\s+emergency\b", text):
        return False
    return any(cue in text for cue in ("emergency", "ambulance", "fire truck", "siren", "priority vehicle"))


def _traffic_has_pedestrian(text: str) -> bool:
    return any(cue in text for cue in ("pedestrian", "crosswalk", "crossing", "walk signal", "walk phase"))


def _traffic_variant_name(
    approach_count: int,
    approach_ids: list[str],
    include_emergency: bool,
    include_pedestrian: bool,
) -> str:
    is_standard_four = approach_count == 4 and approach_ids == [
        "north_approach",
        "east_approach",
        "south_approach",
        "west_approach",
    ]
    base = "standard_four_way" if is_standard_four and not (include_emergency or include_pedestrian) else (
        "four_way" if is_standard_four else "n_approach"
    )
    suffixes = []
    if include_emergency:
        suffixes.append("emergency")
    if include_pedestrian:
        suffixes.append("pedestrian")
    return base if not suffixes else base + "_" + "_".join(suffixes)


def _extract_two_agent_roles(
    agents: list[dict],
    task_lower: str,
    idx_a: int,
    idx_b: int,
) -> tuple[str, str, str, str]:
    """Return (a_id, a_role, b_id, b_role) from agent dicts or synthetic defaults."""
    if len(agents) >= 2:
        a = agents[idx_a]
        b = agents[idx_b]
        a_id = _normalize_id(a.get("id") or a.get("name") or f"agent_{idx_a}")
        b_id = _normalize_id(b.get("id") or b.get("name") or f"agent_{idx_b}")
        a_role = a.get("role") or a.get("description") or a_id
        b_role = b.get("role") or b.get("description") or b_id
        return a_id, a_role, b_id, b_role

    # No structured agents — generate stable synthetic IDs
    return "agent_a", "perform work", "agent_b", "continue work"


def _normalize_id(raw: str) -> str:
    """Convert an agent ID to a safe PlusCal variable prefix."""
    return re.sub(r"[^a-z0-9_]", "_", raw.lower().strip()).strip("_") or "agent"


def _agents_from_harnesses(harnesses: list) -> list[dict]:
    """Derive synthetic agent dicts from TeLLMe candidate_harness names.

    Strips common suffixes (_HARNESS, _AGENT) and returns all distinct agents
    whose names suggest distinct data/processing roles.  Output harnesses
    (ANSWER_*, OUTPUT_*, RESPONSE_*, SYNTHESIS_*) are excluded.
    """
    _OUTPUT_PREFIXES = frozenset(("answer", "output", "response", "synthesis"))
    _STRIP_SUFFIXES = ("_harness", "_agent")
    result: list[dict] = []
    for h in harnesses:
        name = str(h).strip().upper()
        lowered = name.lower()
        if any(lowered.startswith(p) for p in _OUTPUT_PREFIXES):
            continue
        # Strip known suffixes to get the logical agent name
        agent_id = lowered
        for suffix in _STRIP_SUFFIXES:
            if agent_id.endswith(suffix):
                agent_id = agent_id[: -len(suffix)]
                break
        agent_id = re.sub(r"[^a-z0-9_]", "_", agent_id).strip("_") or "agent"
        if agent_id not in {agent["id"] for agent in result}:
            result.append({"id": agent_id})
    return result


def _classification_text(
    task: str,
    tellme_spec: dict[str, Any] | None,
) -> str:
    """Combine raw and structured deterministic signals for template scoring."""
    parts = [str(task or "")]
    if not tellme_spec:
        return "\n".join(parts)

    semantic_keys = (
        "user_query",
        "task",
        "description",
        "route",
        "candidate_harnesses",
        "application_goals",
        "goals",
        "agents",
        "resources",
        "channels",
    )
    for key in semantic_keys:
        value = tellme_spec.get(key)
        if value in (None, "", [], {}):
            continue
        parts.extend(_flatten_semantic_values(value))
    return "\n".join(part for part in parts if part)


def _flatten_semantic_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        flattened: list[str] = []
        for key, child in value.items():
            flattened.append(str(key))
            flattened.extend(_flatten_semantic_values(child))
        return flattened
    if isinstance(value, (list, tuple, set)):
        flattened = []
        for child in value:
            flattened.extend(_flatten_semantic_values(child))
        return flattened
    return [str(value)]


def _apply_structured_score_hints(
    scores: list[tuple[str, float]],
    tellme_spec: dict[str, Any] | None,
) -> list[tuple[str, float]]:
    """Resolve generic/domain ties using explicit TeLLMe harness metadata."""
    if not tellme_spec:
        return scores
    harness_text = " ".join(
        str(value).lower()
        for value in tellme_spec.get("candidate_harnesses") or []
    )
    attendance_cues = ("occupancy", "attendance", "calendar", "sensor")
    attendance_specificity = sum(cue in harness_text for cue in attendance_cues)
    adjusted = [
        (
            pattern_id,
            min(1.0, score + 0.01)
            if (
                pattern_id == "attendance_verification"
                and score > 0.0
                and attendance_specificity >= 2
            )
            else score,
        )
        for pattern_id, score in scores
    ]
    return sorted(adjusted, key=lambda item: item[1], reverse=True)
