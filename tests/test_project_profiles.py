from pathlib import Path

from src.project_profiles import ProjectProfiles


ROOT = Path(__file__).resolve().parents[1]


def test_every_alias_and_canonical_id_resolves() -> None:
    profiles = ProjectProfiles(ROOT / "config" / "project_profiles.json")
    assert len(profiles.profiles) == 2
    for expected, profile in profiles.profiles.items():
        for alias in [expected, profile.name, *profile.aliases]:
            assert profiles.resolve(alias).canonical_id == expected


def test_unknown_project() -> None:
    profiles = ProjectProfiles(ROOT / "config" / "project_profiles.json")
    assert profiles.resolve("not configured") is None


def test_airtable_project_overrides_cli_fallback() -> None:
    profiles = ProjectProfiles(ROOT / "config" / "project_profiles.json")
    assert profiles.resolve_for_lead("Example SaaS", "example-agency").canonical_id == "example-saas"
    assert profiles.resolve_for_lead("", "example-agency").canonical_id == "example-agency"


def test_example_project_alias_resolves() -> None:
    profiles = ProjectProfiles(ROOT / "config" / "project_profiles.json")
    assert profiles.resolve("Demo Software").canonical_id == "example-saas"


def test_owned_outbound_source_domains_are_configured() -> None:
    profiles = ProjectProfiles(ROOT / "config" / "project_profiles.json")
    assert "your-company.example" in profiles.skip_outbound_source_domains


def test_project_specific_context_is_not_hardcoded() -> None:
    profiles = ProjectProfiles(ROOT / "config" / "project_profiles.json")
    saas = profiles.compact_context(profiles.resolve("Example SaaS"))
    agency = profiles.compact_context(profiles.resolve("Example Agency"))
    assert "workflow automation" in saas["strong_topics"]
    assert "performance marketing" in agency["strong_topics"]
