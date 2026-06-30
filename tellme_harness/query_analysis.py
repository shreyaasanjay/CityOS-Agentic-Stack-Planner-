"""Deterministic query analysis for TeLLMe Harness V0."""

from __future__ import annotations

import re
from typing import List, Optional, Set

from .schemas import QueryAnalysis, TellMeQuery

_MODALITY_TOKENS = {
    "video": ("video", "camera"),
    "radar": ("radar",),
    "wifi": ("wifi", "wi-fi"),
    "audio": ("audio", "sound", "noise", "microphone", "speaking", "speech"),
}


def analyze_query(query: TellMeQuery) -> QueryAnalysis:
    user_query = query.user_query.strip()
    lowered = user_query.lower()

    named_modalities = _infer_modalities(lowered)
    if any(token in lowered for token in ("enter", "entered", "doorway", "under the table", "what happened", "injured", "injury", "speaker", "speaking", "speech")) and "video" not in named_modalities:
        named_modalities.append("video")
    if any(token in lowered for token in ("impact", "loud", "sound", "noise", "speaker", "speaking", "speech")) and "audio" not in named_modalities:
        named_modalities.append("audio")
    time_scope = _infer_time_scope(lowered, query.timestamp)
    intent = _infer_intent(lowered, time_scope)
    answer_type = _infer_answer_type(lowered, intent)
    context_requirements = _infer_context_requirements(lowered, intent, named_modalities)
    risk_flags = _infer_risk_flags(lowered, intent)

    requires_identity_continuity = any(
        token in lowered
        for token in (
            "same person",
            "same individual",
            "who entered",
            "later fell",
            "person who entered",
        )
    )
    requires_diagnostic_reasoning = any(
        token in lowered
        for token in (
            "why did",
            "why was",
            "why did the",
            "fail",
            "failed",
            "false positive",
            "false negative",
            "right call",
            "system correct",
            "made the right call",
            "alert reliable",
            "should the system",
            "should have sent",
        )
    )
    requires_concordfs_trace_inspection = any(
        token in lowered
        for token in ("checkpoint", "resume", "pipeline", "query log", "trace")
    ) or ("fall alert" in lowered and "fail" in lowered) or ("alert" in lowered and "fail" in lowered)
    requires_policy_review = any(token in lowered for token in ("policy", "privacy", "allowed"))

    mentions_compare = any(
        token in lowered
        for token in ("compare", "disagree", "agreement", "modality", "modalities")
    )
    requires_multi_modal_reconciliation = (
        mentions_compare
        or len(named_modalities) > 1
        or any(token in lowered for token in ("false positive", "false negative", "same person"))
        or "fall alert" in lowered
        or "speaking" in lowered
        or "speech" in lowered
        or "impact" in lowered
        or "injured" in lowered
    )
    requires_multi_timestamp_reasoning = (
        time_scope == "multi_timestamp"
        or any(token in lowered for token in ("later", "before", "after", "between"))
        or requires_identity_continuity
    )

    estimated_tool_calls = _estimate_tool_calls(
        context_requirements=context_requirements,
        named_modalities=named_modalities,
        requires_multi_modal_reconciliation=requires_multi_modal_reconciliation,
        requires_multi_timestamp_reasoning=requires_multi_timestamp_reasoning,
        requires_diagnostic_reasoning=requires_diagnostic_reasoning,
        requires_concordfs_trace_inspection=requires_concordfs_trace_inspection,
    )

    confidence = _estimate_confidence(
        requires_identity_continuity=requires_identity_continuity,
        requires_multi_modal_reconciliation=requires_multi_modal_reconciliation,
        requires_multi_timestamp_reasoning=requires_multi_timestamp_reasoning,
        requires_diagnostic_reasoning=requires_diagnostic_reasoning,
        requires_concordfs_trace_inspection=requires_concordfs_trace_inspection,
        requires_policy_review=requires_policy_review,
        user_query=user_query,
    )

    return QueryAnalysis(
        query_id=query.query_id,
        user_query=query.user_query,
        intent=intent,
        answer_type=answer_type,
        space_scope="single_space" if query.space_id else "unspecified_space",
        time_scope=time_scope,
        context_requirements=context_requirements,
        named_modalities=named_modalities,
        estimated_tool_calls=estimated_tool_calls,
        requires_multi_modal_reconciliation=requires_multi_modal_reconciliation,
        requires_multi_timestamp_reasoning=requires_multi_timestamp_reasoning,
        requires_identity_continuity=requires_identity_continuity,
        requires_diagnostic_reasoning=requires_diagnostic_reasoning,
        requires_concordfs_trace_inspection=requires_concordfs_trace_inspection,
        requires_policy_review=requires_policy_review,
        risk_flags=risk_flags,
        confidence=confidence,
    )


def _infer_modalities(lowered: str) -> List[str]:
    modalities: List[str] = []
    for modality, tokens in _MODALITY_TOKENS.items():
        if any(token in lowered for token in tokens):
            modalities.append(modality)
    return modalities


def _infer_time_scope(lowered: str, explicit_timestamp: Optional[str]) -> str:
    if explicit_timestamp:
        return "single_timestamp"
    if re.search(r"\bbetween\s+\d{1,2}:\d{2}\s+and\s+\d{1,2}:\d{2}\b", lowered):
        return "multi_timestamp"
    if len(re.findall(r"\b\d{1,2}:\d{2}\b", lowered)) >= 2:
        return "multi_timestamp"
    if re.search(r"\b(at|around)\s+\d{1,2}:\d{2}\b", lowered):
        return "single_timestamp"
    if any(token in lowered for token in ("right now", "latest", "currently", "current")):
        return "latest"
    if any(token in lowered for token in ("later", "before", "after", "then")):
        return "multi_timestamp"
    return "unspecified"


