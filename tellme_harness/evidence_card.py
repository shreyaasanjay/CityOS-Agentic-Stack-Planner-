"""Deterministic evidence-card contract rules.

This module generalizes the original TeLLMe causal-card conventions
(``rebuild-tellme-main/explain/{card_schema,card_builder,nlg}.py`` and
``ui/card.py``) into front-facing evidence cards that support descriptive,
temporal, correlational, safety-assessment, and causal queries.

Design rules carried over from the original stack:

* The original ``CausalCard`` computed every numeric field deterministically
  (ATE/CI/p-value from ``causal/estimators.py``; rows analyzed by counting;
  graph support/consistency from discovery + bootstrap; confidence score from a
  rule-based function in ``card_builder._compute_confidence``). The LLM only
  ever wrote prose (``explain/nlg.py``). We preserve that boundary: this module
  defines *requirements* (structure + value sources), never measured values.
* A causal card is only legitimate when an actual causal estimator runs. The
  original stack always had estimator output; the current CityOS envelope has no
  causal estimator harness, so causal requests deterministically downgrade.

The deterministic builders below are the authoritative source of metric,
confidence, badge, and conclusion-type requirements. An LLM proposal may only
contribute descriptive/textual fields (question, title, claim target, prose
caveats/provenance); everything that could smuggle in a fabricated number is
rebuilt here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .schemas import (
    AnswerPacketRequirements,
    EvidenceCardBadge,
    EvidenceCardBadgeRequirement,
    EvidenceCardConfidenceRequirements,
    EvidenceCardEvidenceRequirement,
    EvidenceCardMetric,
    EvidenceCardMetricRequirement,
    EvidenceCardPacket,
    EvidenceCardRequirements,
    EvidenceRef,
    RenderedConfidence,
    ValidatedClaim,
)

# Harnesses that can supply genuine causal-estimator output. The current CityOS
# envelope ships none, so any causal request downgrades. Adding a real causal
# estimator harness here is the single switch that re-enables causal cards.
CAUSAL_CAPABLE_HARNESSES: set[str] = set()

# Identity-seeking phrasings that force a privacy-blocked card regardless of the
# proposed task category.
_IDENTITY_TOKENS = (
    "who was",
    "who is",
    "who's",
    "whose",
    "name of",
    "identify the",
    "identity of",
    "what is the name",
    "what's the name",
)

# Claims that must never appear as a card conclusion type. Shared with the
# answer-packet forbidden claims so prose and card stay synchronized.
_GLOBAL_FORBIDDEN_CONCLUSIONS = [
    "personal_identity",
    "face_identity",
    "medical_diagnosis",
    "confirmed_injury",
    "cause_or_intent_inference",
    "unsupported_behavioral_inference",
    "raw_sensor_access",
]


# ---------------------------------------------------------------------------
# Card-type resolution + downgrade
# ---------------------------------------------------------------------------

def resolve_card_type(
    *,
    task_category: str,
    normalized_query: str,
    causal_supported: bool,
    proposed_card_type: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Return the authoritative card type plus any repair notes.

    Privacy always wins. A causal request without estimator support downgrades
    to correlational. An LLM may always *escalate down* to a safer card
    (insufficient_evidence / privacy_blocked / unsupported).
    """
    notes: List[str] = []
    lowered = (normalized_query or "").lower()

    if any(token in lowered for token in _IDENTITY_TOKENS):
        if proposed_card_type and proposed_card_type != "privacy_blocked":
            notes.append(
                "Identity-seeking query forced privacy_blocked card over proposed "
                f"'{proposed_card_type}'."
            )
        return "privacy_blocked", notes

    category_map = {
        "occupancy_count": "descriptive",
        "presence_check": "descriptive",
        "event_detection": "descriptive",
        "temporal_correlation": "temporal",
        "safety_event_assessment": "safety_assessment",
        "correlation_analysis": "correlational",
        "causal_analysis": "causal",
        "unsupported": "insufficient_evidence",
    }
    authoritative = category_map.get(task_category, "descriptive")

    if authoritative == "causal" and not causal_supported:
        authoritative = "correlational"
        notes.append(
            "Causal card requested but no causal estimator is available in the "
            "execution envelope; downgraded to correlational."
        )

    # Honor an LLM escalation to a strictly safer fallback card.
    safer = {"insufficient_evidence", "unsupported"}
    if proposed_card_type in safer and authoritative not in {"privacy_blocked"}:
        if proposed_card_type != authoritative:
            notes.append(
                f"Honored LLM escalation from '{authoritative}' to safer "
                f"'{proposed_card_type}' card."
            )
        return proposed_card_type, notes

    # If the LLM proposed a *stronger* card than supported, note the override.
    strength = {
        "insufficient_evidence": 0,
        "privacy_blocked": 0,
        "unsupported": 0,
        "descriptive": 1,
        "temporal": 2,
        "correlational": 3,
        "safety_assessment": 3,
        "causal": 4,
    }
    if (
        proposed_card_type
        and proposed_card_type != authoritative
        and strength.get(proposed_card_type, 0) > strength.get(authoritative, 0)
    ):
        notes.append(
            f"Overrode proposed '{proposed_card_type}' card with deterministically "
            f"supported '{authoritative}' card."
        )
    return authoritative, notes


# ---------------------------------------------------------------------------
# Deterministic per-card-type requirement builders (Phase 6)
# ---------------------------------------------------------------------------

def _confidence(kind: str, *, inputs: List[str], scoring: str, threshold: Optional[float] = None) -> EvidenceCardConfidenceRequirements:
    return EvidenceCardConfidenceRequirements(
        confidence_kind=kind,  # type: ignore[arg-type]
        required_inputs=inputs,
        minimum_threshold=threshold,
        label_bands={"low": [0.0, 0.5], "moderate": [0.5, 0.8], "high": [0.8, 1.0]},
        explanation_required=True,
        scoring_method=scoring,
    )


def _metric(metric_id: str, label: str, desc: str, value_type: str, source: str, *, required: bool, source_field: Optional[str] = None, unavailable: str = "show_na") -> EvidenceCardMetricRequirement:
    return EvidenceCardMetricRequirement(
        metric_id=metric_id,
        display_label=label,
        description=desc,
        required=required,
        value_type=value_type,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        source_field=source_field,
        unavailable_behavior=unavailable,  # type: ignore[arg-type]
    )


def _card_type_badge() -> EvidenceCardBadgeRequirement:
    return EvidenceCardBadgeRequirement(
        badge_id="card_type",
        label_template="{card_type}",
        source="query_analysis",
        required=True,
    )


def _base_requirements(card_type: str) -> EvidenceCardRequirements:
    """Construct the deterministic, authoritative requirements for a card type."""
    title = "{primary_question}"
    fallback = "insufficient_evidence"

    if card_type == "descriptive":
        return EvidenceCardRequirements(
            card_type="descriptive",
            primary_question="What did the structured context observe?",
            title_template=title,
            claim_target="observed_room_state",
            allowed_conclusion_types=["observed_state", "bounded_count", "presence_state"],
            forbidden_conclusion_types=["causal_effect", "statistical_significance", "causal_graph_claim"],
            badge_requirements=[
                _card_type_badge(),
                EvidenceCardBadgeRequirement(badge_id="modality", label_template="{modalities}", source="harness_output"),
            ],
            metric_requirements=[
                _metric("observed_value", "Observed value", "The value observed in the time window (e.g. occupancy count or state).", "category", "harness_output", required=True, source_field="value"),
                _metric("time_window", "Time window", "Bounded window the observation covers.", "range", "harness_output", required=True, source_field="timestamp"),
                _metric("modalities_used", "Modalities used", "Sensor modalities that contributed.", "category", "aggregator", required=True),
                _metric("evidence_count", "Evidence count", "Number of supporting context packets.", "integer", "aggregator", required=True),
                _metric("sensor_agreement", "Sensor agreement", "Fraction of modalities in agreement.", "percentage", "aggregator", required=False),
            ],
            summary_requirements=["State the observed value and its bounded time window in one sentence."],
            confidence_requirements=_confidence("sensor_confidence", inputs=["sensor_confidence", "evidence_count"], scoring="bounded_sensor_confidence"),
            evidence_requirements=[
                EvidenceCardEvidenceRequirement(evidence_type="structured_context", required=True, minimum_count=1, accepted_packet_types=["occupancy_context_packet", "room_state_packet", "motion_context_packet", "audio_context_packet"]),
            ],
            caveat_requirements=["Reflects structured context only; no causal interpretation."],
            provenance_requirements=["List the harness/packet ids backing the observed value."],
            fallback_card_type=fallback,
        )

    if card_type == "temporal":
        return EvidenceCardRequirements(
            card_type="temporal",
            primary_question="In what order did the events occur?",
            title_template=title,
            claim_target="bounded_temporal_sequence",
            allowed_conclusion_types=["temporal_order", "bounded_temporal_sequence", "event_match"],
            forbidden_conclusion_types=["causation", "personal_identity", "intent_inference", "cause_or_intent_inference"],
            badge_requirements=[
                _card_type_badge(),
                EvidenceCardBadgeRequirement(badge_id="ordering", label_template="{ordering}", source="aggregator"),
            ],
            metric_requirements=[
                _metric("event_timestamps", "Event timestamps", "Timestamps of the correlated events.", "timestamp", "harness_output", required=True),
                _metric("elapsed_duration", "Elapsed duration", "Time elapsed between the events.", "duration", "aggregator", required=True),
                _metric("track_continuity", "Track continuity", "Whether continuity was maintained between events (identity-free).", "category", "aggregator", required=True),
                _metric("evidence_agreement", "Evidence agreement", "Cross-source agreement on the sequence.", "percentage", "aggregator", required=False),
            ],
            summary_requirements=["State the temporal order without implying that one event caused the other."],
            confidence_requirements=_confidence("temporal_link_confidence", inputs=["track_continuity", "evidence_agreement"], scoring="temporal_link_confidence"),
            evidence_requirements=[
                EvidenceCardEvidenceRequirement(evidence_type="event_sequence", required=True, minimum_count=2, accepted_packet_types=["entry_event_packet", "fall_event_packet", "tracking_packet", "temporal_consistency_packet"]),
            ],
            caveat_requirements=["Temporal order does not establish causation.", "No identity claim is made about any tracked person."],
            provenance_requirements=["List event packets and the time windows compared."],
            fallback_card_type=fallback,
        )

    if card_type == "correlational":
        return EvidenceCardRequirements(
            card_type="correlational",
            primary_question="Is there an association between these observations?",
            title_template=title,
            claim_target="non_causal_association",
            allowed_conclusion_types=["association", "co_occurrence"],
            forbidden_conclusion_types=["causation", "causal_effect", "personal_identity"],
            badge_requirements=[
                _card_type_badge(),
                EvidenceCardBadgeRequirement(badge_id="association_only", label_template="association only", source="aggregator", required=True),
            ],
            metric_requirements=[
                _metric("association_measure", "Association measure", "Strength of the observed association.", "float", "statistical_estimator", required=True),
                _metric("uncertainty_interval", "Uncertainty interval", "Interval around the association measure.", "range", "statistical_estimator", required=True),
                _metric("sample_size", "Sample size", "Number of observations analyzed.", "integer", "statistical_estimator", required=True, source_field="sample_size"),
                _metric("statistical_evidence", "Statistical evidence (p-value)", "Smaller values indicate stronger statistical evidence.", "float", "statistical_estimator", required=False, source_field="p_value"),
            ],
            summary_requirements=["Report the association and explicitly state it is not causal."],
            confidence_requirements=_confidence("correlation_confidence", inputs=["sample_size", "uncertainty_interval"], scoring="correlation_confidence"),
            evidence_requirements=[
                EvidenceCardEvidenceRequirement(evidence_type="repeated_observations", required=True, minimum_count=2, accepted_packet_types=["temporal_consistency_packet", "cross_modal_consistency_packet"]),
            ],
            caveat_requirements=["Association does not establish causation."],
            provenance_requirements=["List the observation packets and the estimator that produced the association."],
            fallback_card_type=fallback,
        )

    if card_type == "safety_assessment":
        return EvidenceCardRequirements(
            card_type="safety_assessment",
            primary_question="Does the structured evidence support a safety event?",
            title_template=title,
            claim_target="safety_event_candidate",
            allowed_conclusion_types=["safety_event_candidate", "cross_modal_agreement", "timestamped_event_summary"],
            forbidden_conclusion_types=[
                "medical_diagnosis",
                "confirmed_injury",
                "personal_identity",
                "intent_inference",
                "cause_or_intent_inference",
            ],
            badge_requirements=[
                _card_type_badge(),
                EvidenceCardBadgeRequirement(badge_id="event_class", label_template="{event_classification}", source="harness_output", required=True),
            ],
            metric_requirements=[
                _metric("event_time", "Event time", "Timestamp of the candidate safety event.", "timestamp", "harness_output", required=True),
                _metric("event_classification", "Event classification", "Coarse event category (e.g. fall-like).", "category", "harness_output", required=True),
                _metric("modality_support", "Modality support", "Modalities corroborating the event.", "category", "aggregator", required=True),
                _metric("tracking_confidence", "Tracking confidence", "Confidence in identity-free track continuity.", "percentage", "aggregator", required=True),
                _metric("post_event_stillness", "Post-event stillness", "Duration of stillness after the event, if observed.", "duration", "harness_output", required=False),
                _metric("overall_confidence", "Overall confidence", "Composite confidence in the safety assessment.", "percentage", "aggregator", required=True),
            ],
            summary_requirements=["Describe the candidate event and its corroboration without diagnosing injury, identity, intent, or cause."],
            confidence_requirements=_confidence("safety_assessment_confidence", inputs=["modality_support", "tracking_confidence"], scoring="safety_assessment_confidence"),
            evidence_requirements=[
                EvidenceCardEvidenceRequirement(evidence_type="safety_event", required=True, minimum_count=1, accepted_packet_types=["fall_event_packet", "radar_motion_packet", "wifi_presence_packet", "cross_modal_consistency_packet"]),
            ],
            caveat_requirements=[
                "Not a medical diagnosis; no injury is confirmed.",
                "No identity, intent, or cause is asserted.",
            ],
            provenance_requirements=["List the event and corroboration packets and their modalities."],
            fallback_card_type=fallback,
        )

    if card_type == "causal":
        # Mirrors the original CausalCard field set (effect, CI, p-value, rows
        # analyzed, graph support, graph consistency, composite confidence).
        return EvidenceCardRequirements(
            card_type="causal",
            primary_question="What is the estimated causal effect of the treatment on the outcome?",
            title_template=title,
            claim_target="causal_effect_estimate",
            allowed_conclusion_types=["causal_effect_estimate", "direction_of_effect"],
            forbidden_conclusion_types=["personal_identity", "unsupported_causal_claim"],
            badge_requirements=[
                _card_type_badge(),
                EvidenceCardBadgeRequirement(badge_id="effect_direction", label_template="{direction}", source="statistical_estimator", required=True),
                EvidenceCardBadgeRequirement(badge_id="estimator", label_template="{method}", source="statistical_estimator", required=True),
                EvidenceCardBadgeRequirement(badge_id="contrast", label_template="{treatment}: {treated} vs {control}", source="statistical_estimator", required=True),
            ],
            metric_requirements=[
                _metric("estimated_effect", "Estimated effect", "Average effect attributable to the treatment contrast.", "float", "statistical_estimator", required=True, source_field="ate", unavailable="fallback_card"),
                _metric("uncertainty_interval", "Likely range (95% CI)", "Primary uncertainty range from the main estimator.", "range", "statistical_estimator", required=True, source_field="ci", unavailable="fallback_card"),
                _metric("statistical_evidence", "Statistical evidence (p-value)", "Smaller values indicate stronger statistical evidence.", "float", "statistical_estimator", required=True, source_field="p_value"),
                _metric("rows_analyzed", "Rows analyzed", "Rows remaining after applying the parsed conditions.", "integer", "statistical_estimator", required=True, source_field="sample_size", unavailable="fallback_card"),
                _metric("graph_support", "Graph support", "Graph-based support for the queried treatment direction.", "category", "statistical_estimator", required=True, source_field="dag_support"),
                _metric("graph_consistency", "Graph consistency", "How often the edge reappeared in discovery bootstrap runs.", "float", "statistical_estimator", required=True, source_field="edge_stability"),
                _metric("confidence_score", "Confidence score", "Composite confidence (0-100) for the estimate.", "percentage", "statistical_estimator", required=True, source_field="confidence_score"),
            ],
            summary_requirements=["State the direction and size of the estimated effect with its uncertainty; do not overstate certainty."],
            confidence_requirements=_confidence(
                "causal_confidence",
                inputs=["treatment", "outcome", "contrast", "estimator", "adjustment_set", "sample_size", "p_value", "graph_consistency"],
                scoring="composite_causal_confidence",
            ),
            evidence_requirements=[
                EvidenceCardEvidenceRequirement(evidence_type="causal_estimate", required=True, minimum_count=1, accepted_packet_types=["causal_estimate_packet"]),
            ],
            caveat_requirements=[
                "Assumes no unobserved confounders after adjustment.",
                "Estimate is conditional on the learned graph and adjustment set.",
            ],
            provenance_requirements=["List treatment, outcome, contrast, estimator, adjustment set, and rows analyzed."],
            fallback_card_type=fallback,
        )

    if card_type == "privacy_blocked":
        return EvidenceCardRequirements(
            card_type="privacy_blocked",
            primary_question="Was the request within the allowed privacy scope?",
            title_template="Request exceeded the allowed privacy scope",
            claim_target="privacy_blocked_notice",
            allowed_conclusion_types=["privacy_blocked_notice"],
            forbidden_conclusion_types=list(_GLOBAL_FORBIDDEN_CONCLUSIONS),
            badge_requirements=[_card_type_badge()],
            metric_requirements=[
                _metric("blocked_claim_class", "Blocked claim class", "The class of claim that was blocked.", "category", "aggregator", required=True),
                _metric("privacy_preserving_alternative", "Privacy-preserving alternative", "Whether a privacy-preserving alternative was offered.", "text", "aggregator", required=False),
            ],
            summary_requirements=["State that the request exceeded the allowed privacy scope and what class of claim was blocked.", "Do not reveal any sensitive detail."],
            confidence_requirements=_confidence("composite_confidence", inputs=[], scoring="none"),
            evidence_requirements=[],
            caveat_requirements=["No sensitive or identifying details are disclosed."],
            provenance_requirements=["State which privacy policy clause blocked the request."],
            fallback_card_type="privacy_blocked",
        )

    if card_type == "unsupported":
        card_type = "insufficient_evidence"

    # insufficient_evidence (default)
    return EvidenceCardRequirements(
        card_type="insufficient_evidence",
        primary_question="Could the system answer this from the available evidence?",
        title_template="Insufficient evidence to answer confidently",
        claim_target="insufficient_evidence",
        allowed_conclusion_types=["insufficient_evidence"],
        forbidden_conclusion_types=list(_GLOBAL_FORBIDDEN_CONCLUSIONS) + ["fabricated_confidence"],
        badge_requirements=[_card_type_badge()],
        metric_requirements=[
            _metric("what_determined", "What was determined", "What the system could establish, if anything.", "text", "aggregator", required=True),
            _metric("missing_evidence", "Missing evidence", "What evidence was missing or insufficient.", "text", "aggregator", required=True),
            _metric("failed_modality", "Failed harness/modality", "Which harness or modality failed, if safe to disclose.", "text", "tracefix_verification", required=False, unavailable="omit"),
        ],
        summary_requirements=["State what could and could not be determined; do not fabricate confidence."],
        confidence_requirements=_confidence("composite_confidence", inputs=[], scoring="none"),
        evidence_requirements=[],
        caveat_requirements=["A stronger answer was not produced because the bounded evidence was insufficient."],
        provenance_requirements=["List which expected packets were missing or empty."],
        fallback_card_type="insufficient_evidence",
    )


