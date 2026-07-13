"""Execution plan builder for TeLLMe Harness V0."""

from __future__ import annotations

from uuid import uuid4

from .schemas import (
    DEFAULT_OUTPUT_CONTRACT,
    ExecutionPlan,
    IntentDecomposition,
    LLMDecompositionProposal,
    ProposalValidationResult,
    QueryAnalysis,
    RouteDecision,
    TellMeQuery,
    TraceFixTaskSpec,
)

_SINGLE_AGENT_ESCALATION_TRIGGERS = [
    "missing_context",
    "malformed_context",
    "low_confidence",
    "modality_disagreement",
    "tool_budget_exceeded",
    "requires_multiple_context_objects",
]


def build_execution_plan(
    query: TellMeQuery,
    analysis: QueryAnalysis,
    decision: RouteDecision,
    decomposition: IntentDecomposition | None = None,
    proposal: LLMDecompositionProposal | None = None,
    validation_result: ProposalValidationResult | None = None,
    task_spec: TraceFixTaskSpec | None = None,
    llm_backend_mode: str | None = None,
    capability_snapshot: dict | None = None,
    discovery_provenance: dict | None = None,
    room_capability_context: dict | None = None,
    execution_brief: dict | None = None,
) -> ExecutionPlan:
    if decision.route in {"single_agent", "multi_agent"}:
        allowed_tools = list(decision.required_tools)
        if not allowed_tools and decision.selected_tool:
            allowed_tools = [decision.selected_tool]
        return ExecutionPlan(
            plan_type="tracefix",
            query_id=query.query_id,
            task_id=f"task_{uuid4().hex[:12]}",
            selected_agent=decision.selected_agent,
            allowed_tools=allowed_tools,
            max_tool_calls=0,
            context_requirements=list(analysis.context_requirements),
            escalation_allowed=decision.route == "single_agent",
            escalation_triggers=list(_SINGLE_AGENT_ESCALATION_TRIGGERS) if decision.route == "single_agent" else [],
            required_harnesses=list(decomposition.required_harnesses) if decomposition else list(task_spec.candidate_harnesses) if task_spec else [],
            time_windows=list(decomposition.time_windows) if decomposition else list(task_spec.time_windows) if task_spec else ([decision.time_window] if decision.time_window else []),
            required_modalities=list(decomposition.required_modalities) if decomposition else list(task_spec.required_modalities) if task_spec else [],
            output_contract=decomposition.output_contract if decomposition else dict(task_spec.output_contract) if task_spec else dict(DEFAULT_OUTPUT_CONTRACT),
            llm_backend_mode=llm_backend_mode,
            llm_decomposition_proposal=proposal.model_dump() if proposal else None,
            proposal_validation=validation_result.model_dump() if validation_result else None,
            intent_decomposition=decomposition.model_dump() if decomposition else None,
            tracefix_task_spec=task_spec.model_dump() if task_spec else None,
            tracefix_bundle_summary=None,
            cityos_capability_snapshot=capability_snapshot,
            discovery_provenance=discovery_provenance,
            room_capability_context=room_capability_context,
            smartspace_execution_brief=execution_brief,
        )

    if decision.route == "needs_clarification":
        return ExecutionPlan(
            plan_type="needs_clarification",
            query_id=query.query_id,
            allowed_tools=[],
            max_tool_calls=0,
            context_requirements=list(analysis.context_requirements),
            escalation_allowed=False,
            escalation_triggers=[],
        )

    return ExecutionPlan(
        plan_type="not_answerable",
        query_id=query.query_id,
        allowed_tools=[],
        max_tool_calls=0,
        context_requirements=list(analysis.context_requirements),
        escalation_allowed=False,
        escalation_triggers=[],
    )
