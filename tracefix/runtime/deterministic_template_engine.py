"""Deterministic template ranking and validation for coordination attributes.

This module does not call an LLM, generate IR, run OpenCode, synthesize CityOS
apps, or authorize protocol execution.  It compares a validated extractor
artifact against data-only Template objects.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tracefix.protocol_templates.template import Template
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData

PRIMARY_ATTRIBUTES: tuple[str, ...] = (
    "coordination_patterns",
    "number_of_agents",
    "agent_roles",
    "communication_flow",
    "number_of_resources",
    "number_of_channels",
)

ProcedureName = Literal[
    "exact_reuse",
    "parameterized_reuse",
    "partial_recomposition",
    "full_generation",
]


@dataclass(frozen=True)
class AttributeComparison:
    attribute_name: str
    extracted_value: object
    template_value: object
    result: bool | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TemplateRanking:
    template_id: str
    name_of_template: str
    match_count: int
    mismatch_count: int
    unknown_count: int
    compared_count: int
    score: float
    comparisons: tuple[AttributeComparison, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "name_of_template": self.name_of_template,
            "match_count": self.match_count,
            "mismatch_count": self.mismatch_count,
            "unknown_count": self.unknown_count,
            "compared_count": self.compared_count,
            "score": self.score,
            "comparisons": [comparison.to_dict() for comparison in self.comparisons],
        }


@dataclass(frozen=True)
class TemplateValidationResult:
    template_id: str
    valid: bool
    matched_fields: tuple[str, ...]
    mismatched_fields: tuple[str, ...]
    unknown_fields: tuple[str, ...]
    comparisons: tuple[AttributeComparison, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "valid": self.valid,
            "matched_fields": list(self.matched_fields),
            "mismatched_fields": list(self.mismatched_fields),
            "unknown_fields": list(self.unknown_fields),
            "comparisons": [comparison.to_dict() for comparison in self.comparisons],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ProcedureOption:
    procedure: ProcedureName
    deterministically_available: bool
    supporting_reasons: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    candidate_template_id: str | None
    candidate_template_name: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "procedure": self.procedure,
            "deterministically_available": self.deterministically_available,
            "supporting_reasons": list(self.supporting_reasons),
            "blocking_reasons": list(self.blocking_reasons),
            "candidate_template_id": self.candidate_template_id,
            "candidate_template_name": self.candidate_template_name,
        }


@dataclass(frozen=True)
class ProcedureOptions:
    top_candidate_template_id: str | None
    top_candidate_template_name: str | None
    options: tuple[ProcedureOption, ...]
    ranking_summary: dict[str, object]
    validation_summary: dict[str, object]

    def option_for(self, procedure: ProcedureName) -> ProcedureOption | None:
        for option in self.options:
            if option.procedure == procedure:
                return option
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "top_candidate_template_id": self.top_candidate_template_id,
            "top_candidate_template_name": self.top_candidate_template_name,
            "options": [option.to_dict() for option in self.options],
            "ranking_summary": dict(self.ranking_summary),
            "validation_summary": dict(self.validation_summary),
        }


class DeterministicTemplateEngine:
    """Rank and validate templates using shared deterministic comparisons."""

    def rank(
        self,
        extracted: ExtractedCoordinationData,
        templates: Sequence[Template],
    ) -> list[TemplateRanking]:
        rankings = [self._ranking_for(extracted, template) for template in templates]
        return sorted(
            rankings,
            key=lambda ranking: (
                -ranking.score,
                -ranking.match_count,
                ranking.mismatch_count,
                -ranking.compared_count,
                ranking.template_id,
            ),
        )

    def validate(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> TemplateValidationResult:
        comparisons = self.compare_for_validation(extracted, template)
        matched = tuple(comparison.attribute_name for comparison in comparisons if comparison.result is True)
        mismatched = tuple(comparison.attribute_name for comparison in comparisons if comparison.result is False)
        unknown = tuple(comparison.attribute_name for comparison in comparisons if comparison.result is None)
        structural = [comparison for comparison in comparisons if comparison.attribute_name in PRIMARY_ATTRIBUTES]
        structural_compared = sum(1 for comparison in structural if comparison.result is not None)
        valid = structural_compared > 0 and not mismatched
        reasons: list[str] = [comparison.reason for comparison in comparisons]
        if structural_compared == 0:
            reasons.append("Candidate invalid: zero comparable structural attributes.")
        if mismatched:
            reasons.append(
                "Candidate invalid: known attribute mismatches: "
                + ", ".join(mismatched)
                + "."
            )
        return TemplateValidationResult(
            template_id=template.get_template_id(),
            valid=valid,
            matched_fields=matched,
            mismatched_fields=mismatched,
            unknown_fields=unknown,
            comparisons=comparisons,
            reasons=tuple(reasons),
        )

    def procedure_options(
        self,
        rankings: Sequence[TemplateRanking],
        validation: TemplateValidationResult | None,
        templates: Sequence[Template],
    ) -> ProcedureOptions:
        top = rankings[0] if rankings else None
        template = _find_template(templates, top.template_id) if top else None
        options = _build_procedure_options(top, validation, template)
        return ProcedureOptions(
            top_candidate_template_id=top.template_id if top else None,
            top_candidate_template_name=top.name_of_template if top else None,
            options=options,
            ranking_summary={
                "ranking_order": [ranking.template_id for ranking in rankings],
                "top_score": top.score if top else None,
                "top_match_count": top.match_count if top else None,
                "top_mismatch_count": top.mismatch_count if top else None,
                "top_unknown_count": top.unknown_count if top else None,
                "template_count": len(rankings),
            },
            validation_summary=validation.to_dict() if validation else {
                "valid": False,
                "reason": "No candidate validation was available.",
            },
        )

    def compare(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> tuple[AttributeComparison, ...]:
        return (
            _compare_patterns(
                "coordination_patterns",
                extracted.coordination_patterns,
                template.get_coordination_patterns(),
            ),
            _compare_exact_int(
                "number_of_agents",
                extracted.number_of_agents,
                template.get_number_of_agents(),
            ),
            _compare_subset_list(
                "agent_roles",
                extracted.agent_roles,
                template.get_agent_roles(),
            ),
            _compare_ordered_list(
                "communication_flow",
                extracted.communication_flow,
                template.get_communication_flow(),
            ),
            _compare_exact_int(
                "number_of_resources",
                extracted.number_of_resources,
                template.get_number_of_resources(),
            ),
            _compare_exact_int(
                "number_of_channels",
                extracted.number_of_channels,
                template.get_number_of_channels(),
            ),
        )

    def compare_for_validation(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> tuple[AttributeComparison, ...]:
        return self.compare(extracted, template) + (
            _compare_subset_list(
                "limitations",
                extracted.limitations,
                template.get_limitations(),
            ),
        )

    def _ranking_for(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> TemplateRanking:
        comparisons = self.compare(extracted, template)
        primary = [comparison for comparison in comparisons if comparison.attribute_name in PRIMARY_ATTRIBUTES]
        match_count = sum(1 for comparison in primary if comparison.result is True)
        mismatch_count = sum(1 for comparison in primary if comparison.result is False)
        unknown_count = sum(1 for comparison in primary if comparison.result is None)
        compared_count = match_count + mismatch_count
        score = match_count / compared_count if compared_count > 0 else 0.0
        return TemplateRanking(
            template_id=template.get_template_id(),
            name_of_template=template.get_name_of_template(),
            match_count=match_count,
            mismatch_count=mismatch_count,
            unknown_count=unknown_count,
            compared_count=compared_count,
            score=round(score, 4),
            comparisons=comparisons,
        )


def rank_templates_for_attributes(
    extracted: ExtractedCoordinationData,
    templates: Sequence[Template],
) -> list[TemplateRanking]:
    return DeterministicTemplateEngine().rank(extracted, templates)


def validate_template_for_attributes(
    extracted: ExtractedCoordinationData,
    template: Template,
) -> TemplateValidationResult:
    return DeterministicTemplateEngine().validate(extracted, template)


def derive_procedure_options(
    rankings: Sequence[TemplateRanking],
    validation: TemplateValidationResult | None,
    templates: Sequence[Template],
) -> ProcedureOptions:
    return DeterministicTemplateEngine().procedure_options(rankings, validation, templates)


def _compare_patterns(
    attribute_name: str,
    extracted: Sequence[str],
    template_values: Sequence[str],
) -> AttributeComparison:
    extracted_values = tuple(extracted)
    template = tuple(template_values)
    if not extracted_values or not template:
        return _unknown(attribute_name, extracted_values, template, "One side has no coordination patterns.")
    extracted_set = set(extracted_values)
    template_set = set(template)
    missing = sorted(extracted_set - template_set)
    if missing:
        return _mismatch(attribute_name, extracted_values, template, f"Missing template patterns: {missing}.")
    return _match(attribute_name, extracted_values, template, "Every extracted coordination pattern is present.")


def _compare_exact_int(
    attribute_name: str,
    extracted: int | None,
    template_value: int | None,
) -> AttributeComparison:
    if extracted is None or template_value is None:
        return _unknown(attribute_name, extracted, template_value, "One side has no numeric value.")
    if extracted == template_value:
        return _match(attribute_name, extracted, template_value, f"Exact numeric match: {extracted}.")
    return _mismatch(attribute_name, extracted, template_value, f"Numeric mismatch: {extracted} != {template_value}.")


def _compare_subset_list(
    attribute_name: str,
    extracted: Sequence[str],
    template_values: Sequence[str],
) -> AttributeComparison:
    extracted_norm = tuple(_normalize_term(value) for value in extracted if _normalize_term(value))
    template_norm = tuple(_normalize_term(value) for value in template_values if _normalize_term(value))
    if not extracted_norm or not template_norm:
        return _unknown(attribute_name, extracted_norm, template_norm, "One side has no comparable values.")
    template_set = set(template_norm)
    missing = [value for value in extracted_norm if value not in template_set]
    if missing:
        return _mismatch(attribute_name, extracted_norm, template_norm, f"Missing template values: {missing}.")
    return _match(attribute_name, extracted_norm, template_norm, "Every extracted value is present.")


def _compare_ordered_list(
    attribute_name: str,
    extracted: Sequence[str],
    template_values: Sequence[str],
) -> AttributeComparison:
    extracted_norm = tuple(_normalize_term(value) for value in extracted if _normalize_term(value))
    template_norm = tuple(_normalize_term(value) for value in template_values if _normalize_term(value))
    if not extracted_norm or not template_norm:
        return _unknown(attribute_name, extracted_norm, template_norm, "One side has no comparable ordered values.")
    if extracted_norm == template_norm:
        return _match(attribute_name, extracted_norm, template_norm, "Ordered values match exactly.")
    return _mismatch(attribute_name, extracted_norm, template_norm, "Ordered values differ.")


def _build_procedure_options(
    top: TemplateRanking | None,
    validation: TemplateValidationResult | None,
    template: Template | None,
) -> tuple[ProcedureOption, ...]:
    if top is None or template is None:
        no_candidate = "No ranked candidate template exists."
        return (
            _option("exact_reuse", False, (), (no_candidate,), None, None),
            _option("parameterized_reuse", False, (), (no_candidate,), None, None),
            _option("partial_recomposition", False, (), (no_candidate,), None, None),
            _option(
                "full_generation",
                True,
                ("No stored template is available for safe reuse.",),
                (),
                None,
                None,
            ),
        )

    candidate_id = top.template_id
    candidate_name = top.name_of_template
    mismatched = set(validation.mismatched_fields if validation else ())
    matched = set(validation.matched_fields if validation else ())
    fatal_fields = set(template.get_fatal_mismatch_fields())
    parameterizable = set(template.get_parameterizable_fields())
    adaptable = set(template.get_adaptable_fields())
    fatal_mismatches = sorted(mismatched.intersection(fatal_fields))
    compared = top.compared_count

    exact_available = bool(validation and validation.valid and top.mismatch_count == 0 and compared > 0)
    exact_blockers: list[str] = []
    exact_support: list[str] = []
    if exact_available:
        exact_support.append("Top candidate validation passed with zero known structural mismatches.")
    else:
        if validation is None:
            exact_blockers.append("No validation result is available.")
        if compared == 0:
            exact_blockers.append("No comparable structural attributes were available.")
        if top.mismatch_count:
            exact_blockers.append(f"Known attribute mismatches exist: {sorted(mismatched)}.")
        if validation is not None and not validation.valid:
            exact_blockers.append("Candidate validation did not pass.")

    parameterized_available = (
        bool(top and validation)
        and not exact_available
        and compared > 0
        and bool(mismatched)
        and not fatal_mismatches
        and mismatched.issubset(parameterizable)
    )
    parameterized_support: list[str] = []
    parameterized_blockers: list[str] = []
    if parameterized_available:
        parameterized_support.append(f"Mismatches are declared parameterizable: {sorted(mismatched)}.")
        parameterized_support.append("No fatal structural mismatch was reported.")
    else:
        if exact_available:
            parameterized_blockers.append("Exact reuse is already sufficient; no parameter change is required.")
        if not mismatched:
            parameterized_blockers.append("No parameter mismatch was detected.")
        if fatal_mismatches:
            parameterized_blockers.append(f"Fatal mismatches block parameterization: {fatal_mismatches}.")
        missing_parameter_metadata = sorted(mismatched - parameterizable)
        if missing_parameter_metadata:
            parameterized_blockers.append(
                f"Mismatches are not declared parameterizable: {missing_parameter_metadata}."
            )

    meaningful_matches = matched.intersection({
        "coordination_patterns",
        "agent_roles",
        "communication_flow",
        "number_of_resources",
    })
    partial_available = (
        not exact_available
        and not parameterized_available
        and bool(meaningful_matches)
        and not fatal_mismatches
        and bool(mismatched)
        and mismatched.issubset(adaptable)
    )
    partial_support: list[str] = []
    partial_blockers: list[str] = []
    if partial_available:
        partial_support.append(f"Reusable structural evidence exists: {sorted(meaningful_matches)}.")
        partial_support.append(f"Template declares bounded adaptable fields: {sorted(adaptable)}.")
    else:
        if exact_available or parameterized_available:
            partial_blockers.append("A safer reuse procedure is already available.")
        if not meaningful_matches:
            partial_blockers.append("No meaningful reusable structural field matched.")
        if fatal_mismatches:
            partial_blockers.append(f"Fatal mismatches block bounded adaptation: {fatal_mismatches}.")
        if not adaptable:
            partial_blockers.append("Template does not declare bounded adaptable fields.")
        missing_adaptation_metadata = sorted(mismatched - adaptable)
        if missing_adaptation_metadata:
            partial_blockers.append(
                f"Mismatches are not declared adaptable: {missing_adaptation_metadata}."
            )

    reuse_available = exact_available or parameterized_available or partial_available
    full_support = (
        ("Available as fallback if the downstream LLM rejects the reusable options.",)
        if reuse_available
        else ("No safe reuse procedure is currently available.",)
    )
    full_blockers = (
        ("A reusable procedure is deterministically available and should be preferred when safe.",)
        if reuse_available
        else ()
    )

    return (
        _option("exact_reuse", exact_available, exact_support, exact_blockers, candidate_id, candidate_name),
        _option(
            "parameterized_reuse",
            parameterized_available,
            parameterized_support,
            parameterized_blockers,
            candidate_id,
            candidate_name,
        ),
        _option(
            "partial_recomposition",
            partial_available,
            partial_support,
            partial_blockers,
            candidate_id,
            candidate_name,
        ),
        _option("full_generation", True, full_support, full_blockers, None, None),
    )


def _option(
    procedure: ProcedureName,
    available: bool,
    supporting: Sequence[str],
    blocking: Sequence[str],
    candidate_id: str | None,
    candidate_name: str | None,
) -> ProcedureOption:
    return ProcedureOption(
        procedure=procedure,
        deterministically_available=available,
        supporting_reasons=tuple(supporting),
        blocking_reasons=tuple(blocking),
        candidate_template_id=candidate_id,
        candidate_template_name=candidate_name,
    )


def _find_template(templates: Sequence[Template], template_id: str) -> Template | None:
    for template in templates:
        if template.get_template_id() == template_id:
            return template
    return None


def _match(attribute_name: str, extracted: object, template: object, reason: str) -> AttributeComparison:
    return AttributeComparison(attribute_name, extracted, template, True, reason)


def _mismatch(attribute_name: str, extracted: object, template: object, reason: str) -> AttributeComparison:
    return AttributeComparison(attribute_name, extracted, template, False, reason)


def _unknown(attribute_name: str, extracted: object, template: object, reason: str) -> AttributeComparison:
    return AttributeComparison(attribute_name, extracted, template, None, reason)


def _normalize_term(value: Any) -> str:
    return "_".join(
        str(value)
        .strip()
        .lower()
        .replace("-", " ")
        .split()
    )