# ---------------------------------------------------------------------------
# Validation / repair of an LLM-proposed card contract
# ---------------------------------------------------------------------------

def build_validated_card_requirements(
    *,
    task_category: str,
    normalized_query: str,
    answer_requirements: AnswerPacketRequirements,
    candidate_harnesses: List[str],
    proposed: Optional[EvidenceCardRequirements] = None,
) -> Tuple[EvidenceCardRequirements, List[str]]:
    """Produce the authoritative card contract, repairing the LLM proposal.

    Only descriptive/textual fields are taken from the proposal. All numeric
    metric definitions, confidence scoring, badge sources, and conclusion-type
    allowances come from the deterministic baseline so the LLM cannot inject a
    fabricated metric or an over-strong conclusion.
    """
    repairs: List[str] = []
    causal_supported = bool(set(candidate_harnesses) & CAUSAL_CAPABLE_HARNESSES)

    card_type, type_notes = resolve_card_type(
        task_category=task_category,
        normalized_query=normalized_query,
        causal_supported=causal_supported,
        proposed_card_type=proposed.card_type if proposed else None,
    )
    repairs.extend(type_notes)

    requirements = _base_requirements(card_type)

    # Carry over safe, descriptive fields from the proposal when present.
    if proposed is not None:
        if proposed.primary_question.strip():
            requirements.primary_question = proposed.primary_question.strip()
        if proposed.title_template.strip() and card_type not in {"privacy_blocked", "insufficient_evidence"}:
            requirements.title_template = proposed.title_template.strip()
        if proposed.summary_requirements:
            requirements.summary_requirements = _dedupe(
                requirements.summary_requirements + list(proposed.summary_requirements)
            )
        if proposed.caveat_requirements:
            requirements.caveat_requirements = _dedupe(
                requirements.caveat_requirements + list(proposed.caveat_requirements)
            )
        if proposed.provenance_requirements:
            requirements.provenance_requirements = _dedupe(
                requirements.provenance_requirements + list(proposed.provenance_requirements)
            )
        # Reject any metric/confidence/badge structure the LLM tried to redefine.
        if _proposal_redefined_values(proposed, requirements):
            repairs.append(
                "Discarded LLM-supplied metric/confidence/badge definitions; deterministic "
                "card rules are authoritative."
            )

    # Keep card conclusions synchronized with the answer-packet forbidden claims.
    merged_forbidden = _dedupe(
        list(requirements.forbidden_conclusion_types)
        + list(answer_requirements.forbidden_claims)
        + _GLOBAL_FORBIDDEN_CONCLUSIONS
    )
    requirements.forbidden_conclusion_types = merged_forbidden
    # An allowed conclusion can never also be forbidden.
    cleaned_allowed = [c for c in requirements.allowed_conclusion_types if c not in merged_forbidden]
    if len(cleaned_allowed) != len(requirements.allowed_conclusion_types):
        repairs.append("Removed allowed card conclusions that conflicted with forbidden claims.")
    requirements.allowed_conclusion_types = cleaned_allowed

    return requirements, repairs


