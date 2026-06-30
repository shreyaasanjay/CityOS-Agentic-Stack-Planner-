"""Render a human-readable TraceFix design prompt from a SmartspaceExecutionBrief.

This is a deterministic, side-effect-free renderer: given the brief (the semantic
source of truth), it produces the Markdown handoff document a future TraceFix
orchestrator would read to design a multi-agent investigation. It invents no
values and never embeds secrets — only fields already present on the brief.
"""

from __future__ import annotations

from .schemas import SmartspaceExecutionBrief


def render_tracefix_design_prompt(brief: SmartspaceExecutionBrief) -> str:
    lines: list[str] = [
        "# TraceFix Design Prompt",
        "",
        f"Brief ID: {brief.brief_id}",
        f"Query ID: {brief.query_id}",
        f"Space ID: {brief.space_id or 'n/a'}",
        f"Route: {brief.route}",
        f"Task category: {brief.task_category}",
        f"Executable: {brief.executable}",
        "",
        "## User Query",
        brief.user_query,
    ]

    if brief.ambiguity.clarification_required:
        lines += [
            "",
            "## Clarification Required",
            f"- type: {brief.ambiguity.ambiguity_type or 'unspecified'}",
            f"- question: {brief.ambiguity.clarifying_question or 'Please clarify the request.'}",
            "- This brief is NOT executable until the user clarifies; do not coordinate harnesses.",
        ]

    lines += ["", "## Application Goal"]
    lines += _kv_block(
        {
            "goal_type": brief.application_goal.goal_type,
            "user_intent": brief.application_goal.user_intent,
            "success_condition": brief.application_goal.success_condition,
            "failure_condition": brief.application_goal.failure_condition,
        }
    )
    if brief.application_goal.non_goals:
        lines.append("- non_goals: " + ", ".join(brief.application_goal.non_goals))

    lines += _capability_section(brief)

    lines += ["", "## Required Modalities", "- " + (", ".join(brief.required_modalities) or "none")]

    lines += ["", "## Candidate Harnesses"]
    lines += [f"- {name}" for name in brief.candidate_harnesses] or ["- none"]

    lines += ["", "## Time Windows"]
    if brief.time_windows:
        for window in brief.time_windows:
            label = window.label or "time_window"
            lines.append(f"- {label}: start={window.start or 'n/a'}, end={window.end or 'n/a'}")
    else:
        lines.append("- none specified")

    lines += ["", "## Evidence Plan"]
    lines += [
        "- primary: " + (", ".join(brief.evidence_plan.primary_evidence) or "none"),
        "- supporting: " + (", ".join(brief.evidence_plan.supporting_evidence) or "none"),
        "- minimum_sufficient: " + (", ".join(brief.evidence_plan.minimum_sufficient_evidence) or "none"),
        "- conflicting_checks: " + (", ".join(brief.evidence_plan.conflicting_evidence_checks) or "none"),
    ]

    lines += ["", "## Answer Contract"]
    lines += [
        f"- answer_type: {brief.answer_packet_requirements.answer_type}",
        f"- fallback_answer_type: {brief.answer_packet_requirements.fallback_answer_type}",
        "- allowed_claims: " + (", ".join(brief.allowed_claims) or "none"),
        "- forbidden_claims: " + (", ".join(brief.forbidden_claims) or "none"),
    ]

    lines += ["", "## Evidence Card", f"- card_type: {brief.evidence_card_requirements.card_type}", f"- claim_target: {brief.evidence_card_requirements.claim_target}"]

    lines += ["", "## Privacy Policy"]
    lines += _kv_block(brief.privacy_policy) if brief.privacy_policy else ["- none"]

    lines += ["", "## Escalation Conditions"]
    lines += [f"- {item}" for item in brief.escalation_conditions] or ["- none"]

    lines += ["", "## Caveats"]
    lines += [f"- {item}" for item in brief.caveats] or ["- none"]

    lines += [
        "",
        "## Execution Note",
        "This is a design prompt for future TraceFix coordination. TraceFix-main is not invoked in V0.",
    ]
    return "\n".join(lines) + "\n"


def _capability_section(brief: SmartspaceExecutionBrief) -> list[str]:
    context = brief.room_capability_context
    if context is None:
        return ["", "## CityOS Capabilities", "- No capability context resolved for this query."]
    lines = ["", "## CityOS Capabilities", f"- snapshot: {context.snapshot_id}"]
    for sensor in context.relevant_sensors:
        marker = sensor.relevance if sensor.available else "unavailable"
        lines.append(f"- {sensor.sensor_id} ({sensor.modality}): {marker}")
    lines.append("- available_context_apis: " + (", ".join(context.available_context_apis) or "none"))
    if context.coverage_gaps:
        lines.append("- coverage_gaps:")
        lines += [f"  - {gap}" for gap in context.coverage_gaps]
    return lines


def _kv_block(payload: dict) -> list[str]:
    return [f"- {key}: {payload[key]}" for key in payload]
