import json

from tracefix.protocol_templates.template import Template
from tracefix.runtime.deterministic_template_engine import (
    DeterministicTemplateEngine,
    rank_templates_for_attributes,
)
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData


def _sample_extracted(**overrides):
    payload = {
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Task Prioritization",
        ],
        "number_of_agents": 3,
        "agent_roles": ["requester"],
        "communication_flow": ["request", "grant", "enter", "exit", "release"],
        "limitations": [
            "emergency_agents_have_priority",
            "ordinary_agents_must_not_starve",
        ],
        "number_of_resources": 1,
        "number_of_channels": None,
    }
    payload.update(overrides)
    return ExtractedCoordinationData.from_payload(payload)


def _priority_template(**overrides):
    data = {
        "template_id": "priority_shared_resource",
        "name_of_template": "Priority Shared Resource",
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Task Prioritization",
        ],
        "number_of_agents": 3,
        "agent_roles": ["requester", "scheduler", "emergency_agent"],
        "communication_flow": ["request", "grant", "enter", "exit", "release"],
        "limitations": [
            "emergency_agents_have_priority",
            "ordinary_agents_must_not_starve",
        ],
        "number_of_resources": 1,
        "number_of_channels": 3,
    }
    data.update(overrides)
    return Template.from_dict(data)


def _producer_consumer_template():
    return Template(
        template_id="producer_consumer",
        name_of_template="Producer-Consumer",
        coordination_patterns=["Producer-Consumer"],
        number_of_agents=2,
        agent_roles=["producer", "consumer"],
        communication_flow=["produce", "consume"],
        limitations=[],
        number_of_resources=0,
        number_of_channels=1,
    )


def _consensus_template():
    return Template(
        template_id="consensus",
        name_of_template="Consensus",
        coordination_patterns=["Consensus", "Majority Voting"],
        number_of_agents=3,
        agent_roles=["proposer", "voter"],
        communication_flow=["propose", "vote", "decide"],
        limitations=["majority_must_agree"],
        number_of_resources=0,
        number_of_channels=3,
    )


def test_rank_templates_puts_priority_shared_resource_first_and_serializes():
    extracted = _sample_extracted()
    templates = [_producer_consumer_template(), _consensus_template(), _priority_template()]

    rankings = rank_templates_for_attributes(extracted, templates)

    assert [ranking.template_id for ranking in rankings] == [
        "priority_shared_resource",
        "consensus",
        "producer_consumer",
    ]
    assert rankings[0].match_count == 5
    assert rankings[0].mismatch_count == 0
    assert rankings[0].unknown_count == 1
    assert rankings[0].compared_count == 5
    assert rankings[0].score == 1.0
    assert [comparison.attribute_name for comparison in rankings[0].comparisons] == [
        "coordination_patterns",
        "number_of_agents",
        "agent_roles",
        "communication_flow",
        "number_of_resources",
        "number_of_channels",
    ]
    assert "limitations" not in {comparison.attribute_name for comparison in rankings[0].comparisons}
    json.dumps([ranking.to_dict() for ranking in rankings])


def test_shared_comparisons_return_true_false_and_none():
    engine = DeterministicTemplateEngine()
    comparisons = {
        comparison.attribute_name: comparison
        for comparison in engine.compare(_sample_extracted(), _producer_consumer_template())
    }

    assert comparisons["number_of_agents"].result is False
    assert comparisons["coordination_patterns"].result is False
    assert comparisons["number_of_channels"].result is None
    assert "limitations" not in comparisons


def test_unknown_values_do_not_lower_score():
    extracted = _sample_extracted(
        number_of_channels=None,
        coordination_patterns=[],
        agent_roles=[],
    )

    ranking = DeterministicTemplateEngine().rank(extracted, [_priority_template()])[0]

    assert ranking.match_count == 3
    assert ranking.mismatch_count == 0
    assert ranking.unknown_count == 3
    assert ranking.score == 1.0


def test_limitations_do_not_change_ranking_score():
    engine = DeterministicTemplateEngine()
    matching_limitations = _sample_extracted()
    mismatching_limitations = _sample_extracted(limitations=["something_else"])

    first = engine.rank(matching_limitations, [_priority_template()])[0]
    second = engine.rank(mismatching_limitations, [_priority_template()])[0]

    assert first.score == second.score
    assert first.match_count == second.match_count
    assert first.mismatch_count == second.mismatch_count


