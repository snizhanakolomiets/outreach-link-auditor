from __future__ import annotations

import pytest

from src.config import Settings


def test_airtable_writes_are_disabled_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AIRTABLE_WRITE_RESULTS", raising=False)

    settings = Settings.from_env(project_root=tmp_path)

    assert settings.airtable_write_results is False


def test_paths_and_thresholds_can_be_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", "custom/prompt.txt")
    monkeypatch.setenv("PROJECT_PROFILES_PATH", "/tmp/profiles.json")
    monkeypatch.setenv("AIRTABLE_FIELDS_PATH", "custom/fields.json")
    monkeypatch.setenv("PREVIEW_LIMIT", "9")
    monkeypatch.setenv("REJECT_SCORE_BELOW", "30")
    monkeypatch.setenv("ACCEPT_SCORE_AT_LEAST", "90")
    monkeypatch.setenv("MIN_ACCEPT_CONFIDENCE", "0.85")
    monkeypatch.setenv("WEAK_MIN_SCORE", "30")
    monkeypatch.setenv("RELEVANT_WITH_LIMITATIONS_MIN_SCORE", "55")
    monkeypatch.setenv("RELEVANT_MIN_SCORE", "90")
    monkeypatch.setenv("BROWSER_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("BROWSER_FALLBACK_TIMEOUT_SECONDS", "40")
    monkeypatch.setenv("BROWSER_EXECUTABLE", "/Applications/Custom Chrome")

    settings = Settings.from_env(project_root=tmp_path)

    assert settings.system_prompt_path == tmp_path / "custom/prompt.txt"
    assert str(settings.project_profiles_path) == "/tmp/profiles.json"
    assert settings.airtable_fields_path == tmp_path / "custom/fields.json"
    assert settings.preview_limit == 9
    assert settings.reject_score_below == 30
    assert settings.accept_score_at_least == 90
    assert settings.min_accept_confidence == 0.85
    assert settings.browser_fallback_enabled is False
    assert settings.browser_fallback_timeout_seconds == 40
    assert settings.browser_executable == "/Applications/Custom Chrome"


def test_invalid_threshold_order_is_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REJECT_SCORE_BELOW", "90")
    monkeypatch.setenv("ACCEPT_SCORE_AT_LEAST", "80")

    with pytest.raises(ValueError, match="REJECT_SCORE_BELOW"):
        Settings.from_env(project_root=tmp_path)
