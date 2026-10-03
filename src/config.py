from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .models import INPUT_FIELDS, OUTPUT_FIELDS


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1.")
    return value


def _score(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from exc
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100.")
    return value


def _ratio(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}.") from exc
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1.")
    return value


def _config_path(root: Path, name: str, default: str) -> Path:
    raw = os.getenv(name, default).strip() or default
    configured = Path(raw).expanduser()
    return configured if configured.is_absolute() else root / configured


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false.")
    return raw in {"true", "1", "yes"}


def load_field_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Airtable field mapping not found: {path}")
    mapping = json.loads(path.read_text(encoding="utf-8"))
    required = set(INPUT_FIELDS) | set(OUTPUT_FIELDS)
    missing = sorted(required - mapping.keys())
    if missing:
        raise ValueError("Airtable field mapping is missing internal names: " + ", ".join(missing))
    if any(not isinstance(value, str) or not value.strip() for value in mapping.values()):
        raise ValueError("Every Airtable field mapping value must be a non-empty string.")
    return mapping


@dataclass(frozen=True)
class Settings:
    project_root: Path
    openai_api_key: str
    openai_model: str
    prompt_version: str
    max_ai_calls: int
    max_output_tokens: int
    cache_path: Path
    log_level: str
    do_not_contact_domains: frozenset[str]
    duplicate_mode: str
    link_check_timeout_seconds: int
    max_page_bytes: int
    user_agent: str
    system_prompt_path: Path
    project_profiles_path: Path
    airtable_fields_path: Path
    airtable_access_token: str
    airtable_base_id: str
    airtable_table_name: str
    airtable_view_name: str
    airtable_write_results: bool
    airtable_validate_schema: bool
    preview_limit: int = 5
    reject_score_below: int = 40
    accept_score_at_least: int = 80
    min_accept_confidence: float = 0.7
    relevant_min_score: int = 80
    relevant_with_limitations_min_score: int = 60
    weak_min_score: int = 40
    browser_fallback_enabled: bool = True
    browser_fallback_timeout_seconds: int = 25
    browser_executable: str = ""

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        root = project_root or Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env")
        profiles_default = (
            "config/project_profiles.private.json"
            if (root / "config" / "project_profiles.private.json").exists()
            else "config/project_profiles.json"
        )
        fields_default = (
            "config/airtable_fields.private.json"
            if (root / "config" / "airtable_fields.private.json").exists()
            else "config/airtable_fields.json"
        )
        blocked = frozenset(
            item.strip().lower().removeprefix("www.").rstrip(".")
            for item in os.getenv("DO_NOT_CONTACT_DOMAINS", "").split(",")
            if item.strip()
        )
        duplicate_mode = os.getenv("DUPLICATE_MODE", "placement").strip().lower()
        if duplicate_mode not in {"domain", "placement"}:
            raise ValueError("DUPLICATE_MODE must be 'domain' or 'placement'.")
        cache_raw = os.getenv("CACHE_PATH", "cache/audit_cache.sqlite3")
        reject_score_below = _score("REJECT_SCORE_BELOW", 40)
        accept_score_at_least = _score("ACCEPT_SCORE_AT_LEAST", 80)
        weak_min_score = _score("WEAK_MIN_SCORE", 40)
        limited_min_score = _score("RELEVANT_WITH_LIMITATIONS_MIN_SCORE", 60)
        relevant_min_score = _score("RELEVANT_MIN_SCORE", 80)
        if reject_score_below >= accept_score_at_least:
            raise ValueError("REJECT_SCORE_BELOW must be lower than ACCEPT_SCORE_AT_LEAST.")
        if not weak_min_score < limited_min_score < relevant_min_score:
            raise ValueError(
                "Score levels must satisfy WEAK_MIN_SCORE < "
                "RELEVANT_WITH_LIMITATIONS_MIN_SCORE < RELEVANT_MIN_SCORE."
            )
        return cls(
            project_root=root,
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini").strip(),
            prompt_version=os.getenv("PROMPT_VERSION", "v2").strip(),
            max_ai_calls=_positive_int("MAX_AI_CALLS", 50),
            max_output_tokens=_positive_int("MAX_OUTPUT_TOKENS", 1200),
            cache_path=root / cache_raw,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            do_not_contact_domains=blocked,
            duplicate_mode=duplicate_mode,
            link_check_timeout_seconds=_positive_int("LINK_CHECK_TIMEOUT_SECONDS", 15),
            max_page_bytes=_positive_int("MAX_PAGE_BYTES", 2_500_000),
            user_agent=os.getenv("USER_AGENT", "AI-Outreach-Link-Auditor/1.0").strip(),
            system_prompt_path=_config_path(root, "SYSTEM_PROMPT_PATH", "prompts/system_prompt.txt"),
            project_profiles_path=_config_path(root, "PROJECT_PROFILES_PATH", profiles_default),
            airtable_fields_path=_config_path(root, "AIRTABLE_FIELDS_PATH", fields_default),
            airtable_access_token=os.getenv("AIRTABLE_ACCESS_TOKEN", "").strip(),
            airtable_base_id=os.getenv("AIRTABLE_BASE_ID", "").strip(),
            airtable_table_name=os.getenv("AIRTABLE_TABLE_NAME", "").strip(),
            airtable_view_name=os.getenv("AIRTABLE_VIEW_NAME", "").strip(),
            airtable_write_results=_boolean("AIRTABLE_WRITE_RESULTS", False),
            airtable_validate_schema=_boolean("AIRTABLE_VALIDATE_SCHEMA", True),
            preview_limit=_positive_int("PREVIEW_LIMIT", 5),
            reject_score_below=reject_score_below,
            accept_score_at_least=accept_score_at_least,
            min_accept_confidence=_ratio("MIN_ACCEPT_CONFIDENCE", 0.7),
            relevant_min_score=relevant_min_score,
            relevant_with_limitations_min_score=limited_min_score,
            weak_min_score=weak_min_score,
            browser_fallback_enabled=_boolean("BROWSER_FALLBACK_ENABLED", True),
            browser_fallback_timeout_seconds=_positive_int("BROWSER_FALLBACK_TIMEOUT_SECONDS", 25),
            browser_executable=os.getenv("BROWSER_EXECUTABLE", "").strip(),
        )

    def validate_airtable(self) -> None:
        if not self.airtable_access_token:
            raise ValueError("AIRTABLE_ACCESS_TOKEN is missing. Add a Personal Access Token to .env.")
        if not self.airtable_base_id.startswith("app"):
            raise ValueError("AIRTABLE_BASE_ID is missing or invalid; Base IDs normally start with 'app'.")
        if not self.airtable_table_name:
            raise ValueError("AIRTABLE_TABLE_NAME is missing in .env.")
        if not self.airtable_view_name:
            raise ValueError("AIRTABLE_VIEW_NAME is missing in .env.")
