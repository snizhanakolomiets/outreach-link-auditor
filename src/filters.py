from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import Decision, LeadRow


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_domain(value: str) -> str:
    parsed = urlparse(value.strip() if "://" in value else f"https://{value.strip()}")
    return (parsed.hostname or "").casefold().removeprefix("www.").rstrip(".")


@dataclass(frozen=True)
class FilterResult:
    decision: Decision | None = None
    reason: str = ""
    risk_flags: tuple[str, ...] = ()


def duplicate_key(lead: LeadRow, canonical_project: str, mode: str) -> str:
    domain = normalize_domain(lead.domain)
    if mode == "domain":
        return domain
    return "|".join(
        [lead.source_url.strip().casefold(), lead.target_url.strip().casefold(), canonical_project]
    )


def apply_pre_filters(
    lead: LeadRow,
    blocked_domains: frozenset[str],
    seen_keys: set[str],
    *,
    canonical_project: str = "",
    duplicate_mode: str = "placement",
) -> FilterResult:
    domain = normalize_domain(lead.domain)
    status = lead.previous_contact_status.strip().upper()

    if not domain:
        return FilterResult(Decision.REJECT, "Required field Domain is empty.", ("MISSING_DOMAIN",))
    if not lead.source_url.strip():
        return FilterResult(Decision.REJECT, "Required field Source URL is empty.", ("MISSING_URL",))
    if domain in blocked_domains:
        return FilterResult(Decision.REJECT, "Domain is on the do-not-contact list.", ("DO_NOT_CONTACT",))
    if status in {"UNSUBSCRIBE", "REJECTED"}:
        return FilterResult(Decision.REJECT, f"Previous Contact Status is {status}.", (status,))

    key = duplicate_key(lead, canonical_project, duplicate_mode)
    if key and key in seen_keys:
        return FilterResult(Decision.REJECT, "Duplicate audit item in this run.", ("DUPLICATE_DOMAIN",))

    if not lead.contact_email.strip():
        return FilterResult(
            None,
            "Contact Email is empty; a human must find and verify it.",
        )
    if not EMAIL_RE.fullmatch(lead.contact_email.strip()):
        return FilterResult(
            None,
            "Contact Email format is invalid and needs manual verification.",
        )
    return FilterResult()
