"""Deterministic compilation of a SmartspaceExecutionBrief into a TraceFix plan.

This is the TeLLMe→TraceFix boundary. The LLM never emits a coordination plan or
IR directly; this module compiles them deterministically from the *validated*
brief. ``compile_coordination_plan`` produces a :class:`TraceFixCoordinationPlan`,
and ``coordination_plan_to_ir`` maps it onto TraceFix's IR v3 (agents / resources
/ channels), which is structurally validated against TraceFix's own schema by the
workspace adapter.

The first milestone ships one concrete template — *entry + impact sound* — which
encodes: parallel evidence collection → required join → optional supporting
evidence → temporal correlation → claim generation. A generic linear fallback is
used for any other executable brief.
"""

from __future__ import annotations

from typing import Any, Dict

from .schemas import (
    SmartspaceExecutionBrief,
    TraceFixAgentPlan,
    TraceFixChannelPlan,
    TraceFixCompletionPolicy,
    TraceFixCoordinationPlan,
    TraceFixDependency,
    TraceFixFailurePolicy,
    TraceFixInvariant,
)

ENTRY_PLUS_IMPACT_TEMPLATE = "entry_plus_impact"
GENERIC_LINEAR_TEMPLATE = "generic_linear"

_ENTRY_TOKENS = ("enter", "entered", "entry", "walked in", "came in")
_IMPACT_TOKENS = ("impact", "loud", "bang", "thud", "crash", "noise", "sound")


def is_entry_plus_impact(brief: SmartspaceExecutionBrief) -> bool:
    text = f"{brief.normalized_query} {brief.user_query}".lower()
    has_entry = any(token in text for token in _ENTRY_TOKENS)
    has_impact = any(token in text for token in _IMPACT_TOKENS)
    return has_entry and has_impact


def compile_coordination_plan(brief: SmartspaceExecutionBrief) -> TraceFixCoordinationPlan:
    """Deterministically compile the brief into a coordination plan."""
    task_id = brief.brief_id.replace("brief_", "", 1) or brief.query_id
    if brief.executable and is_entry_plus_impact(brief):
        return _entry_plus_impact_plan(brief, task_id)
    return _generic_linear_plan(brief, task_id)


def _entry_plus_impact_plan(brief: SmartspaceExecutionBrief, task_id: str) -> TraceFixCoordinationPlan:
    agents = [
        TraceFixAgentPlan(
            agent_id="entry_event_agent",
            role="Retrieve an anonymous doorway entry event (no identity).",
            harness_name="entry_event_harness",
            domain_tools=["get_entry_event_context"],
            produced_outputs=["EntryEventPacket"],
            timeout_behavior="escalate",
        ),
        TraceFixAgentPlan(
            agent_id="impact_sound_agent",
            role="Retrieve an impact-sound candidate (no transcript, no speaker id).",
            harness_name="audio_context_harness",
            domain_tools=["get_impact_sound_context"],
            produced_outputs=["ImpactSoundPacket"],
            timeout_behavior="escalate",
        ),
        TraceFixAgentPlan(
            agent_id="room_camera_agent",
            role="Optional supporting visual evidence; may be occluded.",
            harness_name="video_context_harness",
            domain_tools=["get_motion_event_context", "get_posture_candidate_context", "get_camera_occlusion_context"],
            produced_outputs=["VisualEventCandidatePacket", "OcclusionPacket"],
            optional=True,
            timeout_behavior="continue_with_limitation",
        ),
        TraceFixAgentPlan(
            agent_id="temporal_correlator",
            role="Correlate entry and impact within the requested interval (no causality).",
            harness_name="temporal_consistency_harness",
            required_inputs=["EntryEventPacket", "ImpactSoundPacket"],
            produced_outputs=["EventCorrelationPacket"],
            timeout_behavior="escalate",
        ),
        TraceFixAgentPlan(
            agent_id="claim_builder",
            role="Produce the single ValidatedClaim from the correlation (no fall/injury claim).",
            harness_name="answer_synthesis_harness",
            required_inputs=["EventCorrelationPacket"],
            produced_outputs=["ValidatedClaim"],
            timeout_behavior="fail",
        ),
    ]

    dependencies = [
        TraceFixDependency(producer_agent_id="entry_event_agent", consumer_agent_id="temporal_correlator", packet_type="EntryEventPacket", requirement="required"),
        TraceFixDependency(producer_agent_id="impact_sound_agent", consumer_agent_id="temporal_correlator", packet_type="ImpactSoundPacket", requirement="required"),
        TraceFixDependency(producer_agent_id="room_camera_agent", consumer_agent_id="temporal_correlator", packet_type="VisualEventCandidatePacket", requirement="optional"),
        TraceFixDependency(producer_agent_id="temporal_correlator", consumer_agent_id="claim_builder", packet_type="EventCorrelationPacket", requirement="required"),
    ]

    channels = [
        TraceFixChannelPlan(channel_id="ch_entry", from_agent="entry_event_agent", to_agent="temporal_correlator", packet_types=["EntryEventPacket"]),
        TraceFixChannelPlan(channel_id="ch_impact", from_agent="impact_sound_agent", to_agent="temporal_correlator", packet_types=["ImpactSoundPacket"]),
        TraceFixChannelPlan(channel_id="ch_room", from_agent="room_camera_agent", to_agent="temporal_correlator", packet_types=["VisualEventCandidatePacket", "OcclusionPacket"]),
        TraceFixChannelPlan(channel_id="ch_correlation", from_agent="temporal_correlator", to_agent="claim_builder", packet_types=["EventCorrelationPacket"]),
    ]

    invariants = [
        TraceFixInvariant(invariant_id="join_before_correlation", kind="ordering",
                          description="temporal_correlator must not act before both EntryEventPacket and ImpactSoundPacket exist."),
        TraceFixInvariant(invariant_id="correlation_before_claim", kind="ordering",
                          description="claim_builder must not act before an EventCorrelationPacket exists."),
        TraceFixInvariant(invariant_id="single_claim_producer", kind="exclusivity",
                          description="Only claim_builder may produce a ValidatedClaim."),
        TraceFixInvariant(invariant_id="required_evidence_present", kind="evidence_required",
                          description="No final claim may be produced before required evidence packets exist."),
        TraceFixInvariant(invariant_id="optional_visual_no_deadlock", kind="no_deadlock",
                          description="Absent or occluded room-camera evidence must not deadlock the workflow."),
        TraceFixInvariant(invariant_id="privacy_bounded", kind="privacy",
                          description="No identity, transcript, raw media, or fall/injury claim may be produced."),
    ]

    completion = TraceFixCompletionPolicy(
        required_agents=["entry_event_agent", "impact_sound_agent", "temporal_correlator", "claim_builder"],
        terminal_agent="claim_builder",
        completion_packet="ValidatedClaim",
    )
    failure = TraceFixFailurePolicy(
        on_required_failure="insufficient_evidence",
        optional_agents_may_timeout=True,
        block_on_missing_required_evidence=True,
    )

    return TraceFixCoordinationPlan(
        plan_id=f"plan_{task_id}",
        task_id=task_id,
        template=ENTRY_PLUS_IMPACT_TEMPLATE,
        agents=agents,
        dependencies=dependencies,
        channels=channels,
        completion_policy=completion,
        failure_policy=failure,
        invariants=invariants,
        source_brief_id=brief.brief_id,
        executable=brief.executable,
        notes=[
            "Deterministic entry+impact template; no LLM-authored IR/PlusCal.",
            "Visual evidence is optional/supporting and must not deadlock the join.",
        ],
    )


