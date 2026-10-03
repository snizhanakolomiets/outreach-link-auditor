from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from src.cache import ResultCache
from src.models import OUTPUT_FIELDS, Decision, LLMDecision, LinkCheck, RelevanceBreakdown, RelevanceLevel, relevance_level_for_score
from src.processor import LeadProcessor
from src.project_profiles import ProjectProfiles


ROOT = Path(__file__).resolve().parents[1]


def breakdown(total: int) -> RelevanceBreakdown:
    maxima = [25, 20, 20, 15, 10, 10]
    values = []
    left = total
    for maximum in maxima:
        value = min(maximum, left)
        values.append(value)
        left -= value
    return RelevanceBreakdown(
        article_topic_fit=values[0], surrounding_context_fit=values[1],
        landing_page_fit=values[2], audience_fit=values[3], site_topic_fit=values[4],
        anchor_text_fit=values[5],
    )


def ai_result(score=90, **changes) -> LLMDecision:
    data = dict(
        confidence=.9, reasons=["Supplied article and context match the selected profile."],
        risk_flags=[], relevance_breakdown=breakdown(score), better_fit_project="",
        personalization_fact="The supplied context discusses creator partnerships.",
        outreach_angle="Reference that verified topic.", email_subject="Draft follow-up",
        email_draft="Hi Alex, I appreciated the creator partnership context. Please review whether our linked resource still fits.",
        next_human_action="Review and approve or discard this draft manually.",
    )
    data.update(changes)
    return LLMDecision(**data)


class FakeLLM:
    def __init__(self, value=None, error=None):
        self.value = value or ai_result()
        self.error = error
        self.calls = 0
        self.contexts = []

    def evaluate(self, context):
        self.calls += 1
        self.contexts.append(context)
        if self.error:
            raise self.error
        return self.value


def active_link(*args, **kwargs) -> LinkCheck:
    return LinkCheck(
        status="ACTIVE_FOLLOW", found=True, http_status=200,
        resolved_target_url="https://target.com/page", anchor_text="creator resource",
        nearby_context="Verified creator partnership paragraph.", source_page_title="Creator guide",
        placement_area="main_content",
    )


def processor(settings, cache, llm=None, **kwargs):
    return LeadProcessor(
        settings, campaign_id="campaign", project_profiles=ProjectProfiles(settings.project_profiles_path),
        llm_client=llm or FakeLLM(), cache=cache, link_checker=active_link, **kwargs,
    )


def test_total_score_is_calculated_from_breakdown() -> None:
    assert breakdown(83).total == 83


@pytest.mark.parametrize(
    ("score", "level"),
    [(0, RelevanceLevel.IRRELEVANT), (39, RelevanceLevel.IRRELEVANT),
     (40, RelevanceLevel.WEAK), (59, RelevanceLevel.WEAK),
     (60, RelevanceLevel.RELEVANT_WITH_LIMITATIONS), (79, RelevanceLevel.RELEVANT_WITH_LIMITATIONS),
     (80, RelevanceLevel.RELEVANT), (100, RelevanceLevel.RELEVANT)],
)
def test_score_thresholds(score, level) -> None:
    assert relevance_level_for_score(score) is level


@pytest.mark.parametrize(("score", "decision"), [(39, Decision.REJECT), (40, Decision.REVIEW), (79, Decision.REVIEW), (80, Decision.ACCEPT)])
def test_python_enforces_final_decision(settings, valid_row, tmp_path, score, decision) -> None:
    with ResultCache(tmp_path / f"{score}.sqlite") as cache:
        result = processor(settings, cache, FakeLLM(ai_result(score))).process_row(valid_row, set())
    assert result.relevance_score == score
    assert result.audit_decision is decision


def test_configurable_decision_and_relevance_thresholds(settings, valid_row, tmp_path) -> None:
    settings = dataclasses.replace(
        settings,
        reject_score_below=30,
        accept_score_at_least=90,
        min_accept_confidence=0.95,
        weak_min_score=30,
        relevant_with_limitations_min_score=50,
        relevant_min_score=90,
    )
    with ResultCache(tmp_path / "custom-thresholds.sqlite") as cache:
        result = processor(settings, cache, FakeLLM(ai_result(85))).process_row(valid_row, set())
    assert result.relevance_level is RelevanceLevel.RELEVANT_WITH_LIMITATIONS
    assert result.audit_decision is Decision.REVIEW


def test_email_over_120_words_is_invalid() -> None:
    with pytest.raises(ValidationError):
        ai_result(email_draft="word " * 121)


