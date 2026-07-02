"""Safe mock CityOS tool execution for the V0.4 single-agent loop."""

from __future__ import annotations

from typing import Any, Dict

from .cityos_mock import MockCityOSClient
from .agent_protocol import ToolExecutionResult


class ToolExecutor:
    """Execute only the fixed mock CityOS tool surface."""

    def __init__(self, cityos_client: MockCityOSClient) -> None:
        self.cityos_client = cityos_client
        self.allowed_tools = {
            "get_occupancy_context": self.cityos_client.get_occupancy_context,
            "get_motion_context": self.cityos_client.get_motion_context,
            "get_audio_context": self.cityos_client.get_audio_context,
            "get_room_state": self.cityos_client.get_room_state,
            "cityos_context_lookup": self.cityos_client.cityos_context_lookup,
        }

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolExecutionResult:
        if tool_name not in self.allowed_tools:
            return ToolExecutionResult(
                tool_name=tool_name,
                arguments=dict(arguments),
                success=False,
                output={},
                error="Unknown or disallowed tool requested.",
            )

        try:
            output = self.allowed_tools[tool_name](**arguments)
            if not isinstance(output, dict):
                return ToolExecutionResult(
                    tool_name=tool_name,
                    arguments=dict(arguments),
                    success=False,
                    output={},
                    error="Tool returned a non-dict output.",
                )
            return ToolExecutionResult(
                tool_name=tool_name,
                arguments=dict(arguments),
                success=True,
                output=output,
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=tool_name,
                arguments=dict(arguments),
                success=False,
                output={},
                error=str(exc),
            )
