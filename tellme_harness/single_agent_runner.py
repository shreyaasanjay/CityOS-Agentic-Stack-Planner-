"""Single-agent execution path for TeLLMe Harness V0."""

from __future__ import annotations

from .cityos_mock import MockCityOSClient
from .schemas import AgentPlan, AnswerPacket, ContextWindow, RouteDecision, ToolCallSpec


class SingleAgentRunner:
    def __init__(self, cityos_client: MockCityOSClient) -> None:
        self.cityos_client = cityos_client

    def run(self, context_window: ContextWindow, route_decision: RouteDecision) -> tuple[AgentPlan, AnswerPacket]:
        if route_decision.route != "single_agent" or not route_decision.selected_tool or not route_decision.selected_agent:
            raise ValueError("SingleAgentRunner requires a single_agent route with a selected tool and agent.")

        timestamp = None
        if route_decision.time_window:
            timestamp = route_decision.time_window.start

        tool_args = {
            "space_id": context_window.space_id,
            "timestamp": timestamp,
        }
        if route_decision.selected_tool == "cityos_context_lookup":
            tool_args["query"] = context_window.user_query
            context_object = self.cityos_client.cityos_context_lookup(**tool_args)
        else:
            tool_method = getattr(self.cityos_client, route_decision.selected_tool)
            context_object = tool_method(**tool_args)

        plan = self.build_plan(context_window, route_decision)
        return plan, self._build_answer(context_window, route_decision, plan, context_object)

    def build_plan(self, context_window: ContextWindow, route_decision: RouteDecision) -> AgentPlan:
        if route_decision.route != "single_agent" or not route_decision.selected_tool or not route_decision.selected_agent:
            raise ValueError("SingleAgentRunner requires a single_agent route with a selected tool and agent.")

        timestamp = None
        if route_decision.time_window:
            timestamp = route_decision.time_window.start

        tool_args = {
            "space_id": context_window.space_id,
            "timestamp": timestamp,
        }
        if route_decision.selected_tool == "cityos_context_lookup":
            tool_args["query"] = context_window.user_query

        return AgentPlan(
            query_id=context_window.query_id,
            selected_agent=route_decision.selected_agent,
            selected_tool=route_decision.selected_tool,
            tool_call=ToolCallSpec(tool_name=route_decision.selected_tool, arguments=tool_args),
            steps=[
                "Inspect the routed single-agent tool selection.",
                "Fetch one structured CityOS mock context object.",
                "Answer conservatively with evidence references and caveats.",
            ],
            escalation_possible=True,
        )

    def _build_answer(
        self,
        context_window: ContextWindow,
        route_decision: RouteDecision,
        plan: AgentPlan,
        context_object: dict,
    ) -> AnswerPacket:
        answer_text = self._render_answer(context_window.user_query, context_object)
        caveats = [
            "This answer is based on simulated CityOS context from local mock JSON fixtures.",
            "V0 uses one structured context lookup and may need TraceFix for more complex reasoning.",
        ]
        caveats.extend(route_decision.caveats)
        return AnswerPacket(
            query_id=context_window.query_id,
            status="answered",
            answer=answer_text,
            confidence=context_object["confidence"],
            basis=[
                f"Selected agent: {route_decision.selected_agent}",
                f"Selected tool: {route_decision.selected_tool}",
                f"Context object: {context_object['context_id']}",
            ],
            evidence_refs=context_object["evidence_refs"],
            privacy_scope=context_object["privacy_scope"],
            caveats=caveats,
            selected_agent=route_decision.selected_agent,
            route_decision=route_decision.model_dump(),
            agent_plan=plan.model_dump(),
            raw_outputs={"cityos_context": context_object},
        )

    def _render_answer(self, user_query: str, context_object: dict) -> str:
        lowered = user_query.lower()
        value = context_object["value"]
        space_id = context_object["space_id"]
        timestamp = context_object["timestamp"]

        if context_object["context_type"] == "occupancy":
            count = value.get("count")
            state = value.get("state", "unknown")
            if "how many" in lowered or "people" in lowered:
                return f"There appear to be {count} people in {space_id} right now."
            if "empty" in lowered:
                return f"At {timestamp}, {space_id} was reported as {state} with an occupancy count of {count}."
            return f"{space_id} was reported as {state} with an occupancy count of {count}."

        if context_object["context_type"] == "motion":
            detected = value.get("motion_detected")
            if detected:
                return f"Motion was detected in {space_id} at {value.get('observed_at', timestamp)}."
            return f"No motion was detected in {space_id} at {value.get('observed_at', timestamp)}."

        if context_object["context_type"] == "audio":
            return (
                f"The audio context around {value.get('observed_at', timestamp)} indicates a noise level of "
                f"{value.get('noise_level_db')} dB in {space_id}."
            )

        if context_object["context_type"] == "room_state":
            return (
                f"The latest room state for {space_id} is {value.get('summary_state')} with occupancy "
                f"{value.get('occupancy_state')} and motion {value.get('motion_state')}."
            )

        return f"Structured CityOS mock context is available for {space_id}, but the answer remains limited in V0."
