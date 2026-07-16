"""Prompt construction for downstream procedure selection.

The prompt is intentionally downstream of deterministic extraction, ranking,
and validation.  It does not calculate scores or compare templates.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from tracefix.runtime.deterministic_template_engine import (
    ProcedureOptions,
    TemplateRanking,
    TemplateValidationResult,
)
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData


PROCEDURE_DEFINITIONS = {
    "exact_reuse": (
        "Use the existing template unchanged when all known structural "
        "attributes match and no adaptation is required."
    ),
    "parameterized_reuse": (
        "Use the existing template structure while changing only fields "
        "explicitly declared parameterizable."
    ),
    "partial_recomposition": (
        "Reuse compatible portions of the closest template and apply only "
        "bounded, later-validated adaptations."
    ),
    "full_generation": (
        "Choose this when no template can be safely reused or adapted. Do not "
        "generate the actual protocol in this step."
    ),
}


def build_procedure_selection_prompt(
    *,
    query: str,
    extracted_data: ExtractedCoordinationData,
    rankings: Sequence[TemplateRanking],
    validation: TemplateValidationResult | None,
    procedure_options: ProcedureOptions,
) -> str:
    payload = {
        "original_user_query": query,
        "extracted_coordination_data": extracted_data.as_dict(),
        "template_rankings": [ranking.to_dict() for ranking in rankings],
        "top_candidate_validation": validation.to_dict() if validation else None,
        "procedure_options": procedure_options.to_dict(),
    }
    definitions = "\n".join(
        f"- {name}: {definition}"
        for name, definition in PROCEDURE_DEFINITIONS.items()
    )
    return (
        "You are selecting a procedure for a direct data-server TraceFix runtime.\n"
        "Attributes were already extracted by a separate LLM step.\n"
        "Templates were already ranked deterministically.\n"
        "The top candidate was already validated deterministically.\n\n"
        "You must choose exactly one procedure from these four options:\n"
        f"{definitions}\n\n"
        "Rules:\n"
        "- Return only valid JSON.\n"
        "- Do not alter scores or comparison results.\n"
        "- Do not recalculate rankings.\n"
        "- Do not override deterministic mismatches.\n"
        "- Do not invent a template.\n"
        "- Do not add a fifth procedure.\n"
        "- Do not generate a protocol in this step.\n"
        "- Only choose an option where deterministically_available is true.\n"
        "- Explain your choice using only supplied deterministic evidence.\n"
        "- Do not include confidence, route, ranking, or extra fields.\n\n"
        "Return exactly this JSON shape:\n"
        "{\n"
        '  "selected_procedure": "exact_reuse | parameterized_reuse | partial_recomposition | full_generation",\n'
        '  "selected_template_id": "template id or null",\n'
        '  "reasoning": "short explanation",\n'
        '  "evidence_used": ["evidence string"]\n'
        "}\n\n"
        "DETERMINISTIC_EVIDENCE_JSON:\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )
