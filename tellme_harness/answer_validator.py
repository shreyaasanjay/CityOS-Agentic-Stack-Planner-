"""Validation for returned AnswerPacket objects."""

from __future__ import annotations

from typing import List

from .agent_protocol import ValidationResult
from .schemas import AnswerPacket


def validate_answer_packet(packet: AnswerPacket) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    valid_statuses = {"answered", "needs_tracefix", "needs_clarification", "not_answerable", "error"}
    if packet.status not in valid_statuses:
        errors.append("status is invalid.")

    if packet.status == "answered":
        if not packet.answer.strip():
            errors.append("answered packets require a non-empty answer.")
        if not isinstance(packet.confidence, (int, float)) or packet.confidence < 0 or packet.confidence > 1:
            errors.append("answered packets require confidence between 0 and 1.")
        elif packet.confidence < 0.6:
            warnings.append("confidence is below 0.6.")
        if not isinstance(packet.evidence_refs, list):
            errors.append("answered packets require evidence_refs to be a list.")
        elif not packet.evidence_refs:
            warnings.append("evidence_refs is empty.")
        if not isinstance(packet.caveats, list):
            errors.append("answered packets require caveats to be a list.")
        if not packet.route_decision:
            errors.append("answered packets require route_decision.")
        lowered = packet.answer.lower()
        if any(token in lowered for token in ("raw video", "raw audio", "raw sensor feed", "full recording")):
            errors.append("answered packets must not expose raw sensor content.")

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        normalized_output=packet.model_dump() if not errors else None,
    )
