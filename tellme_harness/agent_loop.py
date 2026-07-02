"""Single-agent loop with pluggable backends for the simple-query path."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .agent_protocol import AgentAction, AgentLoopTrace, ToolExecutionResult, ValidationResult
from .answer_validator import validate_answer_packet
from .context_validator import validate_cityos_context
from .llm_agent import AgentBackend, DeterministicAgentBackend, validate_agent_action
from .schemas import AnswerPacket, ContextWindow, ExecutionPlan, RouteDecision
from .tool_executor import ToolExecutor


class SingleAgentLoop:
    def __init__(
        self,
        tool_executor: ToolExecutor,
        agent_backend: Optional[AgentBackend] = None,
        max_iterations: int = 3,
    ) -> None:
        self.tool_executor = tool_executor
        self.agent_backend = agent_backend or DeterministicAgentBackend()
        self.max_iterations = max_iterations
        self.last_context_validation: Optional[ValidationResult] = None
        self.last_answer_validation: Optional[ValidationResult] = None

    def run(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
    ) -> Tuple[AnswerPacket, AgentLoopTrace]:
        trace = AgentLoopTrace(query_id=context_window.query_id)
        self.last_context_validation = None
        self.last_answer_validation = None
        self.agent_backend.reset()

        if route_decision.requires_tracefix or route_decision.route != "single_agent":
            action = AgentAction(
                action_type="escalate_to_tracefix",
                escalation_reason="Route already requires TraceFix.",
            )
            trace.final_action = action
            trace.escalated = True
            trace.escalation_reason = action.escalation_reason
            return self._build_escalation_packet(
                context_window,
                route_decision,
                "This query requires TraceFix before a single-agent loop can run.",
            ), trace

        tool_budget = min(self.max_iterations, max(1, execution_plan.max_tool_calls))
        requested_timestamp = None
        if route_decision.time_window:
            requested_timestamp = route_decision.time_window.start

        initial_action, initial_action_meta = self._safe_backend_action(
            phase="initial_action",
            query_id=context_window.query_id,
            action_factory=lambda: self.agent_backend.initial_action(
                context_window=context_window,
                route_decision=route_decision,
                execution_plan=execution_plan,
            ),
        )
        if isinstance(initial_action, AnswerPacket):
            trace.final_action = AgentAction(
                action_type="escalate_to_tracefix" if initial_action.status == "needs_tracefix" else "final_answer",
                escalation_reason=initial_action.error_message or "Initial agent action failed validation.",
            )
            trace.escalated = initial_action.status == "needs_tracefix"
            trace.escalation_reason = trace.final_action.escalation_reason
            return initial_action, trace

        action = initial_action
        action_meta = initial_action_meta
        for iteration in range(1, tool_budget + 1):
            trace.iterations.append({"iteration": iteration, "action": action.model_dump()})
            if action_meta:
                trace.iterations[-1]["llm_phase"] = action_meta.get("phase")
                trace.iterations[-1]["llm_prompt_index"] = action_meta.get("prompt_index")
                trace.iterations[-1]["llm_raw_action"] = action_meta.get("raw_action")
            action_errors = validate_agent_action(action)
            trace.iterations[-1]["action_validation"] = {
                "valid": not action_errors,
                "errors": list(action_errors),
            }

            if action_errors:
                trace.final_action = AgentAction(
                    action_type="escalate_to_tracefix",
                    escalation_reason="AgentAction validation failed.",
                )
                trace.escalated = True
                trace.escalation_reason = "AgentAction validation failed."
                return self._build_escalation_packet(
                    context_window,
                    route_decision,
                    "The single-agent path produced an invalid action and is escalating to TraceFix.",
                    "AgentAction validation failed.",
                ), trace

            if action.action_type == "escalate_to_tracefix":
                trace.final_action = action
                trace.escalated = True
                trace.escalation_reason = action.escalation_reason
                return self._build_escalation_packet(
                    context_window,
                    route_decision,
                    "The single-agent backend requested escalation to TraceFix.",
                    action.escalation_reason,
                ), trace

            if action.action_type == "final_answer":
                validated_context = self._latest_validated_context(trace)
                if validated_context is None:
                    trace.final_action = AgentAction(
                        action_type="escalate_to_tracefix",
                        escalation_reason="Agent proposed final_answer before any validated CityOS context was retrieved.",
                    )
                    trace.escalated = True
                    trace.escalation_reason = trace.final_action.escalation_reason
                    return self._build_escalation_packet(
                        context_window,
                        route_decision,
                        "The single-agent path could not produce a safe answer without validated CityOS context.",
                        trace.final_action.escalation_reason,
                    ), trace
                answer_packet = self._build_answer_packet_from_action(
                    context_window=context_window,
                    route_decision=route_decision,
                    action=action,
                    validated_context=validated_context,
                    validation_warnings=self._latest_context_warnings(trace),
                )
                answer_validation = validate_answer_packet(answer_packet)
                self.last_answer_validation = answer_validation
                trace.validation_results.append(answer_validation)
                trace.iterations[-1]["answer_validation"] = answer_validation.model_dump()

                if not answer_validation.valid:
                    trace.final_action = AgentAction(
                        action_type="escalate_to_tracefix",
                        escalation_reason="Final answer packet validation failed.",
                    )
                    trace.escalated = True
                    trace.escalation_reason = "Final answer packet validation failed."
                    return self._build_escalation_packet(
                        context_window,
                        route_decision,
                        "The single-agent path could not produce a safe validated answer.",
                        "Final answer packet validation failed.",
                    ), trace

                trace.final_action = action
                answer_packet.raw_outputs["agent_loop_trace_summary"] = self._trace_summary(trace)
                return answer_packet, trace

            if action.action_type != "tool_request" or not action.tool_name:
                trace.final_action = AgentAction(
                    action_type="escalate_to_tracefix",
                    escalation_reason="AgentAction did not specify a usable tool request.",
                )
                trace.escalated = True
                trace.escalation_reason = trace.final_action.escalation_reason
                return self._build_escalation_packet(
                    context_window,
                    route_decision,
                    "The single-agent path could not determine a safe next step.",
                    trace.final_action.escalation_reason,
                ), trace

            tool_result = self.tool_executor.execute(action.tool_name, action.arguments)
            trace.tool_results.append(tool_result)
            trace.iterations[-1]["tool_result"] = tool_result.model_dump()

            if not tool_result.success:
                trace.final_action = AgentAction(
                    action_type="escalate_to_tracefix",
                    escalation_reason=tool_result.error or "Tool execution failed.",
                )
                return self._build_error_packet(
                    context_window,
                    route_decision,
                    "The single-agent path failed to return a safe answer.",
                    "The harness encountered an execution error while using mock CityOS context.",
                    tool_result.error or "Unknown tool execution error.",
                ), trace

            expected_types = _expected_context_types(action.tool_name or route_decision.selected_tool)
            context_validation = validate_cityos_context(
                output=tool_result.output,
                expected_context_types=expected_types,
                query_space_id=context_window.space_id,
                requested_timestamp=requested_timestamp,
            )
            self.last_context_validation = context_validation
            trace.validation_results.append(context_validation)
            trace.iterations[-1]["context_validation"] = context_validation.model_dump()

            if not context_validation.valid:
                trace.final_action = AgentAction(
                    action_type="escalate_to_tracefix",
                    escalation_reason="Structured context validation failed.",
                )
                trace.escalated = True
                trace.escalation_reason = "Structured context validation failed."
                return self._build_escalation_packet(
                    context_window,
                    route_decision,
                    "The single-agent path could not validate the structured context safely.",
                    "Structured context validation failed.",
                ), trace

            if iteration >= tool_budget:
                break

            next_action, next_action_meta = self._safe_backend_action(
                phase="next_action",
                query_id=context_window.query_id,
                action_factory=lambda: self.agent_backend.next_action(
                    context_window=context_window,
                    route_decision=route_decision,
                    execution_plan=execution_plan,
                    loop_trace=trace,
                ),
            )
            if isinstance(next_action, AnswerPacket):
                trace.final_action = AgentAction(
                    action_type="escalate_to_tracefix" if next_action.status == "needs_tracefix" else "final_answer",
                    escalation_reason=next_action.error_message or "Next agent action failed validation.",
                )
                trace.escalated = next_action.status == "needs_tracefix"
                trace.escalation_reason = trace.final_action.escalation_reason
                return next_action, trace
            action = next_action
            action_meta = next_action_meta

        trace.final_action = AgentAction(
            action_type="escalate_to_tracefix",
            escalation_reason="Tool budget exceeded.",
        )
        trace.escalated = True
        trace.escalation_reason = "Tool budget exceeded."
        return self._build_escalation_packet(
            context_window,
            route_decision,
            "The single-agent loop exceeded its safe tool budget and is escalating to TraceFix.",
            "Tool budget exceeded.",
        ), trace

    def _safe_backend_action(
        self,
        phase: str,
        query_id: str,
        action_factory: Any,
    ) -> Tuple[Any, Optional[Dict[str, Any]]]:
        pre_count = len(self.agent_backend.get_action_log())
        try:
            action = action_factory()
        except Exception as exc:
            if self.agent_backend.uses_llm:
                return AnswerPacket(
                    query_id=query_id,
                    status="needs_tracefix",
                    answer="The LLM-backed single-agent path could not produce a valid action and is escalating to TraceFix.",
                    confidence=0.0,
                    caveats=[
                        "The optional LLM backend returned an invalid or unparsable action.",
                        "The orchestrator preserved tool and validator control.",
                    ],
                    error_message=str(exc),
                ), None
            return AnswerPacket(
                query_id=query_id,
                status="error",
                answer="The single-agent path failed to return a safe answer.",
                confidence=0.0,
                caveats=["The harness encountered an execution error while selecting the next action."],
                error_message=str(exc),
            ), None
        action_meta = None
        action_log = self.agent_backend.get_action_log()
        if len(action_log) > pre_count:
            action_meta = dict(action_log[-1])
        return action, action_meta

    def _build_answer_packet_from_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        action: AgentAction,
        validated_context: Optional[Dict[str, Any]],
        validation_warnings: List[str],
    ) -> AnswerPacket:
        caveats = [
            "This answer is based on simulated CityOS context from local mock JSON fixtures.",
            "V0 uses one structured context lookup and may need TraceFix for more complex reasoning.",
        ]
        caveats.extend(route_decision.caveats)
        caveats.extend(validation_warnings)
        caveats.extend(action.caveats)
        evidence_refs = list(action.evidence_refs)
        confidence = action.confidence if action.confidence is not None else 0.0
        privacy_scope = "cityos_structured_context_only"
        basis = [f"Selected agent: {route_decision.selected_agent}"]
        if route_decision.selected_tool:
            basis.append(f"Selected tool: {route_decision.selected_tool}")
        if validated_context:
            confidence = action.confidence if action.confidence is not None else validated_context.get("confidence", 0.0)
            if not evidence_refs:
                evidence_refs = list(validated_context.get("evidence_refs", []))
            privacy_scope = validated_context.get("privacy_scope", privacy_scope)
            basis.append("Context object: {context_id}".format(context_id=validated_context.get("context_id")))

        return AnswerPacket(
            query_id=context_window.query_id,
            status="answered",
            answer=action.answer or "",
            confidence=confidence,
            basis=basis,
            evidence_refs=evidence_refs,
            privacy_scope=privacy_scope,
            caveats=caveats,
            selected_agent=route_decision.selected_agent,
            route_decision=route_decision.model_dump(),
            raw_outputs={"cityos_context": validated_context or {}},
        )

    def _build_escalation_packet(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        answer: str,
        escalation_reason: Optional[str],
    ) -> AnswerPacket:
        return AnswerPacket(
            query_id=context_window.query_id,
            status="needs_tracefix",
            answer=answer,
            confidence=0.0,
            caveats=[
                "The single-agent loop could not safely complete this query.",
                "Escalation to a future TraceFix workflow is required.",
            ],
            route_decision=route_decision.model_dump(),
            raw_outputs={
                "agent_loop_trace_summary": {"escalated": True, "escalation_reason": escalation_reason},
            },
        )

    def _build_error_packet(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        answer: str,
        caveat: str,
        error_message: str,
    ) -> AnswerPacket:
        return AnswerPacket(
            query_id=context_window.query_id,
            status="error",
            answer=answer,
            confidence=0.0,
            caveats=[caveat],
            route_decision=route_decision.model_dump(),
            raw_outputs={"agent_loop_trace_summary": {"escalated": False}},
            error_message=error_message,
        )

    def _trace_summary(self, trace: AgentLoopTrace) -> Dict[str, Any]:
        return {
            "iterations": len(trace.iterations),
            "tool_calls": len(trace.tool_results),
            "validation_steps": len(trace.validation_results),
            "escalated": trace.escalated,
            "escalation_reason": trace.escalation_reason,
        }

    def _latest_validated_context(self, trace: AgentLoopTrace) -> Optional[Dict[str, Any]]:
        for iteration in reversed(trace.iterations):
            context_validation = iteration.get("context_validation", {})
            if context_validation.get("valid") and isinstance(context_validation.get("normalized_output"), dict):
                return context_validation["normalized_output"]
            tool_result = iteration.get("tool_result", {})
            if isinstance(tool_result.get("output"), dict):
                return tool_result["output"]
        return None

    def _latest_context_warnings(self, trace: AgentLoopTrace) -> List[str]:
        for iteration in reversed(trace.iterations):
            context_validation = iteration.get("context_validation", {})
            if isinstance(context_validation, dict):
                warnings = context_validation.get("warnings", [])
                if isinstance(warnings, list):
                    return list(warnings)
        return []


def _expected_context_types(selected_tool: Optional[str]) -> List[str]:
    mapping = {
        "get_occupancy_context": ["occupancy"],
        "get_motion_context": ["motion"],
        "get_audio_context": ["audio"],
        "get_room_state": ["room_state"],
    }
    return mapping.get(selected_tool, [])
