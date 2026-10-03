from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import ProjectProfile


class ProjectProfileFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skip_outbound_source_domains: list[str] = Field(default_factory=list)
    profiles: list[ProjectProfile]
    disambiguation: list[dict[str, str]]


class ProjectProfiles:
    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"Project profiles file not found: {path}")
        parsed = ProjectProfileFile.model_validate_json(path.read_text(encoding="utf-8"))
        self.profiles = {profile.canonical_id: profile for profile in parsed.profiles}
        self.skip_outbound_source_domains = frozenset(
            item.casefold().strip().removeprefix("www.").rstrip(".")
            for item in parsed.skip_outbound_source_domains
            if item.strip()
        )
        self.aliases: dict[str, str] = {}
        self.disambiguation = parsed.disambiguation
        for profile in parsed.profiles:
            for alias in [profile.canonical_id, profile.name, *profile.aliases]:
                key = self._normalize(alias)
                if key in self.aliases and self.aliases[key] != profile.canonical_id:
                    raise ValueError(f"Duplicate project alias in configuration: {alias}")
                self.aliases[key] = profile.canonical_id

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().strip().split())

    def resolve(self, value: str) -> ProjectProfile | None:
        canonical_id = self.aliases.get(self._normalize(value))
        return self.profiles.get(canonical_id) if canonical_id else None

    def resolve_for_lead(self, record_project: str, fallback_project: str = "") -> ProjectProfile | None:
        return self.resolve(record_project or fallback_project)

    @staticmethod
    def compact_context(profile: ProjectProfile) -> dict[str, object]:
        return profile.model_dump()

    def compact_catalog(self) -> list[dict[str, object]]:
        return [
            {"canonical_id": item.canonical_id, "name": item.name, "niches": item.niches}
            for item in self.profiles.values()
        ]
