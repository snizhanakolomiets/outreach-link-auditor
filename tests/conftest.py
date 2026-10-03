from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings, load_field_mapping


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=ROOT, openai_api_key="test-openai", openai_model="test-model",
        prompt_version="test-v2", max_ai_calls=2, max_output_tokens=500,
        cache_path=tmp_path / "cache.sqlite3", log_level="INFO",
        do_not_contact_domains=frozenset({"blocked.com"}), duplicate_mode="placement",
        link_check_timeout_seconds=2, max_page_bytes=100_000, user_agent="test-agent",
        system_prompt_path=ROOT / "prompts" / "system_prompt.txt",
        project_profiles_path=ROOT / "config" / "project_profiles.json",
        airtable_fields_path=ROOT / "config" / "airtable_fields.json",
        airtable_access_token="test-airtable", airtable_base_id="appTest",
        airtable_table_name="Links", airtable_view_name="Audit queue",
        airtable_write_results=True, airtable_validate_schema=True,
        browser_fallback_enabled=False,
    )


@pytest.fixture
def mapping() -> dict[str, str]:
    return load_field_mapping(ROOT / "config" / "airtable_fields.json")


@pytest.fixture
def valid_row() -> dict[str, str]:
    return {
        "record_id": "rec1", "project": "Example SaaS", "domain": "example.com",
        "source_url": "https://example.com/article", "target_url": "https://target.com/page",
        "site_title": "Example", "site_description": "Creator marketing publication",
        "content_summary": "Article about creator campaigns", "categories": "marketing",
        "country": "US", "language": "en", "estimated_traffic": "10000",
        "contact_name": "Alex", "contact_role": "Editor",
        "contact_email": "alex@example.com", "previous_contact_status": "",
    }


@pytest.fixture
def public_resolver():
    def resolve(host: str, port: int, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]
    return resolve
