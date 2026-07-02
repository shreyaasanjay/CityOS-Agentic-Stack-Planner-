"""Compatibility wrapper around the deterministic agent orchestrator."""

from __future__ import annotations

from .orchestrator import AgentOrchestrator
from .schemas import RouteDecision, TellMeQuery

_ORCHESTRATOR = AgentOrchestrator()


def route_query(query: TellMeQuery) -> RouteDecision:
    """Preserve the public V0 router API while using the new orchestrator."""
    _analysis, decision, _plan = _ORCHESTRATOR.analyze_and_route(query)
    return decision
