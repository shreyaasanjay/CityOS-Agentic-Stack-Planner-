import pytest

from tracefix.protocol_templates.template import Template
from tracefix.runtime.deterministic_template_engine import DeterministicTemplateEngine
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData
from tracefix.runtime.procedure_decision import (
    ProcedureDecisionError,
    validate_procedure_decision_response,
)


def _options_for_exact():
    extracted = ExtractedCoordinationData.from_payload({
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 2,
        "agent_roles": ["requester"],
        "communication_flow": ["request", "grant"],
        "limitations": ["exclusive_access"],
        "number_of_resources": 1,
        "number_of_channels": 1,
    })
    template = Template(
        "shared_resource",
        "Shared Resource",
        ["Request-Grant"],
        2,
        ["requester", "scheduler"],
        ["request", "grant"],
        ["exclusive_access"],
        1,
        1,
    )
    engine = DeterministicTemplateEngine()
    rankings = engine.rank(extracted, [template])
    return engine.procedure_options(rankings, engine.validate(extracted, template), [template])


def _options_for_parameterized():
    extracted = ExtractedCoordinationData.from_payload({
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 3,
        "agent_roles": ["requester"],
        "communication_flow": ["request", "grant"],
        "limitations": ["exclusive_access"],
        "number_of_resources": 1,
        "number_of_channels": 1,
    })
    template = Template(
        "shared_resource",
        "Shared Resource",
        ["Request-Grant"],
        2,
        ["requester", "scheduler"],
        ["request", "grant"],
        ["exclusive_access"],
        1,
        1,
        parameterizable_fields=["number_of_agents"],
    )
    engine = DeterministicTemplateEngine()
    rankings = engine.rank(extracted, [template])
    return engine.procedure_options(rankings, engine.validate(extracted, template), [template])


def _options_for_partial():
    extracted = ExtractedCoordinationData.from_payload({
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 2,
        "agent_roles": ["requester"],
        "communication_flow": ["request", "grant", "release"],
        "limitations": ["exclusive_access"],
        "number_of_resources": 1,
        "number_of_channels": 1,
    })
    template = Template(
        "shared_resource",
        "Shared Resource",
        ["Request-Grant"],
        2,
        ["requester", "scheduler"],
        ["request", "grant"],
        ["exclusive_access"],
        1,
        1,
        adaptable_fields=["communication_flow"],
    )
    engine = DeterministicTemplateEngine()
    rankings = engine.rank(extracted, [template])
    return engine.procedure_options(rankings, engine.validate(extracted, template), [template])


def test_valid_exact_parameterized_partial_and_full_generation_decisions_are_accepted():
    exact = validate_procedure_decision_response({
        "selected_procedure": "exact_reuse",
        "selected_template_id": "shared_resource",
        "reasoning": "Exact option is available.",
        "evidence_used": ["exact_reuse available"],
    }, _options_for_exact())
    parameterized = validate_procedure_decision_response({
        "selected_procedure": "parameterized_reuse",
        "selected_template_id": "shared_resource",
        "reasoning": "Only declared parameter fields differ.",
        "evidence_used": ["number_of_agents parameterizable"],
    }, _options_for_parameterized())
    partial = validate_procedure_decision_response({
        "selected_procedure": "partial_recomposition",
        "selected_template_id": "shared_resource",
        "reasoning": "Bounded adaptation is available.",
        "evidence_used": ["communication_flow adaptable"],
    }, _options_for_partial())
    full = validate_procedure_decision_response({
        "selected_procedure": "full_generation",
        "selected_template_id": None,
        "reasoning": "Fallback remains available.",
        "evidence_used": ["fallback"],
    }, _options_for_exact())

    assert exact.selected_procedure == "exact_reuse"
    assert parameterized.selected_procedure == "parameterized_reuse"
    assert partial.selected_procedure == "partial_recomposition"
    assert full.selected_procedure == "full_generation"


def test_unavailable_unknown_wrong_template_extra_fields_and_invalid_json_are_rejected():
    options = _options_for_exact()
    with pytest.raises(ProcedureDecisionError, match="not deterministically available"):
        validate_procedure_decision_response({
            "selected_procedure": "parameterized_reuse",
            "selected_template_id": "shared_resource",
            "reasoning": "bad",
            "evidence_used": [],
        }, options)
    with pytest.raises(ProcedureDecisionError, match="schema validation failed"):
        validate_procedure_decision_response({
            "selected_procedure": "not_real",
            "selected_template_id": None,
            "reasoning": "bad",
            "evidence_used": [],
        }, options)
    with pytest.raises(ProcedureDecisionError, match="selected_template_id"):
        validate_procedure_decision_response({
            "selected_procedure": "exact_reuse",
            "selected_template_id": "wrong",
            "reasoning": "bad",
            "evidence_used": [],
        }, options)
    with pytest.raises(ProcedureDecisionError, match="schema validation failed"):
        validate_procedure_decision_response({
            "selected_procedure": "full_generation",
            "selected_template_id": None,
            "reasoning": "bad",
            "evidence_used": [],
            "confidence": 0.9,
        }, options)
    with pytest.raises(ProcedureDecisionError, match="invalid procedure decision JSON"):
        validate_procedure_decision_response("{not json", options)
