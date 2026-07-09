"""Constrained LLM semantic planning for TraceFix task-spec handoff."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from pydantic import ValidationError

from .evidence_card import build_validated_card_requirements
from .llm_client import LLMClient
from .schemas import (
    ApplicationGoal,
    AnswerPacketRequirements,
    DEFAULT_OUTPUT_CONTRACT,
    EvidenceCardRequirements,
    EvidencePlan,
    HarnessSubTask,
    IntentDecomposition,
    LLMDecompositionProposal,
    PrivacyPolicyCapability,
    ProposalValidationResult,
    ProposedHarness,
    QueryAmbiguity,
    QueryAnalysis,
    RoomCapabilityContext,
    RouteDecision,
    SmartspaceExecutionBrief,
    TimeWindow,
    TraceFixTaskSpec,
)

SIMPLE_AGENT_HARNESS_MAP = {
    "occupancy_context_agent": "occupancy_context_harness",
    "motion_context_agent": "motion_context_harness",
    "audio_context_agent": "audio_context_harness",
    "room_state_agent": "room_state_context_harness",
    "general_context_agent": "general_context_harness",
}

HARNESS_REGISTRY: dict[str, dict[str, Any]] = {
    "occupancy_context_harness": {
        "modalities": {"video"},
        "cityos_apis": {"get_occupancy_context"},
        "role": "Retrieve privacy-bounded occupancy context derived from camera coverage.",
        "expected_packet": "occupancy_context_packet",
    },
    "motion_context_harness": {
        "modalities": {"video"},
        "cityos_apis": {"get_motion_event_context"},
        "role": "Retrieve camera-derived motion-event context.",
        "expected_packet": "motion_context_packet",
    },
    "audio_context_harness": {
        "modalities": {"audio"},
        "cityos_apis": {"get_audio_context", "get_speech_activity_context", "get_audio_level_context"},
        "role": "Retrieve microphone-derived speech, sound-level, or acoustic-event context.",
        "expected_packet": "audio_context_packet",
    },
    "room_state_context_harness": {
        "modalities": {"context", "fusion"},
        "cityos_apis": {"get_room_state"},
        "role": "Retrieve room state context.",
        "expected_packet": "room_state_packet",
    },
    "general_context_harness": {
        "modalities": {"context"},
        "cityos_apis": {"cityos_context_lookup"},
        "role": "Retrieve general CityOS structured context.",
        "expected_packet": "general_context_packet",
    },
    "video_context_harness": {
        "modalities": {"video"},
        "cityos_apis": {"get_camera_occupancy_context", "get_motion_event_context", "get_posture_candidate_context"},
        "role": "Summarize privacy-bounded camera-derived structured context.",
        "expected_packet": "video_findings_packet",
    },
    "radar_context_harness": {
        "modalities": {"radar"},
        "cityos_apis": {"get_radar_context"},
        "role": "Summarize radar-derived structured context.",
        "expected_packet": "radar_findings_packet",
    },
    "wifi_context_harness": {
        "modalities": {"wifi"},
        "cityos_apis": {"get_wifi_context"},
        "role": "Summarize Wi-Fi-derived structured context.",
        "expected_packet": "wifi_findings_packet",
    },
    "event_retrieval_harness": {
        "modalities": {"context"},
        "cityos_apis": {"get_event_context"},
        "role": "Retrieve structured event summaries across camera and microphone derived packets.",
        "expected_packet": "event_packet",
    },
    "timestamp_retrieval_harness": {
        "modalities": {"context"},
        "cityos_apis": {"get_context_by_time_window"},
        "role": "Retrieve bounded context windows.",
        "expected_packet": "timestamp_packet",
    },
    "temporal_consistency_harness": {
        "modalities": {"context"},
        "cityos_apis": set(),
        "role": "Correlate findings across bounded time windows.",
        "expected_packet": "temporal_consistency_packet",
    },
    "cross_modal_consistency_harness": {
        "modalities": {"context"},
        "cityos_apis": set(),
        "role": "Correlate findings across modalities.",
        "expected_packet": "cross_modal_consistency_packet",
    },
    "pipeline_diagnostic_harness": {
        "modalities": {"context"},
        "cityos_apis": set(),
        "role": "Inspect query-run logs for failures or stalls.",
        "expected_packet": "diagnostic_packet",
    },
    "answer_synthesis_harness": {
        "modalities": {"context"},
        "cityos_apis": set(),
        "role": "Synthesize the final answer packet.",
        "expected_packet": "answer_packet",
    },
    "entry_event_harness": {
        "modalities": {"video"},
        "cityos_apis": {"get_entry_event_context"},
        "role": "Retrieve doorway entry-event summaries without identity claims.",
        "expected_packet": "entry_event_packet",
    },
    "identity_free_tracking_harness": {
        "modalities": {"video"},
        "cityos_apis": {"get_anonymous_track_context"},
        "role": "Track continuity without identifying a person.",
        "expected_packet": "tracking_packet",
    },
    "fall_detection_harness": {
        "modalities": {"video", "audio"},
        "cityos_apis": {"get_posture_candidate_context", "get_impact_sound_context"},
        "role": "Retrieve safety-event candidates without diagnosing injury.",
        "expected_packet": "fall_event_packet",
    },
    "radar_motion_harness": {
        "modalities": {"radar"},
        "cityos_apis": {"get_radar_context"},
        "role": "Retrieve radar motion summaries for corroboration.",
        "expected_packet": "radar_motion_packet",
    },
    "wifi_presence_harness": {
        "modalities": {"wifi"},
        "cityos_apis": {"get_wifi_context"},
        "role": "Retrieve Wi-Fi presence summaries for corroboration.",
        "expected_packet": "wifi_presence_packet",
    },
}

ANSWER_PACKET_SCHEMAS = {
    "direct_answer": {
        "required_fields": ["answer", "confidence", "evidence_refs", "caveats", "privacy_scope", "limitations"],
        "allowed_claims": ["bounded_count", "presence_state", "timestamped_event_summary"],
        "forbidden_claims": ["personal_identity", "medical_diagnosis", "unsupported_behavioral_inference"],
    },
    "correlation_answer": {
        "required_fields": ["answer", "confidence", "evidence_refs", "caveats", "privacy_scope", "limitations"],
        "allowed_claims": ["event_correlation", "bounded_temporal_sequence", "cross_modal_agreement"],
        "forbidden_claims": ["personal_identity", "medical_diagnosis", "cause_or_intent_inference"],
    },
    "insufficient_evidence": {
        "required_fields": ["answer", "confidence", "evidence_refs", "caveats", "privacy_scope", "limitations"],
        "allowed_claims": ["insufficient_evidence"],
        "forbidden_claims": ["guessing", "personal_identity", "medical_diagnosis"],
    },
}

DEFAULT_FORBIDDEN_CLAIMS = [
    "raw_sensor_access",
    "face_identity",
    "personal_identity",
    "medical_diagnosis",
    "unsupported_behavioral_inference",
]

DEFAULT_ESCALATION_CONDITIONS = [
    "insufficient_evidence",
    "conflicting_structured_context",
    "privacy_policy_denied",
]


def decompose_query(
    *,
    user_query: str,
    analysis: QueryAnalysis,
    allowed_modalities: List[str],
    allowed_time_windows: List[TimeWindow],
    answer_contract: Dict[str, object] | None,
    llm_client: LLMClient,
    allowed_harnesses: List[str],
    space_id: str | None,
    route_decision: RouteDecision | None = None,
) -> IntentDecomposition:
    effective_route_decision = route_decision
    if effective_route_decision is None:
        synthetic_route = "single_agent" if len([h for h in allowed_harnesses if h != "answer_synthesis_harness"]) <= 1 else "multi_agent"
        effective_route_decision = RouteDecision(
            route=synthetic_route,  # type: ignore[arg-type]
            intent="general",
            selected_agent=None,
            rationale="Synthetic route decision for decomposition compatibility.",
            time_window=allowed_time_windows[0] if allowed_time_windows else None,
            requires_tracefix=True,
        )
    envelope = build_policy_envelope(
        analysis=analysis,
        route_decision=effective_route_decision,
        allowed_harnesses=allowed_harnesses,
        allowed_modalities=allowed_modalities,
        allowed_time_windows=allowed_time_windows,
        space_id=space_id,
        answer_contract=answer_contract,
    )
    task_spec, _proposal, _validation = build_task_spec(
        query=user_query,
        policy_envelope=envelope,
        llm_client=llm_client,
    )
    return task_spec_to_intent_decomposition(task_spec)


def build_policy_envelope(
    *,
    analysis: QueryAnalysis,
    route_decision: RouteDecision | None,
    allowed_harnesses: List[str],
    allowed_modalities: List[str],
    allowed_time_windows: List[TimeWindow],
    space_id: str | None,
    answer_contract: Dict[str, object] | None,
    room_context: "RoomCapabilityContext | None" = None,
) -> dict[str, Any]:
    effective_harnesses = list(allowed_harnesses)
    effective_modalities = list(allowed_modalities)
    privacy_policy = _default_privacy_policy()
    room_context_payload = None

    if room_context is not None:
        available_apis = set(room_context.available_context_apis)
        # Drop harnesses whose required CityOS APIs are not actually available in
        # the discovered capability snapshot. Harnesses with no API dependency
        # (consistency/synthesis) and the synthesis harness are always retained.
        effective_harnesses = [
            name
            for name in effective_harnesses
            if name == "answer_synthesis_harness"
            or not HARNESS_REGISTRY.get(name, {}).get("cityos_apis", set())
            or HARNESS_REGISTRY[name]["cityos_apis"] <= available_apis
        ]
        if "answer_synthesis_harness" not in effective_harnesses:
            effective_harnesses.append("answer_synthesis_harness")
        # Restrict modalities to those with an available sensor; "context" is the
        # always-available structured-context channel.
        available_modalities = {
            sensor.modality
            for sensor in room_context.relevant_sensors
            if sensor.available
        } | {"context"}
        effective_modalities = [m for m in effective_modalities if m in available_modalities] or ["context"]
        privacy_policy = _privacy_policy_from_capability(room_context.privacy_policy)
        room_context_payload = room_context.model_dump()

    allowed_context_apis = sorted(
        {
            api
            for harness_name in effective_harnesses
            for api in HARNESS_REGISTRY.get(harness_name, {}).get("cityos_apis", set())
        }
    )
    if room_context is not None:
        allowed_context_apis = sorted(set(allowed_context_apis) & set(room_context.available_context_apis))

    return {
        "query_id": analysis.query_id,
        "space_id": space_id,
        "analysis": analysis.model_dump(),
        "route_decision": route_decision.model_dump() if route_decision is not None else None,
        "allowed_harnesses": list(effective_harnesses),
        "allowed_modalities": list(effective_modalities),
        "allowed_time_windows": [window.model_dump() for window in allowed_time_windows],
        "allowed_context_apis": allowed_context_apis,
        "answer_packet_schemas": ANSWER_PACKET_SCHEMAS,
        "answer_contract": _normalize_contract(answer_contract),
        "privacy_policy": privacy_policy,
        "validation_policy": _default_validation_policy(),
        "room_capability_context": room_context_payload,
        "available_sensor_ids": (
            [s.sensor_id for s in room_context.relevant_sensors if s.available]
            if room_context is not None
            else []
        ),
        "coverage_gaps": list(room_context.coverage_gaps) if room_context is not None else [],
    }


def _privacy_policy_from_capability(policy: "PrivacyPolicyCapability") -> Dict[str, Any]:
    forbidden = _dedupe(list(policy.forbidden_inferences) + list(DEFAULT_FORBIDDEN_CLAIMS))
    return {
        "privacy_scope": policy.privacy_scope,
        "raw_sensor_access_allowed": policy.raw_sensor_access_allowed,
        "identity_inference_allowed": policy.identity_inference_allowed,
        "forbidden_inferences": forbidden,
        "policy_id": policy.policy_id,
    }


def build_task_spec(
    *,
    query: str,
    policy_envelope: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[TraceFixTaskSpec, LLMDecompositionProposal, ProposalValidationResult]:
    task_spec, proposal, validation, _brief = build_task_spec_with_brief(
        query=query,
        policy_envelope=policy_envelope,
        llm_client=llm_client,
    )
    return task_spec, proposal, validation


def build_task_spec_with_brief(
    *,
    query: str,
    policy_envelope: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[TraceFixTaskSpec, LLMDecompositionProposal, ProposalValidationResult, SmartspaceExecutionBrief]:
    """Plan a query end to end, returning the brief as the semantic source of truth.

    The ``SmartspaceExecutionBrief`` and the ``TraceFixTaskSpec`` are derived from
    the same validated proposal: the brief enriches it with the resolved room
    capability context and ambiguity status, and the TaskSpec is the trusted
    contract compiled from the brief.
    """
    route_decision = policy_envelope.get("route_decision") or {}
    if route_decision.get("route") not in {"single_agent", "multi_agent"}:
        proposal = _deterministic_proposal(query=query, policy_envelope=policy_envelope, task_category="unsupported")
        validation = ProposalValidationResult(
            valid=False,
            validation_status="rejected",
            errors=["Deterministic route did not permit TraceFix task construction."],
            validated_proposal=proposal,
        )
        brief = build_execution_brief(proposal=proposal, route_policy=policy_envelope, validation_result=validation)
        return task_spec_from_brief(brief=brief, validation_result=validation), proposal, validation, brief

    proposal = propose_decomposition(
        query=query,
        policy_envelope=policy_envelope,
        available_harnesses=HARNESS_REGISTRY,
        answer_packet_schemas=ANSWER_PACKET_SCHEMAS,
        llm_client=llm_client,
    )
    validation = validate_decomposition_proposal(
        proposal=proposal,
        route_policy=policy_envelope,
        harness_registry=HARNESS_REGISTRY,
    )
    checked_proposal = validation.validated_proposal or _deterministic_proposal(
        query=query,
        policy_envelope=policy_envelope,
        task_category="unsupported",
    )
    brief = build_execution_brief(
        proposal=checked_proposal,
        route_policy=policy_envelope,
        validation_result=validation,
    )
    task_spec = task_spec_from_brief(brief=brief, validation_result=validation)
    return task_spec, proposal, validation, brief


def propose_decomposition(
    *,
    query: str,
    policy_envelope: dict[str, Any],
    available_harnesses: dict[str, dict[str, Any]],
    answer_packet_schemas: dict[str, dict[str, Any]],
    llm_client: LLMClient,
) -> LLMDecompositionProposal:
    fallback = _deterministic_proposal(
        query=query,
        policy_envelope=policy_envelope,
        task_category=_default_task_category(policy_envelope.get("analysis", {})),
    )
    prompt = _build_prompt(
        query=query,
        policy_envelope=policy_envelope,
        available_harnesses=available_harnesses,
        answer_packet_schemas=answer_packet_schemas,
        fallback_proposal=fallback,
    )
    try:
        payload = llm_client.complete_json(prompt)
        if not isinstance(payload, dict):
            raise ValueError("Proposal payload must be an object.")
        return LLMDecompositionProposal(**payload)
    except (ValueError, TypeError, ValidationError):
        return fallback


def validate_decomposition_proposal(
    *,
    proposal: LLMDecompositionProposal,
    route_policy: dict[str, Any],
    harness_registry: dict[str, dict[str, Any]],
) -> ProposalValidationResult:
    allowed_harnesses = set(route_policy.get("allowed_harnesses", []))
    allowed_modalities = set(route_policy.get("allowed_modalities", []))
    allowed_context_apis = set(route_policy.get("allowed_context_apis", []))
    allowed_time_windows = [
        TimeWindow(**window) if isinstance(window, dict) else window
        for window in route_policy.get("allowed_time_windows", [])
    ]
    errors: list[str] = []
    warnings: list[str] = []
    repairs: list[str] = []

    normalized = proposal.model_copy(deep=True)

    repaired_harnesses: list[ProposedHarness] = []
    for harness in normalized.proposed_harnesses:
        if harness.name not in harness_registry:
            errors.append(f"Unknown proposed harness: {harness.name}.")
            continue
        if harness.name not in allowed_harnesses:
            repairs.append(f"Removed harness outside deterministic envelope: {harness.name}.")
            continue
        required_apis = set(harness_registry[harness.name].get("cityos_apis", set()))
        if required_apis - allowed_context_apis:
            repairs.append(f"Removed harness using disallowed CityOS API: {harness.name}.")
            continue
        repaired_harnesses.append(
            harness.model_copy(
                update={
                    "role": harness_registry[harness.name]["role"],
                    "expected_packet": harness_registry[harness.name]["expected_packet"],
                }
            )
        )

    evidence_harnesses = [h for h in repaired_harnesses if h.name != "answer_synthesis_harness"]
    if not evidence_harnesses:
        if repaired_harnesses:
            warnings.append(
                "No evidence-gathering harness survived capability/policy filtering "
                "(likely a coverage gap); proposal downgraded to unsupported."
            )
        else:
            warnings.append("No executable harnesses survived validation; proposal downgraded to unsupported.")
        normalized.task_category = "unsupported"
        repaired_harnesses = [
            ProposedHarness(
                name="answer_synthesis_harness",
                role=HARNESS_REGISTRY["answer_synthesis_harness"]["role"],
                priority="required",
                expected_packet=HARNESS_REGISTRY["answer_synthesis_harness"]["expected_packet"],
                rationale="Return an insufficient-evidence answer without executable CityOS calls.",
            )
        ]

    normalized.proposed_harnesses = repaired_harnesses

    allowed_claims = list(normalized.answer_packet_requirements.allowed_claims)
    forbidden_claims = _dedupe(DEFAULT_FORBIDDEN_CLAIMS + list(normalized.answer_packet_requirements.forbidden_claims))
    if any(claim in forbidden_claims for claim in allowed_claims):
        allowed_claims = [claim for claim in allowed_claims if claim not in forbidden_claims]
        repairs.append("Removed allowed claims that conflicted with forbidden claims.")

    normalized.answer_packet_requirements = normalized.answer_packet_requirements.model_copy(
        update={
            "required_fields": _repair_required_fields(normalized.answer_packet_requirements.required_fields),
            "allowed_claims": allowed_claims,
            "forbidden_claims": forbidden_claims,
            "must_include_confidence": True,
            "must_include_evidence_refs": True,
            "must_include_limitations": True,
            "fallback_answer_type": "insufficient_evidence",
        }
    )

    # Trim any sensor/API references the LLM invented beyond the discovered
    # capability snapshot. The snapshot (not the LLM) is authoritative.
    available_sensor_ids = set(route_policy.get("available_sensor_ids", []))
    if available_sensor_ids and normalized.referenced_sensors:
        kept_sensors = [s for s in normalized.referenced_sensors if s in available_sensor_ids]
        if len(kept_sensors) != len(normalized.referenced_sensors):
            repairs.append("Removed referenced sensors not present in the capability snapshot.")
        normalized.referenced_sensors = kept_sensors
    if normalized.referenced_context_apis:
        kept_apis = [a for a in normalized.referenced_context_apis if a in allowed_context_apis]
        if len(kept_apis) != len(normalized.referenced_context_apis):
            repairs.append("Removed referenced context APIs outside the available/allowed set.")
        normalized.referenced_context_apis = kept_apis

    normalized.privacy_risk_notes = _dedupe(
        list(normalized.privacy_risk_notes)
        + [
            "Use CityOS structured context only.",
            "Do not request raw sensor access.",
            "Do not identify a person, diagnose injury, or infer unsupported behavior.",
        ]
    )
    normalized.escalation_conditions = _dedupe(
        list(normalized.escalation_conditions) + list(DEFAULT_ESCALATION_CONDITIONS)
    )

    required_modalities = _modalities_for_harnesses(
        [harness.name for harness in normalized.proposed_harnesses],
        harness_registry,
    )
    if required_modalities - allowed_modalities:
        repairs.append("Trimmed harness plan to deterministic modalities.")
        filtered_harnesses = [
            harness
            for harness in normalized.proposed_harnesses
            if harness_registry[harness.name]["modalities"] <= allowed_modalities
            or harness.name == "answer_synthesis_harness"
        ]
        normalized.proposed_harnesses = filtered_harnesses

    if normalized.task_category == "unsupported":
        warnings.append("Proposal was marked unsupported and will compile to an insufficient-evidence TaskSpec.")

    # Build the authoritative front-facing evidence-card contract. The LLM only
    # contributes descriptive prose; metric/confidence/conclusion rules are
    # rebuilt deterministically so no fabricated values can enter the card.
    card_requirements, card_repairs = build_validated_card_requirements(
        task_category=normalized.task_category,
        normalized_query=normalized.normalized_query,
        answer_requirements=normalized.answer_packet_requirements,
        candidate_harnesses=[harness.name for harness in normalized.proposed_harnesses],
        proposed=normalized.evidence_card_requirements,
    )
    normalized.evidence_card_requirements = card_requirements
    repairs.extend(card_repairs)

    validation_status = "accepted"
    if errors:
        validation_status = "rejected"
    elif repairs:
        validation_status = "repaired"

    validated = normalized if validation_status != "rejected" else normalized
    return ProposalValidationResult(
        valid=not errors,
        validation_status=validation_status,
        repaired=bool(repairs),
        errors=errors,
        warnings=warnings,
        repairs=repairs,
        validated_proposal=validated,
    )


def compile_tracefix_task_spec(
    *,
    proposal: LLMDecompositionProposal,
    route_policy: dict[str, Any],
    validation_result: ProposalValidationResult,
) -> TraceFixTaskSpec:
    query_id = route_policy["query_id"]
    route_decision = route_policy.get("route_decision") or {}
    task_id = route_policy.get("task_id") or f"task_{query_id.split('_')[-1]}"
    time_windows = _bounded_time_windows(route_policy.get("allowed_time_windows", []))
    harness_names = [harness.name for harness in proposal.proposed_harnesses]
    if "answer_synthesis_harness" not in harness_names:
        harness_names.append("answer_synthesis_harness")
    required_modalities = sorted(
        _modalities_for_harnesses(harness_names, HARNESS_REGISTRY) & set(route_policy.get("allowed_modalities", []))
    )
    if proposal.task_category == "unsupported":
        harness_names = ["answer_synthesis_harness"]
        required_modalities = ["context"]

    # Resolve the authoritative card contract. Always (re)derive deterministically
    # so a proposal that skipped validation (e.g. the unsupported route) still
    # carries a coherent, policy-compliant card contract.
    card_requirements, _ = build_validated_card_requirements(
        task_category=proposal.task_category,
        normalized_query=proposal.normalized_query,
        answer_requirements=proposal.answer_packet_requirements,
        candidate_harnesses=harness_names,
        proposed=proposal.evidence_card_requirements,
    )

    output_contract = _build_output_contract(proposal.answer_packet_requirements)
    return TraceFixTaskSpec(
        task_id=task_id,
        query_id=query_id,
        user_query=proposal.original_query,
        space_id=route_policy.get("space_id"),
        route=route_decision.get("route", "multi_agent"),
        time_windows=time_windows,
        required_modalities=required_modalities,
        candidate_harnesses=harness_names,
        application_goal=proposal.application_goal.model_dump(),
        evidence_plan=proposal.evidence_plan.model_dump(),
        answer_packet_requirements=proposal.answer_packet_requirements.model_dump(),
        evidence_card_contract=card_requirements.model_dump(),
        output_contract=output_contract,
        privacy_policy=dict(route_policy.get("privacy_policy") or _default_privacy_policy()),
        validation_policy=_default_validation_policy(validation_result),
        escalation_conditions=list(proposal.escalation_conditions),
        forbidden_claims=list(proposal.answer_packet_requirements.forbidden_claims),
        allowed_claims=list(proposal.answer_packet_requirements.allowed_claims),
        reasoning_summary=proposal.reasoning_summary,
        executable=proposal.task_category != "unsupported",
        reason=proposal.inferred_user_goal,
        caveats=_dedupe(
            [
                "This is a stub only; TraceFix-main is not invoked in V0.",
                "Real TraceFix multi-agent execution is not integrated yet.",
                "LLM reasoning is audit-only; validators and deterministic guardrails remain authoritative.",
                *list(route_policy.get("coverage_gaps", [])),
                *proposal.uncertainty_analysis,
                *proposal.privacy_risk_notes,
            ]
        ),
    )


def detect_ambiguity(
    *,
    proposal: LLMDecompositionProposal,
    route_policy: dict[str, Any],
) -> QueryAmbiguity:
    """Resolve the query's ambiguity status (LLM signal honored, else not ambiguous).

    A clarification-required result blocks an executable workflow downstream. The
    LLM may *raise* a clarification flag; deterministic policy decides what to do
    with it (it never lets the LLM suppress a coverage/privacy problem).
    """
    if proposal.ambiguity is not None and proposal.ambiguity.clarification_required:
        ambiguity = proposal.ambiguity.model_copy(deep=True)
        ambiguity.is_ambiguous = True
        if not ambiguity.clarifying_question:
            ambiguity.clarifying_question = "Could you clarify the time, space, or specific intent of your question?"
        return ambiguity
    return QueryAmbiguity()


def build_execution_brief(
    *,
    proposal: LLMDecompositionProposal,
    route_policy: dict[str, Any],
    validation_result: ProposalValidationResult,
) -> SmartspaceExecutionBrief:
    """Compile the capability-grounded brief from a validated proposal + envelope."""
    # Canonical compiled contract (well-tested) sources the deterministic fields.
    spec = compile_tracefix_task_spec(
        proposal=proposal,
        route_policy=route_policy,
        validation_result=validation_result,
    )
    ambiguity = detect_ambiguity(proposal=proposal, route_policy=route_policy)

    room_context_payload = route_policy.get("room_capability_context")
    room_context = (
        RoomCapabilityContext(**room_context_payload)
        if isinstance(room_context_payload, dict)
        else None
    )

    executable = spec.executable and not ambiguity.clarification_required
    caveats = list(spec.caveats)
    if ambiguity.clarification_required:
        caveats = _dedupe(
            caveats
            + ["Query is ambiguous; a clarification is required before an executable workflow can run."]
        )

    return SmartspaceExecutionBrief(
        brief_id=f"brief_{spec.task_id}",
        query_id=spec.query_id,
        space_id=spec.space_id,
        user_query=spec.user_query,
        normalized_query=proposal.normalized_query,
        route=spec.route,
        task_category=proposal.task_category,
        application_goal=proposal.application_goal,
        evidence_plan=proposal.evidence_plan,
        answer_packet_requirements=proposal.answer_packet_requirements,
        evidence_card_requirements=EvidenceCardRequirements(**spec.evidence_card_contract),
        candidate_harnesses=list(spec.candidate_harnesses),
        required_modalities=list(spec.required_modalities),
        time_windows=list(spec.time_windows),
        room_capability_context=room_context,
        privacy_policy=dict(spec.privacy_policy),
        validation_policy=dict(spec.validation_policy),
        escalation_conditions=list(spec.escalation_conditions),
        forbidden_claims=list(spec.forbidden_claims),
        allowed_claims=list(spec.allowed_claims),
        ambiguity=ambiguity,
        executable=executable,
        reasoning_summary=spec.reasoning_summary,
        caveats=caveats,
    )


def task_spec_from_brief(
    *,
    brief: SmartspaceExecutionBrief,
    validation_result: ProposalValidationResult | None = None,
) -> TraceFixTaskSpec:
    """Compile the trusted ``TraceFixTaskSpec`` from the brief (the source of truth)."""
    harnesses = list(brief.candidate_harnesses)
    modalities = list(brief.required_modalities)
    reason = brief.application_goal.user_intent if brief.application_goal else brief.reasoning_summary
    if not brief.executable:
        # Ambiguous or unsupported: collapse to a non-executable synthesis-only spec.
        harnesses = ["answer_synthesis_harness"]
        modalities = ["context"]

    return TraceFixTaskSpec(
        task_id=brief.brief_id.replace("brief_", "", 1) or brief.query_id,
        query_id=brief.query_id,
        user_query=brief.user_query,
        space_id=brief.space_id,
        route=brief.route,
        time_windows=list(brief.time_windows),
        required_modalities=modalities,
        candidate_harnesses=harnesses,
        application_goal=brief.application_goal.model_dump(),
        evidence_plan=brief.evidence_plan.model_dump(),
        answer_packet_requirements=brief.answer_packet_requirements.model_dump(),
        evidence_card_contract=brief.evidence_card_requirements.model_dump(),
        output_contract=_build_output_contract(brief.answer_packet_requirements),
        privacy_policy=dict(brief.privacy_policy) or _default_privacy_policy(),
        validation_policy=dict(brief.validation_policy) or _default_validation_policy(validation_result),
        escalation_conditions=list(brief.escalation_conditions),
        forbidden_claims=list(brief.forbidden_claims),
        allowed_claims=list(brief.allowed_claims),
        reasoning_summary=brief.reasoning_summary,
        executable=brief.executable,
        reason=reason,
        caveats=list(brief.caveats),
    )


def task_spec_to_intent_decomposition(task_spec: TraceFixTaskSpec) -> IntentDecomposition:
    semantic_harnesses = [
        harness_name
        for harness_name in task_spec.candidate_harnesses
        if harness_name != "answer_synthesis_harness"
    ] or list(task_spec.candidate_harnesses)
    harness_subtasks = [
        HarnessSubTask(
            harness_name=harness_name,
            space_id=task_spec.space_id,
            time_window=task_spec.time_windows[0] if task_spec.time_windows else None,
            expected_modalities=sorted(HARNESS_REGISTRY.get(harness_name, {}).get("modalities", {"context"})),
        )
        for harness_name in semantic_harnesses
    ]
    return IntentDecomposition(
        query_id=task_spec.query_id,
        required_harnesses=semantic_harnesses,
        harness_subtasks=harness_subtasks,
        required_modalities=list(task_spec.required_modalities),
        time_windows=list(task_spec.time_windows),
        needs_cross_modal_consistency="cross_modal_consistency_harness" in task_spec.candidate_harnesses,
        needs_temporal_consistency="temporal_consistency_harness" in task_spec.candidate_harnesses,
        output_contract=dict(task_spec.output_contract),
        llm_notes=["Compiled from validated LLMDecompositionProposal."],
        safety_caveats=list(task_spec.caveats),
    )


def infer_allowed_modalities(analysis: QueryAnalysis, route: str) -> List[str]:
    if route == "single_agent":
        if "audio" in analysis.context_requirements:
            return ["audio"]
        if any(item in analysis.context_requirements for item in ("occupancy", "motion", "activities", "tracks", "events")):
            return ["video"]
        if "room_state" in analysis.context_requirements:
            return ["context", "video", "audio"]
        return ["context"]

    modalities = list(analysis.named_modalities)
    if not modalities:
        modalities = ["video", "radar", "wifi", "audio", "context"]
    elif "context" not in modalities:
        modalities.append("context")
    if analysis.requires_concordfs_trace_inspection and "context" not in modalities:
        modalities.append("context")
    return _dedupe(modalities)


def infer_allowed_harnesses(analysis: QueryAnalysis, route: str, selected_agent: str | None) -> List[str]:
    if route == "single_agent":
        harness_name = SIMPLE_AGENT_HARNESS_MAP.get(selected_agent or "", "general_context_harness")
        return [harness_name, "answer_synthesis_harness"]

    harnesses: List[str] = []
    modality_to_harness = {
        "video": "video_context_harness",
        "radar": "radar_context_harness",
        "wifi": "wifi_context_harness",
        "audio": "audio_context_harness",
    }
    for modality in analysis.named_modalities:
        harness_name = modality_to_harness.get(modality)
        if harness_name:
            harnesses.append(harness_name)
    if "tracks" in analysis.context_requirements or analysis.requires_identity_continuity:
        harnesses.extend(["entry_event_harness", "identity_free_tracking_harness"])
    if "events" in analysis.context_requirements:
        harnesses.append("event_retrieval_harness")
    if any(token in analysis.user_query.lower() for token in ("fall", "fell")):
        harnesses.append("fall_detection_harness")
    if not harnesses:
        harnesses.extend(["video_context_harness", "radar_context_harness", "wifi_context_harness", "audio_context_harness"])
    if analysis.requires_multi_timestamp_reasoning:
        harnesses.extend(["timestamp_retrieval_harness", "temporal_consistency_harness"])
    if analysis.requires_multi_modal_reconciliation:
        harnesses.append("cross_modal_consistency_harness")
    if analysis.requires_diagnostic_reasoning or analysis.requires_concordfs_trace_inspection:
        harnesses.append("pipeline_diagnostic_harness")
    harnesses.append("answer_synthesis_harness")
    return [name for name in _dedupe(harnesses) if name in HARNESS_REGISTRY]


def _build_prompt(
    *,
    query: str,
    policy_envelope: dict[str, Any],
    available_harnesses: dict[str, dict[str, Any]],
    answer_packet_schemas: dict[str, dict[str, Any]],
    fallback_proposal: LLMDecompositionProposal,
) -> str:
    state = {
        "original_query": query,
        "policy_envelope": policy_envelope,
        "available_harnesses": {
            name: {
                "role": config["role"],
                "modalities": sorted(config["modalities"]),
                "cityos_apis": sorted(config["cityos_apis"]),
                "expected_packet": config["expected_packet"],
            }
            for name, config in available_harnesses.items()
            if name in policy_envelope.get("allowed_harnesses", [])
        },
        "answer_packet_schemas": answer_packet_schemas,
        "fallback_proposal": fallback_proposal.model_dump(),
    }
    return "\n".join(
        [
            "You are the TeLLMe semantic planner for intent decomposition.",
            "Return only JSON matching the LLMDecompositionProposal schema.",
            "Your output is an untrusted proposal that will be validated before execution.",
            "Inputs include the original user query, deterministic route-policy envelope, allowed harnesses, allowed CityOS context APIs, available answer-packet schemas, application-goal requirements, allowed claims, forbidden claims, and escalation behavior.",
            "You must identify: user intent, task category, application goal, primary/supporting evidence, required/supporting/optional harnesses, answer packet requirements, uncertainty sources, privacy risks, and escalation conditions.",
            "You must also propose the front-facing evidence-card structure in 'evidence_card_requirements': the appropriate card_type, primary_question, title_template, claim_target, allowed/forbidden conclusion types, badges, required metrics, evidence requirements, confidence kind, caveats, provenance requirements, and fallback_card_type.",
            "Card constraints (these mirror the original TeLLMe causal-card conventions):",
            "- You are defining OUTPUT REQUIREMENTS, not filling in final measured values.",
            "- You must NOT invent p-values, confidence intervals, sample sizes, effect estimates, graph support, bootstrap consistency, sensor confidence, timestamps, or evidence references. Every numeric value comes later from harness outputs, aggregators, statistical estimators, or TraceFix verification.",
            "- Request card_type 'causal' ONLY when the query and execution plan actually support causal analysis (a causal estimator is available). Otherwise use correlational, temporal, descriptive, or insufficient_evidence.",
            "- Never present temporal order, correlation, or multimodal agreement as causation.",
            "- Use 'insufficient_evidence' for unsupported or low-confidence queries, and 'privacy_blocked' for queries that exceed the allowed privacy scope (e.g. identity).",
            "- The card's allowed and forbidden conclusion types must agree with the answer packet requirements.",
            "CityOS capability awareness:",
            "- The policy envelope includes 'room_capability_context' (discovered sensors, available context APIs, privacy policy, coverage gaps).",
            "- You may only reference sensors in 'available_sensor_ids' and context APIs in 'allowed_context_apis'. Referencing anything else will be stripped by the validator.",
            "- If a required modality or context type appears in 'coverage_gaps', prefer insufficient_evidence rather than assuming the data exists.",
            "- Summarize the relevant capabilities in 'room_context_summary' and list what you used in 'referenced_sensors' / 'referenced_context_apis'.",
            "Ambiguity handling:",
            "- If the query is too under-specified to plan (ambiguous space, time, intent, or modality), set 'ambiguity.clarification_required' = true and provide a single 'clarifying_question'.",
            "- A clarification-required result will compile to a non-executable task that asks the user to clarify.",
            "Hard constraints:",
            "- You cannot request raw sensor access unless explicitly allowed by policy.",
            "- You cannot request face identity, personal identity, medical diagnosis, or unsupported behavioral inference.",
            "- You cannot call CityOS directly; harnesses call CityOS and TraceFix coordinates harnesses.",
            "- You cannot add harnesses, modalities, time windows, or CityOS APIs outside the policy envelope.",
            "- Prefer insufficient_evidence over guessing.",
            "Required JSON skeleton:",
            json.dumps(fallback_proposal.model_dump(), indent=2, sort_keys=True),
            "STATE_JSON:",
            json.dumps(state, indent=2, sort_keys=True),
        ]
    )


def _deterministic_proposal(
    *,
    query: str,
    policy_envelope: dict[str, Any],
    task_category: str,
) -> LLMDecompositionProposal:
    lowered = query.lower()
    allowed_harnesses = list(policy_envelope.get("allowed_harnesses", []))
    query_id = policy_envelope.get("query_id", "query")
    analysis = policy_envelope.get("analysis", {})
    if "how many" in lowered or task_category == "occupancy_count":
        harnesses = [name for name in ["occupancy_context_harness", "video_context_harness", "audio_context_harness"] if name in allowed_harnesses]
        answer_type = "direct_answer"
        user_goal = "Return a bounded occupancy count with confidence and evidence."
        category = "occupancy_count"
    elif "occupied" in lowered or "presence" in lowered:
        harnesses = [name for name in ["occupancy_context_harness", "video_context_harness"] if name in allowed_harnesses]
        answer_type = "direct_answer"
        user_goal = "Determine whether the room was occupied."
        category = "presence_check"
    elif "speaking" in lowered or "speech" in lowered:
        harnesses = [name for name in ["audio_context_harness", "video_context_harness"] if name in allowed_harnesses]
        answer_type = "direct_answer"
        user_goal = "Determine whether speech activity occurred without transcript or speaker identity."
        category = "event_detection"
    elif "impact" in lowered and "enter" in lowered:
        harnesses = [
            name
            for name in [
                "entry_event_harness",
                "audio_context_harness",
                "fall_detection_harness",
                "temporal_consistency_harness",
                "answer_synthesis_harness",
            ]
            if name in allowed_harnesses
        ]
        answer_type = "correlation_answer"
        user_goal = "Correlate entry and impact candidates without claiming a fall or injury."
        category = "temporal_correlation"
    elif "fall" in lowered and ("entered" in lowered or "later" in lowered):
        harnesses = [
            name
            for name in [
                "entry_event_harness",
                "identity_free_tracking_harness",
                "fall_detection_harness",
                "temporal_consistency_harness",
                "answer_synthesis_harness",
            ]
            if name in allowed_harnesses
        ]
        answer_type = "correlation_answer"
        user_goal = "Correlate entry and fall-like events without identifying a person."
        category = "temporal_correlation"
    elif "fall" in lowered or "alert" in lowered:
        harnesses = [name for name in allowed_harnesses if name != "answer_synthesis_harness"]
        answer_type = "correlation_answer"
        user_goal = "Assess whether a safety-related event can be supported by structured evidence."
        category = "safety_event_assessment"
    else:
        harnesses = [name for name in allowed_harnesses if name != "answer_synthesis_harness"][:1]
        answer_type = "insufficient_evidence" if task_category == "unsupported" else "direct_answer"
        user_goal = "Return the smallest policy-compliant answer or escalate on insufficient evidence."
        category = task_category

    proposed_harnesses = [
        ProposedHarness(
            name=harness_name,
            role=HARNESS_REGISTRY[harness_name]["role"],
            priority="required" if index == 0 else "supporting",
            expected_packet=HARNESS_REGISTRY[harness_name]["expected_packet"],
            rationale="Selected within the deterministic privacy and routing envelope.",
        )
        for index, harness_name in enumerate(harnesses)
    ]
    if not proposed_harnesses:
        proposed_harnesses = [
            ProposedHarness(
                name="answer_synthesis_harness",
                role=HARNESS_REGISTRY["answer_synthesis_harness"]["role"],
                priority="required",
                expected_packet=HARNESS_REGISTRY["answer_synthesis_harness"]["expected_packet"],
                rationale="Fallback non-executable answer path.",
            )
        ]

    schema = ANSWER_PACKET_SCHEMAS.get(answer_type, ANSWER_PACKET_SCHEMAS["insufficient_evidence"])
    answer_requirements = AnswerPacketRequirements(
        answer_type=answer_type,
        required_fields=list(schema["required_fields"]),
        allowed_claims=list(schema["allowed_claims"]),
        forbidden_claims=_dedupe(list(schema["forbidden_claims"]) + list(DEFAULT_FORBIDDEN_CLAIMS)),
        fallback_answer_type="insufficient_evidence",
    )
    card_requirements, _ = build_validated_card_requirements(
        task_category=category,
        normalized_query=query.strip().lower(),
        answer_requirements=answer_requirements,
        candidate_harnesses=[h.name for h in proposed_harnesses],
        proposed=None,
    )
    return LLMDecompositionProposal(
        original_query=query,
        normalized_query=query.strip().lower(),
        task_category=category,  # type: ignore[arg-type]
        inferred_user_goal=user_goal,
        application_goal=ApplicationGoal(
            goal_type=category,
            user_intent=analysis.get("intent", "general_lookup"),
            success_condition="Return a privacy-bounded answer packet with confidence and evidence refs.",
            failure_condition="Return insufficient_evidence or escalate when the structured context is inadequate.",
            non_goals=[
                "identify person",
                "face recognition",
                "medical diagnosis",
                "infer cause or intent",
            ],
        ),
        proposed_harnesses=proposed_harnesses,
        evidence_plan=EvidencePlan(
            primary_evidence=[proposed_harnesses[0].expected_packet],
            supporting_evidence=[h.expected_packet for h in proposed_harnesses[1:]],
            minimum_sufficient_evidence=[proposed_harnesses[0].expected_packet],
            conflicting_evidence_checks=["cross_modal_consistency_packet"] if "cross_modal_consistency_harness" in harnesses else [],
        ),
        answer_packet_requirements=answer_requirements,
        evidence_card_requirements=card_requirements,
        uncertainty_analysis=[
            "Mock CityOS structured context may be incomplete.",
            "Confidence must remain bounded by available evidence.",
        ],
        escalation_conditions=list(DEFAULT_ESCALATION_CONDITIONS),
        privacy_risk_notes=[
            "Use CityOS structured context only.",
            "Do not request raw sensor access, identity claims, unrestricted transcription, or medical diagnosis.",
        ],
        reasoning_summary="Deterministic template proposal generated for query {query_id}.".format(query_id=query_id),
    )


def _default_task_category(analysis: dict[str, Any]) -> str:
    intent = analysis.get("intent", "")
    if "occupancy" in intent:
        return "occupancy_count"
    if "motion" in intent or "audio" in intent or "activity" in intent:
        return "event_detection"
    if analysis.get("requires_multi_timestamp_reasoning"):
        return "temporal_correlation"
    if analysis.get("requires_diagnostic_reasoning"):
        return "safety_event_assessment"
    return "unsupported"


def _build_output_contract(requirements: AnswerPacketRequirements) -> Dict[str, Any]:
    required_fields = _repair_required_fields(requirements.required_fields)
    field_types = dict(DEFAULT_OUTPUT_CONTRACT["field_types"])
    field_types.update({"privacy_scope": "string", "limitations": "array"})
    return {
        "required_fields": required_fields,
        "field_types": field_types,
        "answer_type": requirements.answer_type,
        "fallback_answer_type": requirements.fallback_answer_type,
        "allowed_claims": list(requirements.allowed_claims),
        "forbidden_claims": list(requirements.forbidden_claims),
        "must_include_confidence": True,
        "must_include_evidence_refs": True,
        "must_include_limitations": True,
    }


def _normalize_contract(contract: Dict[str, object] | None) -> Dict[str, object]:
    payload = contract or DEFAULT_OUTPUT_CONTRACT
    return {
        "required_fields": list(payload.get("required_fields", DEFAULT_OUTPUT_CONTRACT["required_fields"])),
        "field_types": dict(payload.get("field_types", DEFAULT_OUTPUT_CONTRACT["field_types"])),
    }


def _default_privacy_policy() -> Dict[str, Any]:
    return {
        "privacy_scope": "cityos_structured_context_only",
        "raw_sensor_access_allowed": False,
        "identity_inference_allowed": False,
        "forbidden_inferences": list(DEFAULT_FORBIDDEN_CLAIMS),
    }


def _default_validation_policy(validation_result: ProposalValidationResult | None = None) -> Dict[str, Any]:
    payload = {
        "llm_proposal_untrusted": True,
        "bounded_time_windows_required": True,
        "schema_validation_required": True,
        "must_preserve_forbidden_claims": True,
    }
    if validation_result is not None:
        payload["validation_status"] = validation_result.validation_status
        payload["repairs"] = list(validation_result.repairs)
        payload["warnings"] = list(validation_result.warnings)
    return payload


def _bounded_time_windows(windows: list[dict[str, Any]] | list[TimeWindow]) -> list[TimeWindow]:
    bounded: list[TimeWindow] = []
    for window in windows:
        parsed = TimeWindow(**window) if isinstance(window, dict) else window
        bounded.append(TimeWindow(start=parsed.start, end=parsed.end, label=parsed.label or "bounded_window"))
    return bounded


def _modalities_for_harnesses(harness_names: list[str], harness_registry: dict[str, dict[str, Any]]) -> set[str]:
    modalities: set[str] = set()
    for harness_name in harness_names:
        modalities.update(harness_registry.get(harness_name, {}).get("modalities", set()))
    return modalities


def _repair_required_fields(required_fields: list[str]) -> list[str]:
    fields = _dedupe(
        list(required_fields)
        + ["answer", "confidence", "evidence_refs", "caveats", "privacy_scope", "limitations"]
    )
    return fields


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
