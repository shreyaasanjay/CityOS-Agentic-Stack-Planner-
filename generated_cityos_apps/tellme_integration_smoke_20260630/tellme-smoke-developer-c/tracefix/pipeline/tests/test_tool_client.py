from tracefix.pipeline.tool_client import ToolClient, _parse_tool_call_arguments, _repair_tool_call_json


def test_try_parse_tool_call_from_text_with_malformed_json():
    config = type("Cfg", (), {
        "provider": "openai",
        "model": "gpt-5",
        "api_key": None,
        "base_url": None,
        "max_tokens": 1,
        "reasoning_effort": None,
        "temperature": 1.0,
        "thinking_budget": 0,
    })()
    client = ToolClient(config=config, tool_schemas=[])

    text = '{"name":"compile_scaffold","parameters":{"path":"src","opt":true}}'
    tool_call = client._try_parse_tool_call_from_text(text)

    assert tool_call is not None
    assert tool_call.name == "compile_scaffold"
    assert tool_call.arguments == {"path": "src", "opt": True}


def test_try_parse_tool_call_from_text_returns_none_for_non_json():
    config = type("Cfg", (), {
        "provider": "openai",
        "model": "gpt-5",
        "api_key": None,
        "base_url": None,
        "max_tokens": 1,
        "reasoning_effort": None,
        "temperature": 1.0,
        "thinking_budget": 0,
    })()
    client = ToolClient(config=config, tool_schemas=[])

    text = "I have no tool call here."
    assert client._try_parse_tool_call_from_text(text) is None


def test_parse_tool_call_arguments_recovers_literal_backslash_b():
    # Model wrote a literal backslash followed directly by 'b' (e.g. it meant
    # the PlusCal text `/\ b_submitted` but emitted `/\b_submitted` without
    # escaping the backslash for JSON). A plain json.loads silently decodes
    # this as a backspace control character, destroying the backslash with
    # no parse error. The repair must recover the literal backslash instead.
    raw = '{"old_string": "if (a_submitted /' + chr(92) + 'b_submitted) {"}'
    result = _parse_tool_call_arguments(raw)
    assert result["old_string"] == "if (a_submitted /\\b_submitted) {"


def test_parse_tool_call_arguments_recovers_literal_backslash_f():
    raw = '{"x": "a' + chr(92) + 'foo"}'
    result = _parse_tool_call_arguments(raw)
    assert result["x"] == "a\\foo"


def test_parse_tool_call_arguments_preserves_normal_escapes():
    raw = (
        '{"a": "line1' + chr(92) + 'nline2", '
        '"b": "tab' + chr(92) + 'there", '
        '"c": "quote ' + chr(92) + '"x' + chr(92) + '""}'
    )
    result = _parse_tool_call_arguments(raw)
    assert result["a"] == "line1\nline2"
    assert result["b"] == "tab\there"
    assert result["c"] == 'quote "x"'


def test_parse_tool_call_arguments_preserves_correctly_doubled_backslash():
    raw = '{"old_string": "a' + chr(92) * 2 + ' b"}'
    result = _parse_tool_call_arguments(raw)
    assert result["old_string"] == "a\\ b"


def test_parse_tool_call_arguments_empty_string():
    assert _parse_tool_call_arguments("") == {}


def test_repair_tool_call_json_leaves_valid_json_unchanged():
    raw = '{"path": "Protocol.tla", "new_string": "skip;"}'
    assert _repair_tool_call_json(raw) == raw
