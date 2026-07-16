import json

import pytest

from tracefix.runtime.coordination_patterns import (
    COORDINATION_PATTERNS,
    is_valid_coordination_pattern,
    normalize_coordination_pattern,
    normalize_coordination_patterns,
)
from tracefix.runtime.llm_attribute_extractor import (
    AttributeExtractionError,
    AttributeExtractionResponseError,
    ExtractedCoordinationData,
    ExtractedCoordinationAttributes,
    build_attribute_extraction_prompt,
    extract_coordination_attributes,
)


def _valid_payload(**overrides):
    payload = {
        "coordination_patterns": ["request-grant", "Exclusive Resource Access"],
        "number_of_agents": 3,
        "agent_roles": ["robot", "scheduler", "robot", ""],
        "communication_flow": ["request", "grant", "release"],
        "limitations": ["no starvation", "no starvation"],
        "number_of_resources": 1,
        "number_of_channels": 3,
    }
    payload.update(overrides)
    return payload


def test_coordination_pattern_helpers_are_case_insensitive_and_dedupe():
    assert normalize_coordination_pattern("request-grant") == "Request-Grant"
    assert normalize_coordination_pattern("  TOKEN PASSING ") == "Token Passing"
    assert normalize_coordination_patterns([
        "request-grant",
        "Request-Grant",
        "exclusive resource access",
    ]) == ["Request-Grant", "Exclusive Resource Access"]
    assert is_valid_coordination_pattern("Majority Voting") is True
    assert is_valid_coordination_pattern("made up") is False


def test_attributes_accept_valid_empty_and_complete_payloads():
    assert ExtractedCoordinationAttributes().as_dict() == {
        "coordination_patterns": [],
        "number_of_agents": None,
        "agent_roles": [],
        "communication_flow": [],
        "limitations": [],
        "number_of_resources": None,
        "number_of_channels": None,
    }

    result = ExtractedCoordinationAttributes.from_payload(_valid_payload())

    assert result.as_dict() == {
        "coordination_patterns": ["Request-Grant", "Exclusive Resource Access"],
        "number_of_agents": 3,
        "agent_roles": ["robot", "scheduler"],
        "communication_flow": ["request", "grant", "release"],
        "limitations": ["no starvation"],
        "number_of_resources": 1,
        "number_of_channels": 3,
    }

    assert isinstance(result, ExtractedCoordinationData)


@pytest.mark.parametrize(
    "field,value",
    [
        ("number_of_agents", -1),
        ("number_of_agents", True),
        ("number_of_agents", "3"),
        ("number_of_resources", -1),
        ("number_of_channels", False),
    ],
)
def test_attributes_reject_bad_numeric_values(field, value):
    with pytest.raises(AttributeExtractionResponseError):
        ExtractedCoordinationAttributes.from_payload(_valid_payload(**{field: value}))


@pytest.mark.parametrize(
    "extra_key",
    [
        "selected_template",
        "template_id",
        "match_type",
        "confidence",
        "notes",
        "coord_keywords",
        "coordination_keywords",
        "normalized_keywords",
        "explicit_keywords",
        "template_keywords",
    ],
)
def test_attributes_reject_extra_template_classifier_or_metadata_fields(extra_key):
    payload = _valid_payload()
    payload[extra_key] = "not allowed"

    with pytest.raises(AttributeExtractionResponseError):
        ExtractedCoordinationAttributes.from_payload(payload)


def test_attributes_reject_unknown_patterns():
    with pytest.raises(AttributeExtractionResponseError, match="unknown coordination pattern"):
        ExtractedCoordinationAttributes.from_payload(
            _valid_payload(coordination_patterns=["made up pattern"])
        )


def test_prompt_is_extraction_only_and_includes_pattern_vocabulary():
    messages = build_attribute_extraction_prompt("two robots share a corridor")
    rendered = json.dumps(messages)

    assert "Do not select templates" in rendered
    assert "Do not rank templates" in rendered
    assert "Do not return confidence" in rendered
    assert "coordination_keywords" not in rendered
    assert "coord_keywords" not in rendered
    assert "template_keywords" not in rendered
    assert "template_id" not in rendered
    for pattern in COORDINATION_PATTERNS:
        assert pattern in rendered


def test_extractor_uses_dependency_injected_client_and_model():
    seen = {}

    def fake_client(request):
        seen.update(request)
        return _valid_payload(number_of_agents=4)

    result = extract_coordination_attributes(
        "robots share a corridor",
        model="openrouter/test-model",
        client=fake_client,
    )

    assert seen["model"] == "openrouter/test-model"
    assert seen["messages"][0]["role"] == "system"
    assert result.number_of_agents == 4


def test_extractor_accepts_openai_style_response():
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(_valid_payload(number_of_channels=2)),
                }
            }
        ]
    }

    result = extract_coordination_attributes("robots coordinate", client=lambda _req: response)

    assert result.number_of_channels == 2


@pytest.mark.parametrize("response", ["", "not json", "[]", None])
def test_extractor_rejects_empty_or_invalid_responses(response):
    with pytest.raises(AttributeExtractionResponseError):
        extract_coordination_attributes("robots coordinate", client=lambda _req: response)


def test_extractor_wraps_provider_exception():
    def failing_client(_request):
        raise RuntimeError("provider down")

    with pytest.raises(AttributeExtractionError, match="provider failure"):
        extract_coordination_attributes("robots coordinate", client=failing_client)


def test_extractor_without_client_fails_visibly_when_no_api_key(monkeypatch):
    monkeypatch.delenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY", raising=False)
    monkeypatch.delenv("TELLME_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(AttributeExtractionError, match="requires an API key"):
        extract_coordination_attributes("robots coordinate")
