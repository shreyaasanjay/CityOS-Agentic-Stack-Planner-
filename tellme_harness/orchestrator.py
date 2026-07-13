"""Deterministic TeLLMe agent orchestrator."""

from __future__ import annotations

from typing import Tuple

from .capability_registry import CapabilityRegistry
from .cityos_discovery import CityOSDiscoveryError
from .execution_plan import build_execution_plan
from .intent_decomposition import (
    build_policy_envelope,
    build_task_spec_with_brief,
    infer_allowed_harnesses,
    infer_allowed_modalities,
    task_spec_to_intent_decomposition,
)
from .llm_client import FakeLLMClient, LLMClient
from .query_analysis import analyze_query
from .route_policy import decide_route
from .schemas import ExecutionPlan, QueryAnalysis, RouteDecision, TellMeQuery


class AgentOrchestrator:
    """Build deterministic intermediate routing objects for one query."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        llm_backend_mode: str = "deterministic",
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.llm_client = llm_client or FakeLLMClient()
        self.llm_backend_mode = llm_backend_mode
        self.capability_registry = capability_registry or CapabilityRegistry()

    def analyze_and_route(
        self,
        query: TellMeQuery,
    ) -> Tuple[QueryAnalysis, RouteDecision, ExecutionPlan]:
        analysis = analyze_query(query)
        decision = decide_route(query, analysis)
        decomposition = None
        proposal = None
        validation_result = None
        task_spec = None
        capability_snapshot = None
        discovery_provenance = None
        room_context = None
        brief = None
        if decision.route in {"single_agent", "multi_agent"}:
            space_id = query.space_id or "smart_room_1"
            try:
                # Discover CityOS capabilities for this space, then project them onto
                # this query before any LLM decomposition runs.
                room_context = self.capability_registry.get_relevant_context(
                    query_id=query.query_id,
                    space_id=space_id,
                    analysis=analysis,
                    time_window=decision.time_window,
                )
                capability_snapshot = self.capability_registry.get_snapshot(space_id)
                discovery_provenance = self.capability_registry.get_last_provenance(space_id)
            except CityOSDiscoveryError as exc:
                blocked = decision.model_copy(
                    update={
                        "route": "not_answerable",
                        "requires_tracefix": False,
                        "rationale": "CityOS capability discovery was unavailable, so TeLLMe failed closed.",
                        "caveats": list(decision.caveats)
                        + [
                            "Capability discovery unavailable.",
                            "TeLLMe did not fall back to unrestricted mock capabilities in this mode.",
                            str(exc),
                        ],
                    }
                )
                plan = build_execution_plan(query, analysis, blocked)
                return analysis, blocked, plan
            allowed_modalities = infer_allowed_modalities(analysis, decision.route)
            allowed_harnesses = infer_allowed_harnesses(analysis, decision.route, decision.selected_agent)
            allowed_time_windows = [decision.time_window] if decision.time_window else []
            policy_envelope = build_policy_envelope(
                analysis=analysis,
                route_decision=decision,
                allowed_harnesses=allowed_harnesses,
                allowed_modalities=allowed_modalities,
                allowed_time_windows=allowed_time_windows,
                space_id=space_id,
                answer_contract=None,
                room_context=room_context,
            )
            task_spec, proposal, validation_result, brief = build_task_spec_with_brief(
                query=query.user_query,
                policy_envelope=policy_envelope,
                llm_client=self.llm_client,
            )
            decomposition = task_spec_to_intent_decomposition(task_spec)
        plan = build_execution_plan(
            query,
            analysis,
            decision,
            decomposition,
            proposal=proposal,
            validation_result=validation_result,
            task_spec=task_spec,
            llm_backend_mode=self.llm_backend_mode,
            capability_snapshot=capability_snapshot.model_dump() if capability_snapshot else None,
            discovery_provenance=discovery_provenance,
            room_capability_context=room_context.model_dump() if room_context else None,
            execution_brief=brief.model_dump() if brief else None,
        )
        return analysis, decision, plan
