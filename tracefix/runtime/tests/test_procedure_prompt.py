from tracefix.protocol_templates.template import Template
from tracefix.runtime.deterministic_template_engine import DeterministicTemplateEngine
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData
from tracefix.runtime.procedure_prompt import build_procedure_selection_prompt


def _fixtures():
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
    validation = engine.validate(extracted, template)
    options = engine.procedure_options(rankings, validation, [template])
    return extracted, rankings, validation, options


def test_prompt_contains_four_definitions_and_deterministic_evidence():
    extracted, rankings, validation, options = _fixtures()

    prompt = build_procedure_selection_prompt(
        query="two agents share a resource",
        extracted_data=extracted,
        rankings=rankings,
        validation=validation,
        procedure_options=options,
    )

    for procedure in (
        "exact_reuse",
        "parameterized_reuse",
        "partial_recomposition",
        "full_generation",
    ):
        assert procedure in prompt
    assert "deterministically_available" in prompt
    assert "template_rankings" in prompt
    assert "top_candidate_validation" in prompt
    assert "Do not alter scores" in prompt
    assert "Do not invent a template" in prompt
    assert "choose exactly one procedure" in prompt
    assert "CityOS" not in prompt
