"""Structured schemas for the TeLLMe Harness V0."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

DEFAULT_OUTPUT_CONTRACT = {
    "required_fields": ["answer", "confidence", "evidence_refs", "caveats"],
    "field_types": {
        "answer": "string",
        "confidence": "number",
        "evidence_refs": "array",
        "caveats": "array",
    },
}

RouteType = Literal[
    "single_agent",
    "multi_agent",
    "needs_clarification",
    "not_allowed",
    "not_answerable",
]
IntentType = Literal[
    "live_state",
    "historical_lookup",
    "event_explanation",
    "diagnostic",
    "policy_privacy",
    "general",
]
AnswerStatus = Literal[
    "answered",
    "needs_tracefix",
    "needs_clarification",
    "not_answerable",
    "error",
]


class TimeWindow(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    label: Optional[str] = None


AmbiguityType = Literal[
    "space",
    "time",
    "intent",
    "modality",
    "scope",
]


class QueryAmbiguity(BaseModel):
    """Whether a query is under-specified enough to block executable planning.

    This is distinct from the deterministic ``needs_clarification`` route, which
    fires *before* decomposition. ``QueryAmbiguity`` is produced *after* the LLM
    proposal/brief is assembled: a ``clarification_required`` brief still compiles,
    but to a non-executable TaskSpec that asks for clarification instead of
    coordinating harnesses.
    """

    is_ambiguous: bool = False
    clarification_required: bool = False
    ambiguity_type: Optional[AmbiguityType] = None
    clarifying_question: Optional[str] = None
    reasons: list[str] = Field(default_factory=list)


class TellMeQuery(BaseModel):
    query_id: str
    user_query: str
    space_id: Optional[str] = None
    timestamp: Optional[str] = None
    created_at: str
    intent: IntentType = "general"


class RouteDecision(BaseModel):
    route: RouteType
    intent: IntentType
    selected_agent: Optional[str] = None
    selected_tool: Optional[str] = None
    required_tools: List[str] = Field(default_factory=list)
    rationale: str
    time_window: Optional[TimeWindow] = None
    requires_tracefix: bool = False
    caveats: List[str] = Field(default_factory=list)
    trigger_terms_found: List[str] = Field(default_factory=list)
    explicit_agent_names_detected: bool = False


class QueryAnalysis(BaseModel):
    query_id: str
    user_query: str
    intent: str
    answer_type: str
    space_scope: str
    time_scope: str
    context_requirements: List[str] = Field(default_factory=list)
    named_modalities: List[str] = Field(default_factory=list)
    estimated_tool_calls: int
    requires_multi_modal_reconciliation: bool = False
    requires_multi_timestamp_reasoning: bool = False
    requires_identity_continuity: bool = False
    requires_diagnostic_reasoning: bool = False
    requires_concordfs_trace_inspection: bool = False
    requires_policy_review: bool = False
    requires_explicit_multi_agent: bool = False
    trigger_terms_found: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    confidence: float


class RouteScore(BaseModel):
    score: int
    hard_gate_tracefix: bool = False
    reasons: List[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    plan_type: Literal["tracefix", "needs_clarification", "not_answerable"]
    query_id: str
    task_id: Optional[str] = None
    llm_backend_mode: Optional[str] = None
    selected_agent: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    max_tool_calls: int = 0
    context_requirements: List[str] = Field(default_factory=list)
    escalation_allowed: bool = False
    escalation_triggers: List[str] = Field(default_factory=list)
    required_harnesses: List[str] = Field(default_factory=list)
    time_windows: List[TimeWindow] = Field(default_factory=list)
    required_modalities: List[str] = Field(default_factory=list)
    output_contract: Dict[str, Any] = Field(
        default_factory=lambda: {
            "required_fields": list(DEFAULT_OUTPUT_CONTRACT["required_fields"]),
            "field_types": dict(DEFAULT_OUTPUT_CONTRACT["field_types"]),
        }
    )
    llm_decomposition_proposal: Optional[Dict[str, Any]] = None
    proposal_validation: Optional[Dict[str, Any]] = None
    intent_decomposition: Optional[Dict[str, Any]] = None
    tracefix_task_spec: Optional[Dict[str, Any]] = None
    tracefix_bundle_summary: Optional[Dict[str, Any]] = None
    cityos_capability_snapshot: Optional[Dict[str, Any]] = None
    room_capability_context: Optional[Dict[str, Any]] = None
    smartspace_execution_brief: Optional[Dict[str, Any]] = None


class CityOSContextObject(BaseModel):
    context_id: str
    space_id: str
    timestamp: str
    context_type: str
    modality: str
    sensor_id: Optional[str] = None
    source_api: Optional[str] = None
    start_timestamp: Optional[str] = None
    end_timestamp: Optional[str] = None
    value: dict[str, Any]
    confidence: float
    evidence_refs: list[str] = Field(default_factory=list)
    privacy_scope: str
    retention_policy: str
    limitations: list[str] = Field(default_factory=list)
    trace_id: Optional[str] = None
    checkpoint_id: Optional[str] = None


class ContextWindow(BaseModel):
    query_id: str
    user_query: str
    space_id: Optional[str] = None
    selected_intent: IntentType
    relevant_time_windows: list[TimeWindow] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    routing_decision: RouteDecision
    privacy_constraints: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    available_context_objects: list[CityOSContextObject] = Field(default_factory=list)


class ToolCallSpec(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentPlan(BaseModel):
    query_id: str
    selected_agent: str
    selected_tool: str
    tool_call: ToolCallSpec
    steps: list[str] = Field(default_factory=list)
    escalation_possible: bool = True


class TraceFixTaskSpec(BaseModel):
    task_id: str
    query_id: str
    user_query: str
    space_id: Optional[str] = None
    route: Literal["single_agent", "multi_agent"] = "multi_agent"
    time_windows: list[TimeWindow] = Field(default_factory=list)
    required_modalities: list[str] = Field(default_factory=list)
    candidate_harnesses: list[str] = Field(default_factory=list)
    application_goal: Dict[str, Any] = Field(default_factory=dict)
    evidence_plan: Dict[str, Any] = Field(default_factory=dict)
    answer_packet_requirements: Dict[str, Any] = Field(default_factory=dict)
    # Validated, deterministic card contract carried through to TraceFix. This is
    # the authoritative card requirement set, not the raw LLM proposal.
    evidence_card_contract: Dict[str, Any] = Field(default_factory=dict)
    output_contract: Dict[str, Any] = Field(
        default_factory=lambda: {
            "required_fields": list(DEFAULT_OUTPUT_CONTRACT["required_fields"]),
            "field_types": dict(DEFAULT_OUTPUT_CONTRACT["field_types"]),
        }
    )
    privacy_policy: Dict[str, Any] = Field(default_factory=dict)
    validation_policy: Dict[str, Any] = Field(default_factory=dict)
    escalation_conditions: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    executable: bool = True
    reason: str = ""
    target_tracefix_path: str = "TraceFix-main/"
    status: Literal["stub"] = "stub"
    caveats: list[str] = Field(default_factory=list)


class HarnessSubTask(BaseModel):
    harness_name: str
    space_id: Optional[str] = None
    time_window: Optional[TimeWindow] = None
    expected_modalities: list[str] = Field(default_factory=list)


class IntentDecomposition(BaseModel):
    query_id: str
    plan_type: Literal["tracefix"] = "tracefix"
    required_harnesses: list[str] = Field(default_factory=list)
    harness_subtasks: list[HarnessSubTask] = Field(default_factory=list)
    required_modalities: list[str] = Field(default_factory=list)
    time_windows: list[TimeWindow] = Field(default_factory=list)
    needs_cross_modal_consistency: bool = False
    needs_temporal_consistency: bool = False
    output_contract: Dict[str, Any] = Field(
        default_factory=lambda: {
            "required_fields": list(DEFAULT_OUTPUT_CONTRACT["required_fields"]),
            "field_types": dict(DEFAULT_OUTPUT_CONTRACT["field_types"]),
        }
    )
    llm_notes: list[str] = Field(default_factory=list)
    safety_caveats: list[str] = Field(default_factory=list)


class ApplicationGoal(BaseModel):
    goal_type: str
    user_intent: str
    success_condition: str
    failure_condition: str
    non_goals: list[str] = Field(default_factory=list)


class ProposedHarness(BaseModel):
    name: str
    role: str
    priority: Literal["required", "supporting", "optional"]
    expected_packet: str
    rationale: Optional[str] = None


class EvidencePlan(BaseModel):
    primary_evidence: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    minimum_sufficient_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence_checks: list[str] = Field(default_factory=list)


class AnswerPacketRequirements(BaseModel):
    answer_type: str
    required_fields: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    must_include_confidence: bool = True
    must_include_evidence_refs: bool = True
    must_include_limitations: bool = True
    fallback_answer_type: str = "insufficient_evidence"


class LLMDecompositionProposal(BaseModel):
    original_query: str
    normalized_query: str
    task_category: Literal[
        "occupancy_count",
        "presence_check",
        "event_detection",
        "temporal_correlation",
        "safety_event_assessment",
        "unsupported",
    ]
    inferred_user_goal: str
    application_goal: ApplicationGoal
    proposed_harnesses: list[ProposedHarness] = Field(default_factory=list)
    evidence_plan: EvidencePlan
    answer_packet_requirements: AnswerPacketRequirements
    # Front-facing card contract the LLM proposes. Optional on input so that a
    # real LLM omitting it does not fail validation; the deterministic validator
    # always (re)builds an authoritative contract before compilation.
    evidence_card_requirements: Optional[EvidenceCardRequirements] = None
    uncertainty_analysis: list[str] = Field(default_factory=list)
    escalation_conditions: list[str] = Field(default_factory=list)
    privacy_risk_notes: list[str] = Field(default_factory=list)
    # CityOS capability awareness (added for capability-grounded planning). All
    # optional so existing fixed-key proposal builders and tests stay valid.
    room_context_summary: str = ""
    referenced_sensors: list[str] = Field(default_factory=list)
    referenced_context_apis: list[str] = Field(default_factory=list)
    ambiguity: Optional[QueryAmbiguity] = None
    reasoning_summary: str


# ---------------------------------------------------------------------------
# Evidence-card models.
#
# These generalize the original TeLLMe ``CausalCard`` (rebuild-tellme-main/
# explain/card_schema.py) into a front-facing evidence artifact for any
# supported query type. Three distinct objects are kept separate on purpose:
#
#   EvidenceCardRequirements  -> planning-time CONTRACT (what the card must hold)
#   EvidenceCardPacket        -> runtime artifact populated from real outputs
#   ValidatedClaim            -> single source of truth shared by answer + card
#
# Core principle carried over from the original stack: every numeric value on a
# card is produced deterministically by an estimator/harness/aggregator. The LLM
# only describes structure and writes prose; it never supplies measured values.
# ---------------------------------------------------------------------------

CardType = Literal[
    "descriptive",
    "temporal",
    "correlational",
    "causal",
    "safety_assessment",
    "insufficient_evidence",
    "privacy_blocked",
    "unsupported",
]
FallbackCardType = Literal[
    "insufficient_evidence",
    "privacy_blocked",
    "unsupported",
]
# Approved value sources. Note: an LLM is deliberately NOT an allowed source for
# any metric value. Badges may additionally be sourced from query_analysis
# (e.g. a card-type label) since those are categorical, not measured numbers.
MetricSource = Literal[
    "harness_output",
    "aggregator",
    "statistical_estimator",
    "tracefix_verification",
]
BadgeSource = Literal[
    "query_analysis",
    "harness_output",
    "aggregator",
    "statistical_estimator",
    "tracefix_verification",
]
MetricValueType = Literal[
    "integer",
    "float",
    "percentage",
    "duration",
    "timestamp",
    "category",
    "range",
    "boolean",
    "text",
]
ConfidenceKind = Literal[
    "sensor_confidence",
    "temporal_link_confidence",
    "correlation_confidence",
    "causal_confidence",
    "safety_assessment_confidence",
    "composite_confidence",
]


class EvidenceRef(BaseModel):
    ref_id: str
    packet_type: Optional[str] = None
    modality: Optional[str] = None
    time_window: Optional[TimeWindow] = None
    description: Optional[str] = None


class EvidenceCardBadgeRequirement(BaseModel):
    badge_id: str
    label_template: str
    source: BadgeSource
    required: bool = False


class EvidenceCardMetricRequirement(BaseModel):
    metric_id: str
    display_label: str
    description: str
    required: bool
    value_type: MetricValueType
    source: MetricSource
    source_field: Optional[str] = None
    unavailable_behavior: Literal["omit", "show_na", "fallback_card"] = "show_na"


class EvidenceCardConfidenceRequirements(BaseModel):
    confidence_kind: ConfidenceKind
    required_inputs: list[str] = Field(default_factory=list)
    minimum_threshold: Optional[float] = None
    label_bands: Dict[str, list[float]] = Field(default_factory=dict)
    explanation_required: bool = True
    scoring_method: Optional[str] = None


class EvidenceCardEvidenceRequirement(BaseModel):
    evidence_type: str
    required: bool = True
    minimum_count: int = 1
    accepted_packet_types: list[str] = Field(default_factory=list)
    include_evidence_refs: bool = True
    include_time_window: bool = True
    include_modality: bool = True


class EvidenceCardRequirements(BaseModel):
    """Planning-time contract describing how the final card must be constructed."""

    card_type: CardType
    primary_question: str
    title_template: str
    claim_target: str

    allowed_conclusion_types: list[str] = Field(default_factory=list)
    forbidden_conclusion_types: list[str] = Field(default_factory=list)

    badge_requirements: list[EvidenceCardBadgeRequirement] = Field(default_factory=list)
    metric_requirements: list[EvidenceCardMetricRequirement] = Field(default_factory=list)

    summary_requirements: list[str] = Field(default_factory=list)
    confidence_requirements: EvidenceCardConfidenceRequirements
    evidence_requirements: list[EvidenceCardEvidenceRequirement] = Field(default_factory=list)

    caveat_requirements: list[str] = Field(default_factory=list)
    provenance_requirements: list[str] = Field(default_factory=list)

    fallback_card_type: FallbackCardType = "insufficient_evidence"


# --- Runtime (post-execution) card artifact -------------------------------

MetricAvailability = Literal["real", "mock", "unavailable", "dry_run_placeholder"]


class EvidenceCardBadge(BaseModel):
    badge_id: str
    label: str
    source: str
    tone: Literal["ok", "warn", "info", "neutral"] = "neutral"


class EvidenceCardMetric(BaseModel):
    metric_id: str
    label: str
    value: Any = None
    display_value: str
    explanation: str = ""
    source: str = ""
    source_packet_ids: list[str] = Field(default_factory=list)
    availability: MetricAvailability = "unavailable"


class RenderedConfidence(BaseModel):
    confidence_kind: str
    score: Optional[float] = None
    label: Optional[str] = None
    explanation: str = ""
    inputs: list[str] = Field(default_factory=list)
    availability: MetricAvailability = "unavailable"


class EvidenceCardPacket(BaseModel):
    """Runtime card populated only from validated outputs (never LLM-invented)."""

    card_id: str
    task_id: str
    card_type: str

    title: str
    subtitle: Optional[str] = None

    conclusion: str
    conclusion_class: str

    badges: list[EvidenceCardBadge] = Field(default_factory=list)
    metrics: list[EvidenceCardMetric] = Field(default_factory=list)

    confidence: Optional[RenderedConfidence] = None
    evidence_summary: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)

    validation_status: Literal[
        "valid",
        "insufficient_evidence",
        "privacy_blocked",
        "contract_failed",
    ] = "valid"


class ValidatedClaim(BaseModel):
    """Single validated claim shared by the prose answer and the evidence card."""

    claim_type: str
    claim_text: str
    claim_value: Optional[Any] = None
    conclusion_class: str
    confidence: Optional[float] = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    allowed: bool = True
    limitations: list[str] = Field(default_factory=list)


class ProposalValidationResult(BaseModel):
    valid: bool
    validation_status: Literal["accepted", "repaired", "rejected"]
    repaired: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    validated_proposal: Optional[LLMDecompositionProposal] = None


# ---------------------------------------------------------------------------
# CityOS capability discovery models.
#
# These describe *what a space can do* (sensors, context APIs, privacy policy) as
# metadata only. They are deliberately separate from ``CityOSContextObject``,
# which carries runtime *evidence values*. Capability metadata may inform
# planning; runtime values must not (they belong to harness execution).
# ---------------------------------------------------------------------------

ModalityLiteral = Literal["video", "radar", "wifi", "audio", "fusion", "context"]
RelevanceLiteral = Literal["primary", "supporting", "unavailable"]


class SensorCapability(BaseModel):
    sensor_id: str
    modality: ModalityLiteral
    space_id: str
    description: str = ""
    available: bool = True
    status: str = "online"
    privacy_tier: str = "structured_context_only"
    supported_context_types: list[str] = Field(default_factory=list)
    coverage_time_window: Optional[TimeWindow] = None
    placement: str = ""
    orientation: str = ""
    coverage_zones: list[str] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    supported_capabilities: list[str] = Field(default_factory=list)
    unsupported_capabilities: list[str] = Field(default_factory=list)
    allowed_api_names: list[str] = Field(default_factory=list)
    restricted_api_names: list[str] = Field(default_factory=list)
    retention_window: Optional[str] = None
    data_availability: Optional[str] = None


class ContextAPICapability(BaseModel):
    """A CityOS structured-context API the space exposes.

    ``api_name`` is expected to align with the CityOS APIs declared in the
    harness registry (e.g. ``get_occupancy_context``) so the planner can map
    available APIs to executable harnesses.
    """

    api_name: str
    description: str = ""
    modality: ModalityLiteral = "context"
    returns_packet_type: Optional[str] = None
    requires_privacy_scope: str = "cityos_structured_context_only"
    available: bool = True
    data_level: str = "derived_context"
    required_arguments: list[str] = Field(default_factory=list)
    supported_time_query_modes: list[str] = Field(default_factory=list)
    privacy_level: str = "derived_context"
    raw_access: bool = False
    retention_limits: Optional[str] = None
    authorization_scope: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)
    documentation_reference: Optional[str] = None
    owner_sensor_ids: list[str] = Field(default_factory=list)


class PrivacyPolicyCapability(BaseModel):
    policy_id: str
    privacy_scope: str = "cityos_structured_context_only"
    raw_sensor_access_allowed: bool = False
    identity_inference_allowed: bool = False
    forbidden_inferences: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CityOSCapabilitySnapshot(BaseModel):
    snapshot_id: str
    space_id: str
    generated_at: str
    sensors: list[SensorCapability] = Field(default_factory=list)
    context_apis: list[ContextAPICapability] = Field(default_factory=list)
    privacy_policies: list[PrivacyPolicyCapability] = Field(default_factory=list)
    source: Literal["mock", "live"] = "mock"
    schema_version: str = "1.0"


class RelevantSensorCapability(BaseModel):
    sensor_id: str
    modality: ModalityLiteral
    relevance: RelevanceLiteral
    available: bool = True
    status: str = "online"
    reason: str = ""
    supported_context_types: list[str] = Field(default_factory=list)
    coverage_zones: list[str] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    allowed_api_names: list[str] = Field(default_factory=list)
    restricted_api_names: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RoomCapabilityContext(BaseModel):
    """Query-scoped projection of a capability snapshot.

    Produced by the capability registry for a single query: which sensors are
    relevant, which context APIs are usable, the governing privacy policy, and
    any coverage gaps the planner must respect.
    """

    query_id: str
    space_id: str
    snapshot_id: str
    relevant_sensors: list[RelevantSensorCapability] = Field(default_factory=list)
    available_context_apis: list[str] = Field(default_factory=list)
    privacy_policy: PrivacyPolicyCapability
    coverage_gaps: list[str] = Field(default_factory=list)
    time_window: Optional[TimeWindow] = None
    notes: list[str] = Field(default_factory=list)


class SmartspaceExecutionBrief(BaseModel):
    """Capability-grounded semantic source of truth for a planned query.

    The brief is assembled after the LLM proposal is validated and the room
    capability context is resolved. The ``TraceFixTaskSpec`` is then compiled
    *from* the brief, and the human-readable TraceFix design prompt is rendered
    *from* the brief as well, so both share one authoritative source.
    """

    brief_id: str
    query_id: str
    space_id: Optional[str] = None
    user_query: str
    normalized_query: str = ""
    route: Literal["single_agent", "multi_agent"] = "multi_agent"
    task_category: str = "unsupported"
    application_goal: ApplicationGoal
    evidence_plan: EvidencePlan
    answer_packet_requirements: AnswerPacketRequirements
    evidence_card_requirements: EvidenceCardRequirements
    candidate_harnesses: list[str] = Field(default_factory=list)
    required_modalities: list[str] = Field(default_factory=list)
    time_windows: list[TimeWindow] = Field(default_factory=list)
    room_capability_context: Optional[RoomCapabilityContext] = None
    privacy_policy: Dict[str, Any] = Field(default_factory=dict)
    validation_policy: Dict[str, Any] = Field(default_factory=dict)
    escalation_conditions: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    ambiguity: QueryAmbiguity = Field(default_factory=QueryAmbiguity)
    executable: bool = True
    reasoning_summary: str = ""
    caveats: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TraceFix coordination plan.
#
# This is the typed boundary between TeLLMe and TraceFix. It is compiled
# DETERMINISTICALLY from a validated SmartspaceExecutionBrief — the LLM never
# emits it directly. It maps to TraceFix's IR v3 (agents / resources / channels)
# when a workspace is built; the descriptive policy/invariant fields are carried
# alongside for the design prompt and (future) TLA+ property generation.
# ---------------------------------------------------------------------------

TimeoutBehavior = Literal["fail", "continue_with_limitation", "escalate"]
DependencyRequirement = Literal["required", "optional", "one_of"]


class TraceFixAgentPlan(BaseModel):
    agent_id: str
    role: str
    harness_name: str
    domain_tools: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    produced_outputs: list[str] = Field(default_factory=list)
    optional: bool = False
    retry_limit: int = 0
    timeout_behavior: TimeoutBehavior = "escalate"


class TraceFixDependency(BaseModel):
    producer_agent_id: str
    consumer_agent_id: str
    packet_type: str
    requirement: DependencyRequirement = "required"


class TraceFixChannelPlan(BaseModel):
    channel_id: str
    from_agent: str
    to_agent: str
    packet_types: list[str] = Field(default_factory=list)


class TraceFixCompletionPolicy(BaseModel):
    required_agents: list[str] = Field(default_factory=list)
    terminal_agent: str = "claim_builder"
    completion_packet: str = "ValidatedClaim"


class TraceFixFailurePolicy(BaseModel):
    on_required_failure: Literal["verification_failed", "insufficient_evidence", "escalate"] = "insufficient_evidence"
    optional_agents_may_timeout: bool = True
    block_on_missing_required_evidence: bool = True


class TraceFixInvariant(BaseModel):
    invariant_id: str
    description: str
    kind: Literal["ordering", "exclusivity", "evidence_required", "no_deadlock", "privacy"] = "ordering"


class TraceFixCoordinationPlan(BaseModel):
    plan_id: str
    task_id: str
    template: str = "generic_linear"
    agents: list[TraceFixAgentPlan] = Field(default_factory=list)
    dependencies: list[TraceFixDependency] = Field(default_factory=list)
    channels: list[TraceFixChannelPlan] = Field(default_factory=list)
    completion_policy: TraceFixCompletionPolicy = Field(default_factory=TraceFixCompletionPolicy)
    failure_policy: TraceFixFailurePolicy = Field(default_factory=TraceFixFailurePolicy)
    invariants: list[TraceFixInvariant] = Field(default_factory=list)
    source_brief_id: str = ""
    executable: bool = True
    notes: list[str] = Field(default_factory=list)


class TraceFixWorkspaceBuildResult(BaseModel):
    workspace_path: str
    ir_path: str
    spec_files: list[str] = Field(default_factory=list)
    ir_valid: Optional[bool] = None
    ir_validation_errors: list[str] = Field(default_factory=list)
    ir_validation_source: str = "none"  # "tracefix_validator" | "bundled_schema" | "none"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TraceFixToolchainStatus(BaseModel):
    java_found: bool = False
    java_path: Optional[str] = None
    java_version: Optional[str] = None
    java_compatible: bool = False  # >= 11 (TLC v1.8.0 needs class file 55)

    tla2tools_found: bool = False
    tla2tools_path: Optional[str] = None

    tree_sitter_found: bool = False
    tree_sitter_tlaplus_found: bool = False

    verification_available: bool = False        # java_compatible AND jar present
    state_extraction_available: bool = False     # + tree-sitter (optional, separate)

    blockers: list[str] = Field(default_factory=list)


class TraceFixVerificationResult(BaseModel):
    status: Literal[
        "verified",
        "counterexample",
        "generation_failed",
        "translation_failed",
        "tlc_failed",
        "toolchain_unavailable",
        "artifact_missing",
    ]
    verified: bool = False
    executable: bool = False

    workspace_path: str
    protocol_tla_path: Optional[str] = None
    protocol_cfg_path: Optional[str] = None
    translated_tla_path: Optional[str] = None
    states_path: Optional[str] = None

    invariants_checked: list[str] = Field(default_factory=list)
    # Honest disclosure of WHAT was verified. The deterministic IR scaffold only
    # carries structural invariants (TypeInvariant/ChannelsDrained); coordination
    # ordering lives in PlusCal process bodies that the LLM/opencode design step
    # fills, so it is NOT covered here.
    verification_scope: Literal["structural_scaffold", "coordination_protocol"] = "structural_scaffold"
    state_count: Optional[int] = None
    distinct_state_count: Optional[int] = None

    counterexample_summary: Optional[str] = None
    failure_stage: Optional[str] = None
    sanitized_error: Optional[str] = None

    duration_ms: int = 0
    toolchain: TraceFixToolchainStatus = Field(default_factory=TraceFixToolchainStatus)


class AnswerPacket(BaseModel):
    query_id: str
    status: AnswerStatus
    answer: str
    confidence: float = 0.0
    basis: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    privacy_scope: str = "cityos_structured_context_only"
    caveats: list[str] = Field(default_factory=list)
    selected_agent: Optional[str] = None
    route_decision: dict[str, Any] = Field(default_factory=dict)
    agent_plan: dict[str, Any] = Field(default_factory=dict)
    raw_outputs: dict[str, Any] = Field(default_factory=dict)
    tracefix_task_spec: Optional[TraceFixTaskSpec] = None
    error_message: Optional[str] = None