def _infer_intent(lowered: str, time_scope: str) -> str:
    if not lowered.strip():
        return "underspecified"
    if any(
        token in lowered
        for token in (
            "false positive",
            "false negative",
            "right call",
            "system correct",
            "alert reliable",
            "should the system",
            "should have sent",
        )
    ):
        return "decision_evaluation"
    if any(token in lowered for token in ("why did", "why was", "pipeline", "checkpoint", "resume", "fail", "failed")):
        return "pipeline_diagnostic"
    if "same person" in lowered:
        return "identity_continuity_assessment"
    if any(token in lowered for token in ("how many", "occupancy", "occupied", "empty", "people")):
        if time_scope == "single_timestamp":
            return "historical_occupancy_count"
        if time_scope == "latest":
            return "live_occupancy_count"
        return "occupancy_lookup"
    if "motion" in lowered:
        return "motion_lookup"
    if any(token in lowered for token in ("noise", "audio", "sound", "speech", "speaking", "microphone")):
        return "audio_lookup"
    if "room state" in lowered or "latest state" in lowered:
        return "room_state_lookup"
    if "compare" in lowered:
        return "cross_modal_comparison"
    return "general_lookup"


def _infer_answer_type(lowered: str, intent: str) -> str:
    if any(token in lowered for token in ("how many", "count")):
        return "count"
    if intent in ("decision_evaluation", "pipeline_diagnostic"):
        return "explanation"
    if any(token in lowered for token in ("is ", "was ", "did ", "were ", "same person")):
        return "boolean"
    if "room state" in lowered or "latest state" in lowered:
        return "state_summary"
    return "summary"


def _infer_context_requirements(lowered: str, intent: str, named_modalities: List[str]) -> List[str]:
    requirements: List[str] = []
    if any(token in lowered for token in ("how many", "occupancy", "occupied", "empty", "people")):
        requirements.append("occupancy")
    if "motion" in lowered:
        requirements.append("motion")
    if any(token in lowered for token in ("noise", "audio", "sound", "speech", "speaking", "microphone")):
        requirements.append("audio")
    if "room state" in lowered or "latest state" in lowered:
        requirements.append("room_state")
    if "same person" in lowered:
        requirements.extend(["tracks", "events"])
    if any(token in lowered for token in ("enter", "entered", "exit", "exited")):
        requirements.extend(["tracks", "events"])
    if any(token in lowered for token in ("fall", "incident", "alert", "impact", "entered", "speaking", "speech", "injured", "injury")):
        requirements.append("events")
    if intent == "pipeline_diagnostic":
        requirements.append("query_logs")
    if "compare" in lowered or len(named_modalities) > 1:
        requirements.append("cross_modal_context")
    return _dedupe(requirements or ["general_context"])


def _infer_risk_flags(lowered: str, intent: str) -> List[str]:
    flags: Set[str] = set()
    if "same person" in lowered:
        flags.add("identity_or_continuity")
    if any(
        token in lowered
        for token in (
            "false positive",
            "false negative",
            "right call",
            "system correct",
            "alert reliable",
            "should the system",
            "should have sent",
        )
    ):
        flags.add("decision_quality")
    if intent == "pipeline_diagnostic" or any(token in lowered for token in ("pipeline", "checkpoint", "resume", "fail", "failed")):
        flags.add("pipeline_diagnostic")
    if "compare" in lowered or "disagree" in lowered:
        flags.add("modality_disagreement")
    if any(token in lowered for token in ("privacy", "policy", "raw video", "raw audio", "raw sensor")):
        flags.add("policy_or_privacy")
    return sorted(flags)


def _estimate_tool_calls(
    context_requirements: List[str],
    named_modalities: List[str],
    requires_multi_modal_reconciliation: bool,
    requires_multi_timestamp_reasoning: bool,
    requires_diagnostic_reasoning: bool,
    requires_concordfs_trace_inspection: bool,
) -> int:
    if (
        len(context_requirements) == 1
        and not named_modalities
        and not requires_multi_modal_reconciliation
        and not requires_multi_timestamp_reasoning
        and not requires_diagnostic_reasoning
        and not requires_concordfs_trace_inspection
    ):
        return 1

    estimate = max(1, len(context_requirements))
    if named_modalities:
        estimate = max(estimate, len(named_modalities))
    if requires_multi_modal_reconciliation:
        estimate += 1
    if requires_multi_timestamp_reasoning:
        estimate += 1
    if requires_diagnostic_reasoning:
        estimate += 1
    if requires_concordfs_trace_inspection:
        estimate += 1
    if requires_multi_timestamp_reasoning and estimate < 3:
        estimate = 3
    return estimate


def _estimate_confidence(
    requires_identity_continuity: bool,
    requires_multi_modal_reconciliation: bool,
    requires_multi_timestamp_reasoning: bool,
    requires_diagnostic_reasoning: bool,
    requires_concordfs_trace_inspection: bool,
    requires_policy_review: bool,
    user_query: str,
) -> float:
    if not user_query.strip():
        return 0.1

    confidence = 0.92
    if requires_multi_modal_reconciliation:
        confidence -= 0.12
    if requires_multi_timestamp_reasoning:
        confidence -= 0.10
    if requires_identity_continuity:
        confidence -= 0.15
    if requires_diagnostic_reasoning:
        confidence -= 0.12
    if requires_concordfs_trace_inspection:
        confidence -= 0.10
    if requires_policy_review:
        confidence -= 0.08
    return round(max(0.2, min(0.95, confidence)), 2)


def _dedupe(values: List[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