def test_validation_inspects_limitations_and_rejects_mismatch():
    engine = DeterministicTemplateEngine()

    valid_result = engine.validate(_sample_extracted(), _priority_template())
    invalid_result = engine.validate(
        _sample_extracted(limitations=["unsupported_limit"]),
        _priority_template(),
    )

    assert valid_result.valid is True
    assert "limitations" in valid_result.matched_fields
    assert invalid_result.valid is False
    assert "limitations" in invalid_result.mismatched_fields


def test_validation_rejects_zero_comparable_structural_fields():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(
        coordination_patterns=[],
        number_of_agents=None,
        agent_roles=[],
        communication_flow=[],
        limitations=[],
        number_of_resources=None,
        number_of_channels=None,
    )

    result = engine.validate(extracted, _priority_template())

    assert result.valid is False
    assert "zero comparable structural attributes" in " ".join(result.reasons)


def test_inputs_are_not_mutated():
    extracted = _sample_extracted()
    template = _priority_template()
    before_extracted = extracted.as_dict()
    before_template = template.to_dict()

    DeterministicTemplateEngine().rank(extracted, [template])
    DeterministicTemplateEngine().validate(extracted, template)

    assert extracted.as_dict() == before_extracted
    assert template.to_dict() == before_template


def test_procedure_options_perfect_match_enables_exact_reuse():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _priority_template()
    rankings = engine.rank(extracted, [template])
    validation = engine.validate(extracted, template)

    options = engine.procedure_options(rankings, validation, [template])
    by_name = {option.procedure: option for option in options.options}

    assert tuple(by_name) == (
        "exact_reuse",
        "parameterized_reuse",
        "partial_recomposition",
        "full_generation",
    )
    assert by_name["exact_reuse"].deterministically_available is True
    assert by_name["parameterized_reuse"].deterministically_available is False


def test_parameter_only_mismatch_requires_declared_parameter_field():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_agents=4, number_of_channels=3)
    allowed = _priority_template(parameterizable_fields=["number_of_agents"])
    blocked = _priority_template(template_id="blocked", parameterizable_fields=[])

    allowed_options = engine.procedure_options(
        engine.rank(extracted, [allowed]),
        engine.validate(extracted, allowed),
        [allowed],
    )
    blocked_options = engine.procedure_options(
        engine.rank(extracted, [blocked]),
        engine.validate(extracted, blocked),
        [blocked],
    )

    assert allowed_options.option_for("parameterized_reuse").deterministically_available is True
    assert blocked_options.option_for("parameterized_reuse").deterministically_available is False


def test_nonparameterizable_mismatch_blocks_parameterized_reuse():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_agents=4, number_of_channels=3)
    template = _priority_template(parameterizable_fields=["number_of_channels"])

    options = engine.procedure_options(
        engine.rank(extracted, [template]),
        engine.validate(extracted, template),
        [template],
    )

    assert options.option_for("parameterized_reuse").deterministically_available is False


def test_adaptable_nonfatal_mismatch_may_enable_partial_recomposition():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _priority_template(
        communication_flow=["request", "grant", "release"],
        adaptable_fields=["communication_flow"],
    )

    options = engine.procedure_options(
        engine.rank(extracted, [template]),
        engine.validate(extracted, template),
        [template],
    )

    assert options.option_for("partial_recomposition").deterministically_available is True


def test_fatal_pattern_mismatch_blocks_reuse_and_full_generation_available():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _producer_consumer_template()
    rankings = engine.rank(extracted, [template])
    validation = engine.validate(extracted, template)

    options = engine.procedure_options(rankings, validation, [template])

    assert options.option_for("exact_reuse").deterministically_available is False
    assert options.option_for("parameterized_reuse").deterministically_available is False
    assert options.option_for("partial_recomposition").deterministically_available is False
    assert options.option_for("full_generation").deterministically_available is True


def test_no_templates_emits_all_options_with_full_generation_available():
    options = DeterministicTemplateEngine().procedure_options([], None, [])

    assert [option.procedure for option in options.options] == [
        "exact_reuse",
        "parameterized_reuse",
        "partial_recomposition",
        "full_generation",
    ]
    assert options.option_for("full_generation").deterministically_available is True
