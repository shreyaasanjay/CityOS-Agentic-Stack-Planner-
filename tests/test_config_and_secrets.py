"""Tests for .env config loading and that secrets never reach artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tellme_harness import TellMeHarness
from tellme_harness.config import (
    get_cityos_discovery_config,
    get_llm_config,
    load_dotenv,
    parse_env_file,
)


def test_parse_env_file_handles_comments_quotes_and_export() -> None:
    text = "\n".join(
        [
            "# a comment",
            "",
            "OPENAI_API_KEY=sk-abc123",
            'OPENAI_MODEL="gpt-4.1-mini"',
            "export OPENAI_BASE_URL='https://example.test/v1'",
            "MALFORMED_LINE_NO_EQUALS",
        ]
    )
    parsed = parse_env_file(text)
    assert parsed["OPENAI_API_KEY"] == "sk-abc123"
    assert parsed["OPENAI_MODEL"] == "gpt-4.1-mini"
    assert parsed["OPENAI_BASE_URL"] == "https://example.test/v1"
    assert "MALFORMED_LINE_NO_EQUALS" not in parsed


def test_load_dotenv_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_load_dotenv_does_not_override_existing_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_MODEL=from-file\n", encoding="utf-8")
    load_dotenv(env_file)
    import os

    assert os.environ["OPENAI_MODEL"] == "already-set"


def test_get_llm_config_reports_no_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # ensure_loaded already ran once; re-reading env reflects the deletion
    import tellme_harness.config as config_module

    config_module._loaded = True  # skip .env reload so the deletion sticks
    config = get_llm_config()
    assert config.has_key is False
    assert config.model  # default model present


def test_get_llm_config_reads_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-value")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    import tellme_harness.config as config_module

    config_module._loaded = True
    config = get_llm_config()
    assert config.has_key is True
    assert config.api_key == "sk-test-value"


def test_get_cityos_discovery_config_defaults_to_test_mock(monkeypatch) -> None:
    monkeypatch.delenv("CITYOS_DISCOVERY_MODE", raising=False)
    monkeypatch.delenv("CITYOS_DISCOVERY_PROVIDER", raising=False)
    import tellme_harness.config as config_module

    config_module._loaded = True
    config = get_cityos_discovery_config()
    assert config.mode == "test"
    assert config.provider == "mock"


def test_get_cityos_discovery_config_reads_production_values(monkeypatch) -> None:
    monkeypatch.setenv("CITYOS_DISCOVERY_MODE", "production")
    monkeypatch.setenv("CITYOS_DISCOVERY_PROVIDER", "http")
    monkeypatch.setenv("CITYOS_DISCOVERY_SERVICE_URL", "http://cityos.local/v1/discovery/query")
    monkeypatch.setenv("CITYOS_DISCOVERY_BEARER_TOKEN", "test-token")
    import tellme_harness.config as config_module

    config_module._loaded = True
    config = get_cityos_discovery_config()
    assert config.mode == "production"
    assert config.provider == "http"
    assert config.build_query_url() == "http://cityos.local/v1/discovery/query"
    assert config.bearer_token == "test-token"


def test_get_cityos_discovery_config_rejects_missing_production_auth(monkeypatch) -> None:
    monkeypatch.setenv("CITYOS_DISCOVERY_MODE", "production")
    monkeypatch.setenv("CITYOS_DISCOVERY_PROVIDER", "http")
    monkeypatch.setenv("CITYOS_DISCOVERY_SERVICE_URL", "http://cityos.local/v1/discovery/query")
    monkeypatch.delenv("CITYOS_DISCOVERY_BEARER_TOKEN", raising=False)
    import tellme_harness.config as config_module

    config_module._loaded = True
    with pytest.raises(ValueError, match="CITYOS_DISCOVERY_BEARER_TOKEN"):
        get_cityos_discovery_config()


def test_secret_never_appears_in_new_capability_artifacts(tmp_path: Path, monkeypatch) -> None:
    secret = "sk-CAPABILITY-LEAK-CANARY"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    import tellme_harness.config as config_module

    config_module._loaded = True

    runs_root = tmp_path / ".runs" / "tellme"
    # fake_llm mode: no network, but the key is present in the environment.
    harness = TellMeHarness(runs_root=runs_root, agent_backend_mode="fake_llm")
    answer = harness.handle_query("How many people were in the room at 10:05?")

    run_dir = runs_root / answer.query_id
    for file_path in run_dir.rglob("*"):
        if file_path.is_file():
            assert secret not in file_path.read_text(encoding="utf-8", errors="ignore"), (
                f"secret leaked into {file_path.relative_to(run_dir)}"
            )
    assert secret not in json.dumps(answer.model_dump())
