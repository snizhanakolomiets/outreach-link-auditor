from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


INPUT_FIELDS = (
    "audit_selected", "project", "domain", "source_url", "target_url", "site_title",
    "site_description", "content_summary", "categories", "country", "language",
    "estimated_traffic", "contact_name", "contact_role", "contact_email",
    "previous_contact_status",
)

OUTPUT_FIELDS = (
    "audit_decision", "relevance_score", "relevance_level", "relevance_breakdown",
    "confidence", "reasons", "risk_flags", "link_status", "link_found", "link_rel",
    "link_http_status", "resolved_target_url", "link_anchor_text", "link_context",
    "better_fit_project", "personalization_fact", "outreach_angle", "email_subject",
    "email_draft", "next_human_action", "processed_at", "prompt_version", "cache_hit",
    "audit_error",
)


class Decision(str, Enum):
    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    NEEDS_CONTACT = "NEEDS_CONTACT"


class RelevanceLevel(str, Enum):
    RELEVANT = "RELEVANT"
    RELEVANT_WITH_LIMITATIONS = "RELEVANT_WITH_LIMITATIONS"
    WEAK = "WEAK"
    IRRELEVANT = "IRRELEVANT"
    UNKNOWN = "UNKNOWN"


class ProjectProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str
    name: str
    aliases: list[str]
    niches: list[str]
    description: str
    strong_topics: list[str]
    weak_topics: list[str]
    special_constraints: list[str] = Field(default_factory=list)


class LeadRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    record_id: str = ""
    audit_selected: str = ""
    project: str = ""
    domain: str = ""
    source_url: str = ""
    target_url: str = ""
    site_title: str = ""
    site_description: str = ""
    content_summary: str = ""
    categories: str = ""
    country: str = ""
    language: str = ""
    estimated_traffic: str = ""
    contact_name: str = ""
    contact_role: str = ""
    contact_email: str = ""
    previous_contact_status: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def normalize_external_values(cls, value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, list):
            return "" if not value else str(value[0]).strip() if len(value) == 1 else ", ".join(map(str, value))
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value).strip()

    @field_validator("content_summary")
    @classmethod
    def truncate_summary(cls, value: str) -> str:
        return value[:6000]


class LinkCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    found: bool = False
    rel: list[str] = Field(default_factory=list)
    http_status: int | None = None
    resolved_target_url: str = ""
    anchor_text: str = ""
    nearby_context: str = ""
    source_page_title: str = ""
    placement_area: str = "unknown"
    error: str = ""


class RelevanceBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_topic_fit: int = Field(ge=0, le=25)
    surrounding_context_fit: int = Field(ge=0, le=20)
    landing_page_fit: int = Field(ge=0, le=20)
    audience_fit: int = Field(ge=0, le=15)
    site_topic_fit: int = Field(ge=0, le=10)
    anchor_text_fit: int = Field(ge=0, le=10)

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class LLMDecision(BaseModel):
    """Strict AI-owned fields; Python owns final score, level, and decision."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1, max_length=5)
    risk_flags: list[str] = Field(default_factory=list)
    relevance_breakdown: RelevanceBreakdown
    better_fit_project: str = ""
    personalization_fact: str = ""
    outreach_angle: str = ""
    email_subject: str = ""
    email_draft: str = ""
    next_human_action: str

    @model_validator(mode="after")
    def enforce_manual_draft_limits(self) -> "LLMDecision":
        if len(self.email_draft.split()) > 120:
            raise ValueError("email_draft exceeds the 120-word limit")
        return self


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = ""
    audit_decision: Decision = Decision.REVIEW
    relevance_score: int = Field(default=0, ge=0, le=100)
    relevance_level: RelevanceLevel = RelevanceLevel.UNKNOWN
    relevance_breakdown: dict[str, int] = Field(default_factory=dict)
    confidence: float = Field(default=0, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    link_status: str = "NOT_CONFIGURED"
    link_found: bool = False
    link_rel: list[str] = Field(default_factory=list)
    link_http_status: int | None = None
    resolved_target_url: str = ""
    link_anchor_text: str = ""
    link_context: str = ""
    better_fit_project: str = ""
    personalization_fact: str = ""
    outreach_angle: str = ""
    email_subject: str = ""
    email_draft: str = ""
    next_human_action: str = "Review this record manually."
    processed_at: str
    prompt_version: str
    cache_hit: bool = False
    audit_error: str = ""
    skipped: bool = Field(default=False, exclude=True)
    action_required: bool = Field(default=False, exclude=True)


def relevance_level_for_score(
    score: int,
    *,
    relevant_min_score: int = 80,
    relevant_with_limitations_min_score: int = 60,
    weak_min_score: int = 40,
) -> RelevanceLevel:
    if score >= relevant_min_score:
        return RelevanceLevel.RELEVANT
    if score >= relevant_with_limitations_min_score:
        return RelevanceLevel.RELEVANT_WITH_LIMITATIONS
    if score >= weak_min_score:
        return RelevanceLevel.WEAK
    return RelevanceLevel.IRRELEVANT
