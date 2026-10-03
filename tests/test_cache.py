from pathlib import Path

from src.cache import ResultCache
from src.models import LLMDecision, RelevanceBreakdown


def decision(reason="ok") -> LLMDecision:
    return LLMDecision(
        confidence=.9, reasons=[reason], risk_flags=[],
        relevance_breakdown=RelevanceBreakdown(
            article_topic_fit=24, surrounding_context_fit=18, landing_page_fit=18,
            audience_fit=14, site_topic_fit=9, anchor_text_fit=9,
        ),
        personalization_fact="Verified article topic.", outreach_angle="Thank the editor.",
        email_subject="Draft subject", email_draft="A short draft.",
        next_human_action="Review manually.",
    )


def test_cache_hit_requires_matching_context_and_project(tmp_path: Path) -> None:
    with ResultCache(tmp_path / "cache.sqlite3") as cache:
        cache.set("example.com", "campaign", "v2", "example-saas", "hash-a", decision())
        assert cache.get("example.com", "campaign", "v2", "example-saas", "hash-a") is not None
        assert cache.get("example.com", "campaign", "v2", "example-saas", "hash-b") is None
        assert cache.get("example.com", "campaign", "v2", "example-agency", "hash-a") is None


def test_context_change_replaces_old_cache_entry(tmp_path: Path) -> None:
    with ResultCache(tmp_path / "cache.sqlite3") as cache:
        cache.set("example.com", "c", "v2", "example-saas", "old", decision("old"))
        cache.set("example.com", "c", "v2", "example-saas", "new", decision("new"))
        assert cache.get("example.com", "c", "v2", "example-saas", "old") is None
        assert cache.get("example.com", "c", "v2", "example-saas", "new").reasons == ["new"]