def _proposal_redefined_values(
    proposed: EvidenceCardRequirements,
    authoritative: EvidenceCardRequirements,
) -> bool:
    auth_metric_ids = {m.metric_id for m in authoritative.metric_requirements}
    for metric in proposed.metric_requirements:
        if metric.metric_id not in auth_metric_ids:
            return True
    if proposed.confidence_requirements.confidence_kind != authoritative.confidence_requirements.confidence_kind:
        return True
    return False


# ---------------------------------------------------------------------------
# Runtime helpers (Phase 7 + 8)
# ---------------------------------------------------------------------------

def build_dryrun_card_preview(
    requirements: EvidenceCardRequirements,
    *,
    task_id: str,
    card_id: Optional[str] = None,
) -> EvidenceCardPacket:
    """Build a placeholder card preview for the dry-run flow.

    No values are fabricated: every metric is marked ``dry_run_placeholder`` with
    a "—" display value so the UI can render the contract shape without implying
    measurement. This is distinct from a populated ``EvidenceCardPacket``.
    """
    metrics = [
        EvidenceCardMetric(
            metric_id=req.metric_id,
            label=req.display_label,
            value=None,
            display_value="—",
            explanation=req.description,
            source=req.source,
            source_packet_ids=[],
            availability="dry_run_placeholder",
        )
        for req in requirements.metric_requirements
    ]
    badges = [
        EvidenceCardBadge(
            badge_id=req.badge_id,
            label=req.label_template.replace("{card_type}", requirements.card_type),
            source=req.source,
            tone="info",
        )
        for req in requirements.badge_requirements
    ]
    confidence = RenderedConfidence(
        confidence_kind=requirements.confidence_requirements.confidence_kind,
        score=None,
        label=None,
        explanation="Populated only after harness execution.",
        inputs=list(requirements.confidence_requirements.required_inputs),
        availability="dry_run_placeholder",
    )
    return EvidenceCardPacket(
        card_id=card_id or f"card_{task_id}",
        task_id=task_id,
        card_type=requirements.card_type,
        title=requirements.title_template.replace("{primary_question}", requirements.primary_question),
        subtitle="Dry-run preview — values populated only after harness execution.",
        conclusion="Pending harness execution.",
        conclusion_class=requirements.claim_target,
        badges=badges,
        metrics=metrics,
        confidence=confidence,
        evidence_summary=list(requirements.summary_requirements),
        evidence_refs=[],
        limitations=list(requirements.caveat_requirements),
        provenance={"mode": "dry_run", "provenance_requirements": list(requirements.provenance_requirements)},
        validation_status="valid",
    )


