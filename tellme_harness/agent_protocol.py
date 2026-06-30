"""Schemas for the V0.4 deterministic single-agent loop."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    action_type: Literal["tool_request", "final_answer", "escalate_to_tracefix"]
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    answer: Optional[str] = None
    confidence: Optional[float] = None
    evidence_refs: List[str] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None


class ToolExecutionResult(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    success: bool
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ValidationResult(BaseModel):
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    normalized_output: Optional[Dict[str, Any]] = None


class AgentLoopTrace(BaseModel):
    query_id: str
    iterations: List[Dict[str, Any]] = Field(default_factory=list)
    final_action: Optional[AgentAction] = None
    tool_results: List[ToolExecutionResult] = Field(default_factory=list)
    validation_results: List[ValidationResult] = Field(default_factory=list)
    escalated: bool = False
    escalation_reason: Optional[str] = None