def test_dry_run_makes_zero_llm_calls(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        result = processor(settings, cache, llm, dry_run=True).process_row(valid_row, set())
    assert llm.calls == 0
    assert result.audit_decision is Decision.REVIEW
    assert "DRY_RUN" in result.risk_flags


def test_unknown_project_skips_llm(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        result = processor(settings, cache, llm).process_row({**valid_row, "project": "Mystery"}, set())
    assert llm.calls == 0
    assert result.relevance_level is RelevanceLevel.UNKNOWN
    assert "UNKNOWN_PROJECT" in result.risk_flags


def test_owned_site_outbound_partner_link_is_untouched(settings, valid_row, tmp_path) -> None:
    link_calls = []
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        p = processor(settings, cache, llm)
        p.link_checker = lambda *args, **kwargs: link_calls.append(1) or active_link()
        result = p.process_row(
            {
                **valid_row,
                "source_url": "https://your-company.example/articles/partner-list/",
                "target_url": "https://partner.example/service",
            },
            set(),
        )
    assert result.skipped is True
    assert result.risk_flags == ["OWNED_SITE_OUTBOUND"]
    assert link_calls == []
    assert llm.calls == 0


def test_owned_site_internal_link_is_still_auditable(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        result = processor(settings, cache, llm).process_row(
            {
                **valid_row,
                "source_url": "https://your-company.example/articles/workflow-guide/",
                "target_url": "https://your-company.example/services/automation/",
            },
            set(),
        )
    assert result.skipped is False
    assert llm.calls == 1


@pytest.mark.parametrize(
    "contact_email",
    ["", "broken"],
)
def test_contact_issue_is_guidance_not_risk(settings, valid_row, tmp_path, contact_email) -> None:
    calls = []
    def checker(*args, **kwargs):
        calls.append(1)
        return active_link()
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        p = processor(settings, cache, llm)
        p.link_checker = checker
        result = p.process_row({**valid_row, "contact_email": contact_email}, set())
    assert calls and llm.calls == 1
    assert result.audit_decision is Decision.ACCEPT
    assert result.relevance_score == 90
    assert result.relevance_level is RelevanceLevel.RELEVANT
    assert "MISSING_CONTACT_EMAIL" not in result.risk_flags
    assert "INVALID_CONTACT_EMAIL" not in result.risk_flags
    assert "Contact Email" in result.next_human_action


def test_wrong_brand_match_is_disclosed(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM(ai_result(better_fit_project="Example Agency"))
    with ResultCache(tmp_path / "c.sqlite") as cache:
        result = processor(settings, cache, llm).process_row(valid_row, set())
    assert result.better_fit_project == "example-agency"
    assert "WRONG_BRAND_MATCH" in result.risk_flags
    assert result.audit_decision is Decision.REVIEW


def test_link_not_found_forces_review_and_flag(settings, valid_row, tmp_path) -> None:
    def missing(*args, **kwargs):
        return LinkCheck(status="NOT_FOUND_IN_HTML", http_status=200)
    with ResultCache(tmp_path / "c.sqlite") as cache:
        p = processor(settings, cache)
        p.link_checker = missing
        result = p.process_row(valid_row, set())
    assert result.audit_decision is Decision.REVIEW
    assert "LINK_NOT_FOUND" in result.risk_flags
    assert result.skipped is True
    assert result.action_required is True


def test_action_summary_contains_record_domain_url_and_fix(settings, valid_row, tmp_path) -> None:
    def blocked(*args, **kwargs):
        return LinkCheck(status="BLOCKED_OR_FORBIDDEN", http_status=403, error="Source page denied access.")

    with ResultCache(tmp_path / "action.sqlite") as cache:
        p = processor(settings, cache)
        p.link_checker = blocked
        _, summary = p.process_records([valid_row])

    assert summary.action_required == 1
    message = summary.action_messages[0]
    assert "rec1" in message
    assert "example.com" in message
    assert valid_row["source_url"] in message
    assert "HTTP 403" in message
    assert "Open the Article URL" in message


def test_403_uses_browser_fallback(settings, valid_row, tmp_path) -> None:
    settings = dataclasses.replace(settings, browser_fallback_enabled=True)
    browser_calls = []
    with ResultCache(tmp_path / "browser.sqlite") as cache:
        p = processor(settings, cache)
        p.link_checker = lambda *args, **kwargs: LinkCheck(
            status="BLOCKED_OR_FORBIDDEN", http_status=403, error="denied"
        )
        p.browser_link_checker = lambda *args, **kwargs: browser_calls.append(kwargs) or active_link()
        result = p.process_row(valid_row, set())

    assert browser_calls
    assert result.link_found is True
    assert result.link_anchor_text == "creator resource"
    assert result.audit_decision is Decision.ACCEPT


def test_missing_required_fields_leave_result_unwritable(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        result = processor(settings, cache, llm).process_row(
            {**valid_row, "source_url": "", "target_url": ""},
            set(),
        )
    assert result.skipped is True
    assert result.action_required is True
    assert "Article URL" in result.reasons[0]
    assert "Link URL" in result.reasons[0]
    assert llm.calls == 0


def test_404_adds_source_page_removed(settings, valid_row, tmp_path) -> None:
    def removed(*args, **kwargs):
        return LinkCheck(status="SOURCE_4XX", http_status=404)
    with ResultCache(tmp_path / "c.sqlite") as cache:
        p = processor(settings, cache)
        p.link_checker = removed
        result = p.process_row(valid_row, set())
    assert "SOURCE_PAGE_REMOVED" in result.risk_flags


def test_llm_failure_and_invalid_response_preserve_record(settings, valid_row, tmp_path) -> None:
    for index, llm in enumerate([FakeLLM(error=RuntimeError("simulated sk-secretvalue")), FakeLLM(value={"unexpected": True})]):
        with ResultCache(tmp_path / f"c{index}.sqlite") as cache:
            result = processor(settings, cache, llm).process_row(valid_row, set())
        assert result.record_id == "rec1"
        assert result.audit_decision is Decision.REVIEW
        assert "LLM_ERROR" in result.risk_flags
        assert "sk-secretvalue" not in result.audit_error
        assert result.skipped is True
        assert result.action_required is True


def test_ai_budget_preserves_remaining_record(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        p = processor(settings, cache, llm, max_ai_calls=1)
        first = p.process_row(valid_row, set())
        second = p.process_row({**valid_row, "record_id": "rec2", "domain": "second.com", "source_url": "https://second.com/a"}, set())
    assert first.audit_decision is Decision.ACCEPT
    assert second.audit_decision is Decision.REVIEW
    assert "AI_BUDGET_REACHED" in second.risk_flags
    assert llm.calls == 1


def test_cache_hit_and_context_change(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    mutable = {"context": "one"}
    def checker(*args, **kwargs):
        link = active_link()
        link.nearby_context = mutable["context"]
        return link
    with ResultCache(tmp_path / "c.sqlite") as cache:
        p = processor(settings, cache, llm)
        p.link_checker = checker
        first = p.process_row(valid_row, set())
        second = p.process_row(valid_row, set())
        mutable["context"] = "changed"
        third = p.process_row(valid_row, set())
    assert not first.cache_hit and second.cache_hit and not third.cache_hit
    assert llm.calls == 2


def test_llm_context_excludes_email_and_contains_project(settings, valid_row, tmp_path) -> None:
    llm = FakeLLM()
    with ResultCache(tmp_path / "c.sqlite") as cache:
        processor(settings, cache, llm).process_row(valid_row, set())
    context = llm.contexts[0]
    assert context["project"]["canonical_id"] == "example-saas"
    assert len(context["project_catalog"]) == 2
    assert any(rule["project"] == "example-agency" for rule in context["project_disambiguation"])
    assert "contact_email" not in context["lead"]


def test_csv_limit_five(settings, valid_row, mapping, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    target = tmp_path / "out.csv"
    rows = [{**valid_row, "domain": f"example{i}.com", "source_url": f"https://example{i}.com/a"} for i in range(7)]
    pd.DataFrame(rows).to_csv(source, index=False, encoding="utf-8-sig")
    with ResultCache(tmp_path / "c.sqlite") as cache:
        _, summary = processor(settings, cache, dry_run=True).process_csv(source, target, mapping, limit=5)
    output = pd.read_csv(target, encoding="utf-8-sig")
    assert summary.records_read == 5
    assert len(output) == 5
    assert "Audit Decision" in output.columns


def test_skipped_partner_link_has_blank_csv_outputs(settings, valid_row, mapping, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    target = tmp_path / "out.csv"
    pd.DataFrame([
        {
            **valid_row,
            "source_url": "https://your-company.example/articles/partner-list/",
            "target_url": "https://partner.example/service",
        }
    ]).to_csv(source, index=False, encoding="utf-8-sig")
    with ResultCache(tmp_path / "c.sqlite") as cache:
        _, summary = processor(settings, cache).process_csv(source, target, mapping)
    output = pd.read_csv(target, encoding="utf-8-sig", keep_default_na=False)
    assert summary.records_processed == 0
    assert summary.records_skipped == 1
    assert all(output.loc[0, mapping[field]] == "" for field in OUTPUT_FIELDS)
