import pytest

from tracefix.protocol_templates import (
    Template,
    build_template_from_metadata,
    clear_generated_templates_for_tests,
    get_template,
    list_pattern_ids,
    register_template,
)


def test_template_is_data_only_and_normalizes_patterns():
    template = Template(
        template_id="corridor_access",
        name_of_template="Corridor Access",
        coordination_patterns=["request-grant", "Exclusive Resource Access", "request-grant"],
        number_of_agents=3,
        agent_roles=["robot", "scheduler"],
        communication_flow=["request", "grant", "release"],
        limitations=["does not perform matching"],
        number_of_resources=1,
        number_of_channels=3,
    )

    assert template.get_template_id() == "corridor_access"
    assert template.get_name_of_template() == "Corridor Access"
    assert template.get_coordination_patterns() == [
        "Request-Grant",
        "Exclusive Resource Access",
    ]
    assert template.get_number_of_agents() == 3
    assert template.get_agent_roles() == ["robot", "scheduler"]
    assert template.get_communication_flow() == ["request", "grant", "release"]
    assert template.get_limitations() == ["does not perform matching"]
    assert template.get_number_of_resources() == 1
    assert template.get_number_of_channels() == 3
    assert not hasattr(template, "get_coordination_keywords")
    assert template.to_dict() == {
        "template_id": "corridor_access",
        "name_of_template": "Corridor Access",
        "coordination_patterns": ["Request-Grant", "Exclusive Resource Access"],
        "number_of_agents": 3,
        "agent_roles": ["robot", "scheduler"],
        "communication_flow": ["request", "grant", "release"],
        "limitations": ["does not perform matching"],
        "number_of_resources": 1,
        "number_of_channels": 3,
        "parameterizable_fields": [],
        "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }


def test_template_round_trips_from_dict_and_defensive_copies():
    template = Template.from_dict({
        "template_id": "corridor_access",
        "name_of_template": "Corridor Access",
        "coordination_patterns": ["request-grant"],
        "number_of_agents": 3,
        "agent_roles": ["robot"],
        "communication_flow": ["request"],
        "limitations": [],
        "number_of_resources": 1,
        "number_of_channels": 1,
        "parameterizable_fields": [],
        "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
    })

    roles = template.get_agent_roles()
    roles.append("mutated")

    assert template.get_agent_roles() == ["robot"]
    assert Template.from_dict(template.to_dict()).to_dict() == template.to_dict()


def test_template_rejects_bad_numeric_values_and_unknown_patterns():
    with pytest.raises(ValueError, match="number_of_agents must not be negative"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=[],
            number_of_agents=-1,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
        )


def test_template_validates_reuse_metadata_fields():
    template = Template(
        template_id="corridor_access",
        name_of_template="Corridor Access",
        coordination_patterns=["request-grant"],
        number_of_agents=3,
        agent_roles=["robot", "scheduler"],
        communication_flow=["request", "grant", "release"],
        limitations=["one robot at a time"],
        number_of_resources=1,
        number_of_channels=3,
        parameterizable_fields=["number_of_agents"],
        adaptable_fields=["communication_flow"],
        fatal_mismatch_fields=["coordination_patterns"],
    )

    assert template.get_parameterizable_fields() == ["number_of_agents"]
    assert template.get_adaptable_fields() == ["communication_flow"]
    assert template.get_fatal_mismatch_fields() == ["coordination_patterns"]

    with pytest.raises(ValueError, match="identity field"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=[],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
            parameterizable_fields=["template_id"],
        )

    with pytest.raises(ValueError, match="unsupported template attribute"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=[],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
            adaptable_fields=["not_a_field"],
        )

    with pytest.raises(ValueError, match="unknown coordination pattern"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=["not real"],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
        )


def test_builtin_template_registry_returns_data_objects_without_matching():
    ids = list_pattern_ids()

    assert "traffic_signal_coordination" in ids
    template = get_template("traffic_signal_coordination")
    assert isinstance(template, Template)
    assert template.get_template_id() == "traffic_signal_coordination"
    assert not hasattr(template, "score")
    assert not hasattr(template, "match")


def test_generated_template_registration_rejects_duplicate_ids():
    clear_generated_templates_for_tests()
    template = Template(
        template_id="generated_corridor_access",
        name_of_template="Generated Corridor Access",
        coordination_patterns=["Request-Grant"],
        number_of_agents=2,
        agent_roles=["requester", "scheduler"],
        communication_flow=["request", "grant"],
        limitations=[],
        number_of_resources=1,
        number_of_channels=1,
    )

    register_template(template)
    assert "generated_corridor_access" in list_pattern_ids()
    assert get_template("generated_corridor_access").to_dict() == template.to_dict()
    with pytest.raises(ValueError, match="already registered"):
        register_template(template)


def test_opencode_metadata_constructs_valid_template_before_registration():
    clear_generated_templates_for_tests()
    metadata = {
        "name_of_template": "OpenCode Corridor Template",
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 2,
        "agent_roles": ["requester", "scheduler"],
        "communication_flow": ["request", "grant"],
        "limitations": ["exclusive_access"],
        "number_of_resources": 1,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents"],
        "adaptable_fields": ["communication_flow"],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }

    template = build_template_from_metadata(metadata)
    register_template(template)

    assert template.get_template_id().startswith("generated_")
    assert get_template(template.get_template_id()).get_name_of_template() == "OpenCode Corridor Template"