def _generic_linear_plan(brief: SmartspaceExecutionBrief, task_id: str) -> TraceFixCoordinationPlan:
    """Fallback: evidence harnesses (parallel) → correlation → claim_builder."""
    evidence_harnesses = [h for h in brief.candidate_harnesses if h != "answer_synthesis_harness"]
    agents: list[TraceFixAgentPlan] = []
    channels: list[TraceFixChannelPlan] = []
    dependencies: list[TraceFixDependency] = []

    for index, harness in enumerate(evidence_harnesses):
        agent_id = f"evidence_agent_{index}"
        packet = f"{harness}_packet"
        agents.append(
            TraceFixAgentPlan(
                agent_id=agent_id,
                role=f"Collect evidence via {harness}.",
                harness_name=harness,
                produced_outputs=[packet],
                timeout_behavior="escalate",
            )
        )
        channels.append(
            TraceFixChannelPlan(channel_id=f"ch_{agent_id}", from_agent=agent_id, to_agent="claim_builder", packet_types=[packet])
        )
        dependencies.append(
            TraceFixDependency(producer_agent_id=agent_id, consumer_agent_id="claim_builder", packet_type=packet, requirement="required")
        )

    agents.append(
        TraceFixAgentPlan(
            agent_id="claim_builder",
            role="Synthesize the single ValidatedClaim.",
            harness_name="answer_synthesis_harness",
            required_inputs=[f"{h}_packet" for h in evidence_harnesses],
            produced_outputs=["ValidatedClaim"],
            timeout_behavior="fail",
        )
    )

    invariants = [
        TraceFixInvariant(invariant_id="single_claim_producer", kind="exclusivity",
                          description="Only claim_builder may produce a ValidatedClaim."),
        TraceFixInvariant(invariant_id="required_evidence_present", kind="evidence_required",
                          description="No final claim before required evidence packets exist."),
    ]

    return TraceFixCoordinationPlan(
        plan_id=f"plan_{task_id}",
        task_id=task_id,
        template=GENERIC_LINEAR_TEMPLATE,
        agents=agents,
        dependencies=dependencies,
        channels=channels,
        completion_policy=TraceFixCompletionPolicy(
            required_agents=[a.agent_id for a in agents],
            terminal_agent="claim_builder",
            completion_packet="ValidatedClaim",
        ),
        failure_policy=TraceFixFailurePolicy(),
        invariants=invariants,
        source_brief_id=brief.brief_id,
        executable=brief.executable and bool(evidence_harnesses),
        notes=["Generic linear fallback plan (no specialized template matched)."],
    )


def coordination_plan_to_ir(plan: TraceFixCoordinationPlan) -> Dict[str, Any]:
    """Map a coordination plan onto TraceFix IR v3 (agents / resources / channels).

    Joins are modeled by the receiver's blocking receives on multiple channels
    (per the IR's FIFO-channel semantics), so no explicit resource is required.
    """
    agents = [
        {"id": agent.agent_id, **({"tools": list(agent.domain_tools)} if agent.domain_tools else {})}
        for agent in plan.agents
    ]
    channels = [
        {
            "id": channel.channel_id,
            "from": channel.from_agent,
            "to": channel.to_agent,
            "labels": list(channel.packet_types),
        }
        for channel in plan.channels
    ]
    return {"agents": agents, "resources": [], "channels": channels}
