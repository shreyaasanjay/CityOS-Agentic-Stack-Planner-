"""Strict validation for downstream procedure-selection LLM responses."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tracefix.runtime.deterministic_template_engine import ProcedureOptions

ProcedureName = Literal[
    "exact_reuse",
    "parameterized_reuse",
    "partial_recomposition",
    "full_generation",
]


class ProcedureDecisionError(RuntimeError):
    """Raised when procedure-selection output is unavailable or invalid."""


class ProcedureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_procedure: ProcedureName
    selected_template_id: str | None = None
    reasoning: str
    evidence_used: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("reasoning")
    @classmethod
    def _validate_reasoning(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("reasoning is required")
        return cleaned

    @field_validator("evidence_used", mode="before")
    @classmethod
    def _validate_evidence(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("evidence_used must be a list")
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("evidence_used entries must be strings")
            cleaned = item.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return tuple(result)

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_procedure": self.selected_procedure,
            "selected_template_id": self.selected_template_id,
            "reasoning": self.reasoning,
            "evidence_used": list(self.evidence_used),
        }


def validate_procedure_decision_response(
    raw: Any,
    procedure_options: ProcedureOptions,
) -> ProcedureDecision:
    payload = _coerce_payload(raw)
    try:
        decision = ProcedureDecision.model_validate(payload)
    except ValidationError as exc:
        raise ProcedureDecisionError("procedure decision schema validation failed: " + str(exc)) from exc

    option = procedure_options.option_for(decision.selected_procedure)
    if option is None:
        raise ProcedureDecisionError(f"unknown procedure option: {decision.selected_procedure}")
    if not option.deterministically_available:
        raise ProcedureDecisionError(
            f"selected procedure is not deterministically available: {decision.selected_procedure}"
        )

    expected_template_id = option.candidate_template_id
    if decision.selected_procedure == "full_generation":
        if decision.selected_template_id is not None:
            raise ProcedureDecisionError("full_generation must use selected_template_id=null")
    elif decision.selected_template_id != expected_template_id:
        raise ProcedureDecisionError(
            "selected_template_id does not match the selected procedure candidate: "
            f"expected {expected_template_id!r}, got {decision.selected_template_id!r}"
        )
    return decision


def _coerce_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, ProcedureDecision):
        return raw.to_dict()
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            raise ProcedureDecisionError("procedure decision response was empty")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProcedureDecisionError(f"invalid procedure decision JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProcedureDecisionError("procedure decision JSON must decode to an object")
        return payload
    raise ProcedureDecisionError(f"unsupported procedure decision response type: {type(raw).__name__}")
