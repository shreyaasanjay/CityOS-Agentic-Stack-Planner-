"""Deterministic route policy for TeLLMe Harness V0."""

from __future__ import annotations

import re
from typing import List, Optional

from .schemas import QueryAnalysis, RouteDecision, RouteScore, TellMeQuery, TimeWindow

SINGLE_AGENT_MAP = {
    "occupancy_context_agent": "get_occupancy_context",
    "motion_context_agent": "get_motion_context",
    "audio_context_agent": "get_audio_context",
    "room_state_agent": "get_room_state",
    "general_context_agent": "cityos_context_lookup",
}


def score_query_analysis(analysis: QueryAnalysis) -> RouteScore:
    score = 0
    reasons: List[str] = []

    if analysis.requires_diagnostic_reasoning:
        score += 3
        reasons.append("diagnostic_reasoning")
    if analysis.requires_identity_continuity:
        score += 3
        reasons.append("identity_continuity")
    if analysis.requires_multi_modal_reconciliation:
        score += 2
        reasons.append("multi_modal_reconciliation")
    if analysis.requires_multi_timestamp_reasoning:
        score += 2
        reasons.append("multi_timestamp_reasoning")
    if analysis.estimated_tool_calls > 2:
        score += 2
        reasons.append("estimated_tool_calls_gt_2")
    if analysis.requires_policy_review:
        score += 1
        reasons.append("policy_review")

    if _is_direct_lookup(analysis):
        score -= 2
        reasons.append("direct_lookup")
    if analysis.estimated_tool_calls <= 1:
        score -= 1
        reasons.append("estimated_tool_calls_lte_1")

    hard_gate = _is_hard_gate_tracefix(analysis)
    if hard_gate:
        reasons.append("hard_gate_tracefix")

    return RouteScore(score=score, hard_gate_tracefix=hard_gate, reasons=reasons)


def decide_route(query: TellMeQuery, analysis: QueryAnalysis) -> RouteDecision:
    user_query = query.user_query.strip()
    lowered = user_query.lower()

    if not user_query:
        return RouteDecision(
            route="needs_clarification",
            intent="general",
            rationale="The query is empty or underspecified.",
            caveats=["A non-empty user question is required."],
        )

    if any(token in lowered for token in ("raw video", "raw audio", "raw sensor", "transcript", "who was speaking")):
        return RouteDecision(
            route="not_allowed",
            intent="policy_privacy",
            rationale="The request asks for raw sensor access outside the V0 privacy boundary.",
            caveats=["V0 can only use CityOS-style structured context, not raw sensor artifacts, speaker identity, or unrestricted transcription."],
        )

    score = score_query_analysis(analysis)
    time_window = infer_time_window(user_query, query.timestamp)
    intent = _route_intent(analysis.intent)

    if score.hard_gate_tracefix or score.score >= 3:
        trigger_terms = getattr(analysis, "trigger_terms_found", [])
        explicit_agents = getattr(analysis, "requires_explicit_multi_agent", False)
        if explicit_agents and trigger_terms:
            rationale = (
                f"Explicit multi-agent coordination detected "
                f"(triggers: {', '.join(trigger_terms[:4])}). Requires TraceFix."
            )
        else:
            rationale = "The query requires multi-step or multi-modal reasoning beyond one mock CityOS lookup."
        return RouteDecision(
            route="multi_agent",
            intent=intent,
            rationale=rationale,
            time_window=time_window,
            requires_tracefix=True,
            caveats=["V0 only emits a TraceFixTaskSpec stub for complex queries."],
            trigger_terms_found=trigger_terms,
            explicit_agent_names_detected=explicit_agents,
        )

    selected_agent = _select_single_agent(analysis)
    selected_tool = SINGLE_AGENT_MAP[selected_agent]
    return RouteDecision(
        route="single_agent",
        intent=intent,
        selected_agent=selected_agent,
        selected_tool=selected_tool,
        required_tools=[selected_tool],
        rationale="The query appears answerable with one focused CityOS-style context lookup.",
        time_window=time_window,
        caveats=["If the single-agent lookup is insufficient, the harness should escalate to TraceFix later."],
    )


def infer_time_window(user_query: str, timestamp: Optional[str] = None) -> Optional[TimeWindow]:
    if timestamp:
        return TimeWindow(start=timestamp, end=timestamp, label="explicit_timestamp")

    lowered = user_query.lower()
    between_match = re.search(r"between\s+(\d{1,2}:\d{2})\s+and\s+(\d{1,2}:\d{2})", lowered)
    if between_match:
        return TimeWindow(start=between_match.group(1), end=between_match.group(2), label="query_range")

    at_match = re.search(r"\bat\s+(\d{1,2}:\d{2})\b", lowered)
    if at_match:
        time_value = at_match.group(1)
        return TimeWindow(start=time_value, end=time_value, label="query_timestamp")

    around_match = re.search(r"\baround\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)", lowered)
    if around_match:
        time_value = around_match.group(1)
        return TimeWindow(start=time_value, end=time_value, label="approximate_query_timestamp")

    if "right now" in lowered or "latest" in lowered:
        return TimeWindow(label="latest")
    return None


def _is_hard_gate_tracefix(analysis: QueryAnalysis) -> bool:
    lowered = analysis.user_query.lower()
    return (
        analysis.requires_explicit_multi_agent
        or analysis.requires_identity_continuity
        or analysis.requires_diagnostic_reasoning
        or analysis.requires_concordfs_trace_inspection
        or "false positive" in lowered
        or "false negative" in lowered
        or any(token in lowered for token in ("checkpoint", "resume", "pipeline"))
        or "compare modalities" in lowered
        or "compare video" in lowered
        or "compare radar" in lowered
        or "did the system make the right call" in lowered
        or "was the system correct" in lowered
    )


def _is_direct_lookup(analysis: QueryAnalysis) -> bool:
    if analysis.requires_explicit_multi_agent:
        return False
    return analysis.intent in (
        "historical_occupancy_count",
        "live_occupancy_count",
        "occupancy_lookup",
        "motion_lookup",
        "audio_lookup",
        "room_state_lookup",
        "general_lookup",
    ) and not any(
        (
            analysis.requires_multi_modal_reconciliation,
            analysis.requires_multi_timestamp_reasoning,
            analysis.requires_identity_continuity,
            analysis.requires_diagnostic_reasoning,
            analysis.requires_concordfs_trace_inspection,
        )
    )


def _route_intent(analysis_intent: str) -> str:
    if analysis_intent == "pipeline_diagnostic":
        return "diagnostic"
    if analysis_intent == "decision_evaluation":
        return "event_explanation"
    if analysis_intent == "live_occupancy_count":
        return "live_state"
    if analysis_intent in (
        "historical_occupancy_count",
        "occupancy_lookup",
        "motion_lookup",
        "audio_lookup",
        "room_state_lookup",
        "cross_modal_comparison",
        "identity_continuity_assessment",
    ):
        return "historical_lookup"
    if analysis_intent == "underspecified":
        return "general"
    return "general"


def _select_single_agent(analysis: QueryAnalysis) -> str:
    requirements = set(analysis.context_requirements)
    if "motion" in requirements:
        return "motion_context_agent"
    if "audio" in requirements:
        return "audio_context_agent"
    if "room_state" in requirements:
        return "room_state_agent"
    if "occupancy" in requirements:
        return "occupancy_context_agent"
    return "general_context_agent"
