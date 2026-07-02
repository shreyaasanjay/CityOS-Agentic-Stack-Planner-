from __future__ import annotations

import warnings

from tracefix.textio import safe_read_json, safe_read_text


def test_safe_read_text_utf8(tmp_path):
    path = tmp_path / "description.md"
    path.write_text("hello - utf8\n", encoding="utf-8")

    assert safe_read_text(path) == "hello - utf8\n"


def test_safe_read_text_utf8_bom(tmp_path):
    path = tmp_path / "description.md"
    path.write_bytes("hello bom\n".encode("utf-8-sig"))

    assert safe_read_text(path) == "hello bom\n"


def test_safe_read_text_cp1252_em_dash_warns(tmp_path):
    path = tmp_path / "description.md"
    path.write_bytes("alpha \u2014 beta\n".encode("cp1252"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        text = safe_read_text(path)

    assert text == "alpha \u2014 beta\n"
    assert any("CP1252 fallback" in str(item.message) for item in caught)


def test_safe_read_text_replacement_fallback_warns(tmp_path):
    path = tmp_path / "description.md"
    path.write_bytes(b"\x81\x8d\x8f")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        text = safe_read_text(path)

    assert "\ufffd" in text
    assert any("replacement characters" in str(item.message) for item in caught)


def test_safe_read_json_cp1252_string_value(tmp_path):
    path = tmp_path / "ir.json"
    path.write_bytes(b'{"description": "alpha \x97 beta"}')

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        data = safe_read_json(path, {})

    assert data == {"description": "alpha \u2014 beta"}
