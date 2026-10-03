from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pydantic import ValidationError

from .cache import ResultCache
from .config import Settings
from .filters import apply_pre_filters, duplicate_key, normalize_domain
from .link_checker import check_link, check_link_in_browser
from .models import (
    INPUT_FIELDS, OUTPUT_FIELDS, AuditResult, Decision, LLMDecision, LeadRow,
    LinkCheck, ProjectProfile, RelevanceLevel, relevance_level_for_score,
)
from .project_profiles import ProjectProfiles


LOGGER = logging.getLogger(__name__)
CRITICAL_AI_FLAGS = {
    "ANCHOR_MISMATCH", "FORCED_CONTEXT", "WRONG_BRAND_MATCH", "COMPETITOR_PAGE",
    "PAGE_TOPIC_MISMATCH", "LANDING_PAGE_MISMATCH", "LANGUAGE_MISMATCH",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def context_hash(context: dict[str, Any]) -> str:
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_error(prefix: str, exc: Exception) -> str:
    detail = str(exc).replace("\n", " ")[:500]
    detail = re.sub(r"(?i)\b(?:bearer\s+|sk-|pat)[A-Za-z0-9._-]+", "[REDACTED]", detail)
    return f"{prefix}: {detail}" if detail else f"{prefix} ({type(exc).__name__})"


def _base_result(record_id: str, prompt_version: str) -> AuditResult:
    return AuditResult(record_id=record_id, processed_at=_utc_now(), prompt_version=prompt_version)


def _require_action(result: AuditResult, message: str, *flags: str) -> AuditResult:
    """Keep Airtable audit fields untouched and surface a human-readable request."""
    result.skipped = True
    result.action_required = True
    result.reasons = [message]
    result.risk_flags = list(dict.fromkeys([*result.risk_flags, *flags]))
    return result


def _link_flags(link: LinkCheck) -> list[str]:
    flags: list[str] = []
    if link.status == "NOT_FOUND_IN_HTML":
        flags.append("LINK_NOT_FOUND")
    if link.status == "SOURCE_4XX" and link.http_status in {404, 410}:
        flags.append("SOURCE_PAGE_REMOVED")
    if link.status in {"SOURCE_UNAVAILABLE", "SOURCE_5XX", "BLOCKED_OR_FORBIDDEN"}:
        flags.append("SOURCE_UNAVAILABLE")
    if "nofollow" in link.rel:
        flags.append("NOFOLLOW")
    if "sponsored" in link.rel:
        flags.append("SPONSORED")
    if "ugc" in link.rel:
        flags.append("UGC_ATTRIBUTE")
    return flags


def _apply_link(result: AuditResult, link: LinkCheck) -> None:
    result.link_status = link.status
    result.link_found = link.found
    result.link_rel = link.rel
    result.link_http_status = link.http_status
    result.resolved_target_url = link.resolved_target_url
    result.link_anchor_text = link.anchor_text
    result.link_context = link.nearby_context
    result.risk_flags = list(dict.fromkeys([*result.risk_flags, *_link_flags(link)]))
    if link.error:
        result.audit_error = link.error


def _apply_contact_guidance(result: AuditResult, contact_note: str) -> None:
    if not contact_note:
        return
    existing = result.next_human_action.strip()
    contact_action = f"{contact_note} No email has been sent."
    if existing and existing != "Review this record manually.":
        result.next_human_action = f"{contact_action} {existing}"
    else:
        result.next_human_action = contact_action


def _link_failure_instruction(link: LinkCheck) -> str:
    status = link.status
    if status == "BLOCKED_OR_FORBIDDEN":
        return (
            "The site blocked automated access"
            f"{f' (HTTP {link.http_status})' if link.http_status else ''}. "
            "Open the Article URL in a browser. If the address is wrong, correct it. "
            "If the page is only available in a browser, retry later or use another "
            "publicly accessible page containing the link."
        )
    if status == "SOURCE_4XX" and link.http_status in {404, 410}:
        return (
            f"The Article URL returns HTTP {link.http_status}; the page was removed or moved. "
            "Find the current article address and replace the Article URL."
        )
    if status == "NOT_FOUND_IN_HTML":
        return (
            "No link to the Link URL was found on the Article URL page. "
            "Check both addresses and confirm that the link exists in the published HTML, "
            "rather than appearing only after a click or JavaScript execution."
        )
    if status == "INVALID_SOURCE_URL":
        return "The Article URL format is invalid. Enter a complete public address beginning with https://."
    if status in {"SOURCE_UNAVAILABLE", "SOURCE_5XX"}:
        suffix = f" (HTTP {link.http_status})" if link.http_status else ""
        return (
            f"The Article URL page is currently unavailable{suffix}. Check the address and site availability, "
            "then run the audit again."
        )
    detail = link.error or f"the check finished with status {status}"
    return f"The link could not be checked: {detail}. Check the Article URL and Link URL, then run the audit again."


def _final_decision(
    ai: LLMDecision,
    link: LinkCheck,
    flags: set[str],
    *,
    reject_score_below: int = 40,
    accept_score_at_least: int = 80,
    min_accept_confidence: float = 0.7,
) -> Decision:
    score = ai.relevance_breakdown.total
    if score < reject_score_below:
        return Decision.REJECT
    if score < accept_score_at_least:
        return Decision.REVIEW
    if not link.status.startswith("ACTIVE_"):
        return Decision.REVIEW
    if set(link.rel) & {"nofollow", "sponsored", "ugc"}:
        return Decision.REVIEW
    if flags & CRITICAL_AI_FLAGS or ai.confidence < min_accept_confidence:
        return Decision.REVIEW
    if not ai.personalization_fact.strip():
        return Decision.REVIEW
    return Decision.ACCEPT


@dataclass
class RunSummary:
    records_read: int = 0
    records_processed: int = 0
    records_skipped: int = 0
    links_found: int = 0
    links_not_found: int = 0
    follow_links: int = 0
    nofollow_links: int = 0
    sponsored_links: int = 0
    ugc_links: int = 0
    accept: int = 0
    review: int = 0
    reject: int = 0
    needs_contact: int = 0
    new_ai_calls: int = 0
    cache_hits: int = 0
    airtable_updates: int = 0
    airtable_write_failures: int = 0
    action_required: int = 0
    action_messages: list[str] = field(default_factory=list)
    backup_path: str = ""

    def add(self, result: AuditResult, raw: dict[str, Any] | None = None) -> None:
        if result.action_required:
            self.action_required += 1
            raw = raw or {}
            identity = [result.record_id or "record without ID"]
            domain = str(raw.get("domain", "")).strip()
            source_url = str(raw.get("source_url", "")).strip()
            if domain:
                identity.append(domain)
            if source_url and source_url != domain:
                identity.append(f"Article URL: {source_url}")
            label = " | ".join(identity)
            message = result.reasons[0] if result.reasons else "Manual review is required."
            self.action_messages.append(f"{label}: {message}")
        if result.skipped:
            self.records_skipped += 1
            return
        self.records_processed += 1
        self.links_found += int(result.link_found)
        self.links_not_found += int(result.link_status == "NOT_FOUND_IN_HTML")
        self.follow_links += int(result.link_status == "ACTIVE_FOLLOW")
        self.nofollow_links += int("nofollow" in result.link_rel)
        self.sponsored_links += int("sponsored" in result.link_rel)
        self.ugc_links += int("ugc" in result.link_rel)
        self.accept += int(result.audit_decision is Decision.ACCEPT)
        self.review += int(result.audit_decision is Decision.REVIEW)
        self.reject += int(result.audit_decision is Decision.REJECT)
        self.needs_contact += int(result.audit_decision is Decision.NEEDS_CONTACT)
        self.cache_hits += int(result.cache_hit)

    def display(self) -> str:
        labels = {
            "records_read": "Records read", "records_processed": "Records processed",
            "records_skipped": "Records skipped", "links_found": "Links found",
            "links_not_found": "Links not found", "follow_links": "Follow links",
            "nofollow_links": "Nofollow links", "sponsored_links": "Sponsored links",
            "ugc_links": "UGC links", "accept": "ACCEPT", "review": "REVIEW",
            "reject": "REJECT", "needs_contact": "NEEDS_CONTACT",
            "new_ai_calls": "New AI calls", "cache_hits": "Cache hits",
            "airtable_updates": "Airtable updates",
            "airtable_write_failures": "Airtable write failures",
            "action_required": "Action required",
            "backup_path": "Output backup path",
        }
        return "\n".join(f"{label}: {getattr(self, key)}" for key, label in labels.items())


class LeadProcessor:
    def __init__(
        self,
        settings: Settings,
        *,
        campaign_id: str,
        project_profiles: ProjectProfiles,
        llm_client: Any | None,
        cache: ResultCache,
        project: str = "",
        dry_run: bool = False,
        max_ai_calls: int | None = None,
        skip_link_check: bool = False,
        link_checker: Callable[..., LinkCheck] = check_link,
        browser_link_checker: Callable[..., LinkCheck] = check_link_in_browser,
    ) -> None:
        self.settings = settings
        self.campaign_id = campaign_id
        self.project_profiles = project_profiles
        self.llm_client = llm_client
        self.cache = cache
        self.project = project
        self.dry_run = dry_run
        self.max_ai_calls = settings.max_ai_calls if max_ai_calls is None else max_ai_calls
        self.skip_link_check = skip_link_check
        self.link_checker = link_checker
        self.browser_link_checker = browser_link_checker
        self.ai_calls = 0

    def _link_check(self, lead: LeadRow) -> LinkCheck:
        if self.skip_link_check:
            return LinkCheck(status="SKIPPED", resolved_target_url=lead.target_url)
        result = self.link_checker(
            lead.source_url, lead.target_url,
            timeout=self.settings.link_check_timeout_seconds,
            max_page_bytes=self.settings.max_page_bytes,
            user_agent=self.settings.user_agent,
        )
        if (
            self.settings.browser_fallback_enabled
            and result.status == "BLOCKED_OR_FORBIDDEN"
            and result.http_status in {401, 403}
        ):
            LOGGER.info("HTTP access was denied for %s; trying visible Chrome fallback.", lead.source_url)
            return self.browser_link_checker(
                lead.source_url,
                lead.target_url,
                timeout=self.settings.browser_fallback_timeout_seconds,
                max_page_bytes=self.settings.max_page_bytes,
                browser_executable=self.settings.browser_executable,
            )
        return result

    def _llm_context(self, lead: LeadRow, link: LinkCheck, profile: ProjectProfile) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "prompt_version": self.settings.prompt_version,
            "project": self.project_profiles.compact_context(profile),
            "project_catalog": self.project_profiles.compact_catalog(),
            "project_disambiguation": self.project_profiles.disambiguation,
            "lead": {
                "domain": normalize_domain(lead.domain), "source_url": lead.source_url,
                "target_url": lead.target_url, "site_title": lead.site_title,
                "site_description": lead.site_description,
                "content_summary": lead.content_summary[:6000], "categories": lead.categories,
                "country": lead.country, "language": lead.language,
                "estimated_traffic": lead.estimated_traffic, "contact_name": lead.contact_name,
                "contact_role": lead.contact_role,
                "previous_contact_status": lead.previous_contact_status,
            },
            "link_check": {
                "source_page_title": link.source_page_title, "anchor_text": link.anchor_text,
                "nearby_context": link.nearby_context, "placement_area": link.placement_area,
                "status": link.status, "rel": link.rel, "http_status": link.http_status,
                "resolved_target_url": link.resolved_target_url,
            },
            "constraints": {
                "email_is_draft_only": True, "manual_approval_required": True,
                "max_email_words": 120, "use_only_supplied_evidence": True,
            },
        }

    def process_row(self, raw: dict[str, Any], seen_keys: set[str]) -> AuditResult:
        record_id = str(raw.get("record_id", ""))
        result = _base_result(record_id, self.settings.prompt_version)
        try:
            lead = LeadRow.model_validate(raw)
        except ValidationError as exc:
            return _require_action(
                result,
                f"Input data is invalid: {_safe_error('validation', exc)}",
                "INVALID_INPUT",
            )

        missing = [
            label
            for value, label in (
                (lead.project, "Project"),
                (lead.domain, "Domain"),
                (lead.source_url, "Article URL"),
                (lead.target_url, "Link URL"),
            )
            if not value.strip()
        ]
        if missing:
            return _require_action(
                result,
                "Fill the required Airtable field(s): " + ", ".join(missing) + ".",
                "MISSING_REQUIRED_FIELDS",
            )

        source_domain = normalize_domain(lead.source_url)
        target_domain = normalize_domain(lead.target_url)
        if (
            source_domain in self.project_profiles.skip_outbound_source_domains
            and target_domain
            and target_domain != source_domain
        ):
            return _require_action(
                result,
                "This is an owned-site outbound partner link and is outside the audit scope; uncheck its audit-selection box.",
                "OWNED_SITE_OUTBOUND",
            )

        profile = self.project_profiles.resolve_for_lead(lead.project, self.project)
        canonical_project = profile.canonical_id if profile else ""
        prefilter = apply_pre_filters(
            lead, self.settings.do_not_contact_domains, seen_keys,
            canonical_project=canonical_project, duplicate_mode=self.settings.duplicate_mode,
        )
        key = duplicate_key(lead, canonical_project, self.settings.duplicate_mode)
        if key:
            seen_keys.add(key)

        if prefilter.decision is Decision.REJECT:
            return _require_action(result, prefilter.reason, *prefilter.risk_flags)
        contact_note = prefilter.reason

        link = self._link_check(lead)
        _apply_link(result, link)
        result.risk_flags = list(dict.fromkeys([*result.risk_flags, *prefilter.risk_flags]))

        if not link.status.startswith("ACTIVE_"):
            return _require_action(
                result,
                _link_failure_instruction(link),
                "LINK_CHECK_FAILED",
            )

        if profile is None:
            return _require_action(
                result,
                f"Project value {lead.project or self.project or '(empty)'} is not configured. Correct it and retry.",
                "UNKNOWN_PROJECT",
            )

        context = self._llm_context(lead, link, profile)
        fingerprint = context_hash(context)
        domain = normalize_domain(lead.domain)
        cached = self.cache.get(domain, self.campaign_id, self.settings.prompt_version, profile.canonical_id, fingerprint)
        if cached is not None:
            self._apply_ai(result, cached, link, profile)
            result.cache_hit = True
            _apply_contact_guidance(result, contact_note)
            return result

        if self.dry_run:
            result.audit_decision = Decision.REVIEW
            result.reasons = ["Dry-run: link checking completed and the AI evaluation was intentionally skipped."]
            result.risk_flags = list(dict.fromkeys([*result.risk_flags, "DRY_RUN"]))
            result.next_human_action = "Review this preview; rerun without --dry-run for AI scoring."
            _apply_contact_guidance(result, contact_note)
            return result

        if self.ai_calls >= self.max_ai_calls:
            return _require_action(
                result,
                f"AI call limit ({self.max_ai_calls}) was reached. Increase MAX_AI_CALLS or select fewer records.",
                "AI_BUDGET_REACHED",
            )

        try:
            self.ai_calls += 1
            ai = LLMDecision.model_validate(self.llm_client.evaluate(context))
            self._apply_ai(result, ai, link, profile)
            self.cache.set(domain, self.campaign_id, self.settings.prompt_version, profile.canonical_id, fingerprint, ai)
        except Exception as exc:
            LOGGER.exception("LLM evaluation failed for record_id=%s domain=%s", record_id, domain)
            return _require_action(
                result,
                f"AI analysis failed: {_safe_error('LLM error', exc)} Retry later.",
                "LLM_ERROR",
            )
        _apply_contact_guidance(result, contact_note)
        return result

    def _apply_ai(self, result: AuditResult, ai: LLMDecision, link: LinkCheck, profile: ProjectProfile) -> None:
        score = ai.relevance_breakdown.total
        flags = set(result.risk_flags) | set(ai.risk_flags)
        if ai.better_fit_project:
            better = self.project_profiles.resolve(ai.better_fit_project)
            if better and better.canonical_id != profile.canonical_id:
                flags.add("WRONG_BRAND_MATCH")
                result.better_fit_project = better.canonical_id
        result.relevance_score = score
        result.relevance_level = relevance_level_for_score(
            score,
            relevant_min_score=self.settings.relevant_min_score,
            relevant_with_limitations_min_score=self.settings.relevant_with_limitations_min_score,
            weak_min_score=self.settings.weak_min_score,
        )
        result.relevance_breakdown = ai.relevance_breakdown.model_dump()
        result.confidence = ai.confidence
        result.reasons = ai.reasons
        result.risk_flags = sorted(flags)
        result.personalization_fact = ai.personalization_fact
        result.outreach_angle = ai.outreach_angle
        result.email_subject = ai.email_subject
        result.email_draft = ai.email_draft
        result.next_human_action = ai.next_human_action
        result.audit_decision = _final_decision(
            ai,
            link,
            flags,
            reject_score_below=self.settings.reject_score_below,
            accept_score_at_least=self.settings.accept_score_at_least,
            min_accept_confidence=self.settings.min_accept_confidence,
        )
        if not ai.personalization_fact.strip():
            result.email_subject = ""
            result.email_draft = ""
            result.next_human_action = "Verify a specific personalization fact before drafting outreach."

    def process_records(self, records: list[dict[str, Any]]) -> tuple[list[AuditResult], RunSummary]:
        seen: set[str] = set()
        summary = RunSummary(records_read=len(records))
        results: list[AuditResult] = []
        for raw in records:
            result = self.process_row(raw, seen)
            results.append(result)
            summary.add(result, raw)
        summary.new_ai_calls = self.ai_calls
        return results, summary

    def process_csv(self, input_path: Path, output_path: Path, mapping: dict[str, str], limit: int | None = None) -> tuple[list[AuditResult], RunSummary]:
        if not input_path.exists():
            raise FileNotFoundError(f"Input CSV not found: {input_path}")
        frame = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if limit is not None:
            frame = frame.head(limit).copy()
        inverse = {external: internal for internal, external in mapping.items() if internal in INPUT_FIELDS}
        records = []
        for _, row in frame.iterrows():
            original = row.to_dict()
            records.append({internal: original.get(internal, original.get(mapping[internal], "")) for internal in INPUT_FIELDS})
        results, summary = self.process_records(records)
        output = frame.reset_index(drop=True).copy()
        for internal in OUTPUT_FIELDS:
            values = [
                "" if result.skipped else result.model_dump(mode="json")[internal]
                for result in results
            ]
            output[mapping.get(internal, internal)] = [json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (list, dict)) else v for v in values]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output.to_csv(output_path, index=False, encoding="utf-8-sig")
        summary.backup_path = str(output_path)
        return results, summary


def write_backup(records: list[dict[str, Any]], results: list[AuditResult], path: Path, mapping: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for raw, result in zip(records, results, strict=True):
        row = {mapping.get(key, key): value for key, value in raw.items() if key != "record_id"}
        row["record_id"] = result.record_id
        if result.skipped:
            for key in OUTPUT_FIELDS:
                row[mapping.get(key, key)] = ""
        else:
            for key, value in result.model_dump(mode="json", exclude={"record_id"}).items():
                row[mapping.get(key, key)] = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
