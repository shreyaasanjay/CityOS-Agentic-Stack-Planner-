"""Pluggable agent backends for the V0.5 single-agent loop."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .agent_protocol import AgentAction, AgentLoopTrace
from .llm_client import LLMClient
from .schemas import ContextWindow, ExecutionPlan, RouteDecision

ANSWER_CONTRACT = {
    "required_fields": ["answer", "confidence", "evidence_refs", "caveats"],
    "field_types": {
        "answer": "string",
        "confidence": "number",
        "evidence_refs": "array",
        "caveats": "array",
    },
}
TOOL_ARGUMENT_SCHEMA = {
    "get_occupancy_context": {"space_id": "string", "timestamp": "string|null"},
    "get_motion_context": {"space_id": "string", "timestamp": "string|null"},
    "get_audio_context": {"space_id": "string", "timestamp": "string|null"},
    "get_room_state": {"space_id": "string", "timestamp": "string|null"},
    "cityos_context_lookup": {
        "space_id": "string",
        "timestamp": "string|null",
        "query": "string",
    },
}


class AgentBackend:
    mode = "deterministic"
    uses_llm = False

    def reset(self) -> None:
        return None

    def initial_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
    ) -> AgentAction:
        raise NotImplementedError

    def next_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
        loop_trace: AgentLoopTrace,
    ) -> AgentAction:
        raise NotImplementedError

    def get_prompt_log(self) -> List[str]:
        return []

    def get_action_log(self) -> List[Dict[str, Any]]:
        return []


class DeterministicAgentBackend(AgentBackend):
    mode = "deterministic"
    uses_llm = False

    def initial_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
    ) -> AgentAction:
        allowed_tools = list(route_decision.required_tools or execution_plan.allowed_tools)
        if not allowed_tools and route_decision.selected_tool:
            allowed_tools = [route_decision.selected_tool]
        tool_name = allowed_tools[0] if allowed_tools else "cityos_context_lookup"
        arguments = {
            "space_id": context_window.space_id,
            "timestamp": route_decision.time_window.start if route_decision.time_window else None,
        }
        if tool_name == "cityos_context_lookup":
            arguments["query"] = context_window.user_query
        return AgentAction(
            action_type="tool_request",
            tool_name=tool_name,
            arguments=arguments,
        )

    def next_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
        loop_trace: AgentLoopTrace,
    ) -> AgentAction:
        validated_context = _latest_validated_context(loop_trace)
        if not validated_context:
            return AgentAction(
                action_type="escalate_to_tracefix",
                escalation_reason="Validated context was not available for final answer generation.",
            )

        return AgentAction(
            action_type="final_answer",
            answer=_render_answer(context_window.user_query, validated_context),
            confidence=validated_context.get("confidence"),
            evidence_refs=list(validated_context.get("evidence_refs", [])),
            caveats=[],
            escalation_reason=None,
        )


class LLMAgentBackend(AgentBackend):
    uses_llm = True

    def __init__(self, llm_client: LLMClient, mode: str = "llm") -> None:
        self.llm_client = llm_client
        self.mode = mode
        self._prompt_log: List[str] = []
        self._action_log: List[Dict[str, Any]] = []

    def reset(self) -> None:
        self._prompt_log = []
        self._action_log = []

    def get_prompt_log(self) -> List[str]:
        return list(self._prompt_log)

    def get_action_log(self) -> List[Dict[str, Any]]:
        return list(self._action_log)

    def initial_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
    ) -> AgentAction:
        prompt = self._build_prompt(
            context_window=context_window,
            route_decision=route_decision,
            execution_plan=execution_plan,
            loop_trace=None,
            phase="initial_action",
        )
        return self._complete_action(prompt, "initial_action")

    def next_action(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
        loop_trace: AgentLoopTrace,
    ) -> AgentAction:
        prompt = self._build_prompt(
            context_window=context_window,
            route_decision=route_decision,
            execution_plan=execution_plan,
            loop_trace=loop_trace,
            phase="next_action",
        )
        return self._complete_action(prompt, "next_action")

    def _complete_action(self, prompt: str, phase: str) -> AgentAction:
        self._prompt_log.append(prompt)
        payload = self.llm_client.complete_json(prompt)
        self._action_log.append({"phase": phase, "prompt_index": len(self._prompt_log) - 1, "raw_action": payload})
        if not isinstance(payload, dict):
            raise ValueError("LLM response must be a JSON object.")
        try:
            action = AgentAction(**payload)
        except ValidationError as exc:
            raise ValueError("LLM response did not match AgentAction schema.") from exc
        errors = validate_agent_action(action)
        if errors:
            raise ValueError("LLM response failed AgentAction validation: " + "; ".join(errors))
        return action

    def _build_prompt(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        execution_plan: ExecutionPlan,
        loop_trace: Optional[AgentLoopTrace],
        phase: str,
    ) -> str:
        allowed_tools = list(route_decision.required_tools or execution_plan.allowed_tools)
        if not allowed_tools and route_decision.selected_tool:
            allowed_tools = [route_decision.selected_tool]
        validated_context = _latest_validated_context(loop_trace) if loop_trace else None
        state_payload = {
            "phase": phase,
            "query_id": context_window.query_id,
            "user_query": context_window.user_query,
            "space_id": context_window.space_id,
            "timestamp": route_decision.time_window.start if route_decision.time_window else None,
            "time_window": route_decision.time_window.model_dump() if route_decision.time_window else None,
            "time_windows": (
                [route_decision.time_window.model_dump()] if route_decision.time_window else []
            ),
            "available_modalities": list(getattr(execution_plan, "required_modalities", []) or []),
            "selected_agent": route_decision.selected_agent,
            "allowed_tools": allowed_tools,
            "validated_context": validated_context,
            "loop_state": _loop_state_summary(loop_trace),
        }
        return "\n".join(
            [
                "You are a bounded TeLLMe single-agent backend.",
                "You may only propose one AgentAction at a time.",
                "You must never execute tools, call arbitrary code, or bypass the orchestrator.",
                "The deterministic RoutePolicy already selected the simple-query path and remains final authority.",
                "Allowed tools only: " + ", ".join(allowed_tools or ["cityos_context_lookup"]),
                "Tool argument schema:",
                json.dumps({name: TOOL_ARGUMENT_SCHEMA[name] for name in (allowed_tools or ["cityos_context_lookup"])}, indent=2, sort_keys=True),
                "CityOS privacy rules:",
                "- Use only CityOS structured context.",
                "- Do not request raw sensor data.",
                "- Do not mention raw video, raw audio, or full recordings in the answer.",
                "Answer contract:",
                json.dumps(ANSWER_CONTRACT, indent=2, sort_keys=True),
                "Escalation rules:",
                "- Escalate if the query cannot be answered from one structured context lookup.",
                "- Escalate if validated context is missing, contradictory, or insufficient.",
                "- Return only JSON matching AgentAction.",
                "Examples:",
                json.dumps(
                    {
                        "action_type": "tool_request",
                        "tool_name": "get_occupancy_context",
                        "arguments": {"space_id": "smart_room_1", "timestamp": "11:30"},
                        "answer": None,
                        "confidence": None,
                        "evidence_refs": [],
                        "caveats": [],
                        "escalation_reason": None,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "action_type": "final_answer",
                        "tool_name": None,
                        "arguments": {},
                        "answer": "There appear to be 3 people in smart_room_1.",
                        "confidence": 0.91,
                        "evidence_refs": ["mock://..."],
                        "caveats": ["Based on CityOS structured context."],
                        "escalation_reason": None,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "action_type": "escalate_to_tracefix",
                        "tool_name": None,
                        "arguments": {},
                        "answer": None,
                        "confidence": None,
                        "evidence_refs": [],
                        "caveats": [],
                        "escalation_reason": "The query requires multi-modal reconciliation.",
                    },
                    indent=2,
                    sort_keys=True,
                ),
                "STATE_JSON:",
                json.dumps(state_payload, indent=2, sort_keys=True),
            ]
        )


def validate_agent_action(action: AgentAction) -> List[str]:
    errors: List[str] = []
    if action.action_type == "tool_request":
        if not action.tool_name:
            errors.append("tool_request actions require tool_name.")
        if not isinstance(action.arguments, dict):
            errors.append("tool_request actions require arguments to be a dict.")
    elif action.action_type == "final_answer":
        if not action.answer or not action.answer.strip():
            errors.append("final_answer actions require a non-empty answer.")
        if action.confidence is not None and (
            not isinstance(action.confidence, (int, float)) or action.confidence < 0 or action.confidence > 1
        ):
            errors.append("final_answer confidence must be between 0 and 1 when provided.")
        if not isinstance(action.evidence_refs, list):
            errors.append("final_answer evidence_refs must be a list.")
        if not isinstance(action.caveats, list):
            errors.append("final_answer caveats must be a list.")
    elif action.action_type == "escalate_to_tracefix":
        if not action.escalation_reason:
            errors.append("escalate_to_tracefix actions require escalation_reason.")
    return errors


def _latest_validated_context(loop_trace: Optional[AgentLoopTrace]) -> Optional[Dict[str, Any]]:
    if loop_trace is None:
        return None
    for iteration in reversed(loop_trace.iterations):
        context_validation = iteration.get("context_validation")
        if isinstance(context_validation, dict) and context_validation.get("valid"):
            normalized_output = context_validation.get("normalized_output")
            if isinstance(normalized_output, dict):
                return normalized_output
        tool_result = iteration.get("tool_result")
        if isinstance(tool_result, dict) and isinstance(tool_result.get("output"), dict):
            return tool_result.get("output")
    return None


def _loop_state_summary(loop_trace: Optional[AgentLoopTrace]) -> Dict[str, Any]:
    if loop_trace is None:
        return {"iterations": 0, "tool_calls": 0, "escalated": False}
    return {
        "iterations": len(loop_trace.iterations),
        "tool_calls": len(loop_trace.tool_results),
        "escalated": loop_trace.escalated,
        "escalation_reason": loop_trace.escalation_reason,
    }


def _render_answer(user_query: str, context_object: Dict[str, Any]) -> str:
    lowered = user_query.lower()
    value = context_object["value"]
    space_id = context_object["space_id"]
    timestamp = context_object["timestamp"]

    if context_object["context_type"] == "occupancy":
        count = value.get("count")
        state = value.get("state", "unknown")
        if "how many" in lowered or "people" in lowered:
            return "There appear to be {count} people in {space_id}.".format(count=count, space_id=space_id)
        if "empty" in lowered:
            return "At {timestamp}, {space_id} was reported as {state} with an occupancy count of {count}.".format(
                timestamp=timestamp,
                space_id=space_id,
                state=state,
                count=count,
            )
        return "{space_id} was reported as {state} with an occupancy count of {count}.".format(
            space_id=space_id,
            state=state,
            count=count,
        )

    if context_object["context_type"] == "motion":
        detected = value.get("motion_detected")
        observed_at = value.get("observed_at", timestamp)
        if detected:
            return "Motion was detected in {space_id} at {observed_at}.".format(
                space_id=space_id,
                observed_at=observed_at,
            )
        return "No motion was detected in {space_id} at {observed_at}.".format(
            space_id=space_id,
            observed_at=observed_at,
        )

    if context_object["context_type"] == "audio":
        return (
            "The audio context around {observed_at} indicates a noise level of {noise_level_db} dB in {space_id}."
        ).format(
            observed_at=value.get("observed_at", timestamp),
            noise_level_db=value.get("noise_level_db"),
            space_id=space_id,
        )

    if context_object["context_type"] == "room_state":
        return (
            "The latest room state for {space_id} is {summary_state} with occupancy {occupancy_state} "
            "and motion {motion_state}."
        ).format(
            space_id=space_id,
            summary_state=value.get("summary_state"),
            occupancy_state=value.get("occupancy_state"),
            motion_state=value.get("motion_state"),
        )

    summary = value.get("summary") or value.get("notes")
    if summary:
        return "Structured context for {space_id}: {summary}".format(space_id=space_id, summary=summary)
    return "Structured CityOS mock context is available for {space_id}, but the answer remains limited in V0.".format(
        space_id=space_id
    )