def check_answer_card_consistency(
    *,
    requirements: EvidenceCardRequirements,
    claim: ValidatedClaim,
    card: EvidenceCardPacket,
    answer_requirements: Optional[AnswerPacketRequirements] = None,
) -> List[str]:
    """Return consistency errors between a validated claim, answer, and card.

    Rejects when: the card asserts a stronger conclusion than allowed, a metric
    lacks a source packet, a value came from a non-approved source, a causal card
    lacks causal outputs, forbidden claims appear, or required provenance is
    missing. Both the prose answer and the card are expected to derive from the
    same ``ValidatedClaim``.
    """
    errors: List[str] = []
    approved_sources = {"harness_output", "aggregator", "statistical_estimator", "tracefix_verification"}

    # 1. Card conclusion must be an allowed conclusion type.
    if card.conclusion_class not in requirements.allowed_conclusion_types and card.validation_status == "valid":
        errors.append(
            f"Card conclusion_class '{card.conclusion_class}' is not in allowed "
            f"conclusion types {requirements.allowed_conclusion_types}."
        )

    # 2. No forbidden conclusion may appear on the card.
    if card.conclusion_class in requirements.forbidden_conclusion_types:
        errors.append(f"Card conclusion_class '{card.conclusion_class}' is forbidden.")
    if claim.conclusion_class in requirements.forbidden_conclusion_types:
        errors.append(f"Claim conclusion_class '{claim.conclusion_class}' is forbidden.")

    # 3. Card cannot present a stronger conclusion than the claim allows.
    if not claim.allowed and card.validation_status == "valid":
        errors.append("Card presents a conclusion the validated claim marked as not allowed.")

    # 4. Every real/mock metric needs an approved source and a source packet.
    for metric in card.metrics:
        if metric.availability in {"real", "mock"}:
            if metric.source not in approved_sources:
                errors.append(f"Metric '{metric.metric_id}' value came from a non-approved source '{metric.source}'.")
            if not metric.source_packet_ids:
                errors.append(f"Metric '{metric.metric_id}' has no source packet id.")

    # 5. A causal card must carry causal-estimate outputs.
    if requirements.card_type == "causal":
        present = {m.metric_id for m in card.metrics if m.availability in {"real", "mock"}}
        for needed in ("estimated_effect", "uncertainty_interval", "rows_analyzed"):
            if needed not in present:
                errors.append(f"Causal card is missing required causal output '{needed}'.")

    # 6. Required provenance must be present on a valid card.
    if requirements.provenance_requirements and card.validation_status == "valid" and not card.provenance:
        errors.append("Card is missing required provenance.")

    # 7. Answer/card claim text alignment: confidence may not exceed claim confidence.
    if (
        card.confidence is not None
        and card.confidence.score is not None
        and claim.confidence is not None
        and card.confidence.score > claim.confidence + 1e-9
    ):
        errors.append("Card confidence exceeds the validated claim confidence.")

    # 8. Forbidden answer claims must also be forbidden on the card.
    if answer_requirements is not None:
        for forbidden in answer_requirements.forbidden_claims:
            if forbidden not in requirements.forbidden_conclusion_types:
                errors.append(f"Forbidden answer claim '{forbidden}' is not forbidden on the card.")

    return errors


def _dedupe(values: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
