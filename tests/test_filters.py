import pytest

from src.filters import apply_pre_filters, duplicate_key, normalize_domain
from src.models import Decision, LeadRow


def run(row: dict[str, str], *, blocked=frozenset(), seen=None, mode="placement"):
    lead = LeadRow.model_validate(row)
    return apply_pre_filters(lead, blocked, seen or set(), canonical_project="example-saas", duplicate_mode=mode)


def test_normalize_domain() -> None:
    assert normalize_domain("https://www.Example.com/path") == "example.com"


@pytest.mark.parametrize(
    ("change", "decision", "flag"),
    [
        ({"domain": ""}, Decision.REJECT, "MISSING_DOMAIN"),
        ({"source_url": ""}, Decision.REJECT, "MISSING_URL"),
        ({"domain": "blocked.com"}, Decision.REJECT, "DO_NOT_CONTACT"),
        ({"previous_contact_status": "UNSUBSCRIBE"}, Decision.REJECT, "UNSUBSCRIBE"),
        ({"previous_contact_status": "REJECTED"}, Decision.REJECT, "REJECTED"),
        ({"contact_email": ""}, None, None),
        ({"contact_email": "broken"}, None, None),
    ],
)
def test_non_ai_filters(valid_row, change, decision, flag) -> None:
    row = {**valid_row, **change}
    result = run(row, blocked=frozenset({"blocked.com"}))
    assert result.decision is decision
    if flag is None:
        assert result.risk_flags == ()
        assert result.reason
    else:
        assert flag in result.risk_flags


def test_duplicate_placement_is_rejected(valid_row) -> None:
    lead = LeadRow.model_validate(valid_row)
    key = duplicate_key(lead, "example-saas", "placement")
    result = apply_pre_filters(lead, frozenset(), {key}, canonical_project="example-saas")
    assert result.decision is Decision.REJECT
    assert "DUPLICATE_DOMAIN" in result.risk_flags


def test_same_domain_different_placement_allowed_by_default(valid_row) -> None:
    lead = LeadRow.model_validate(valid_row)
    other = LeadRow.model_validate({**valid_row, "source_url": "https://example.com/other"})
    seen = {duplicate_key(lead, "example-saas", "placement")}
    assert apply_pre_filters(other, frozenset(), seen, canonical_project="example-saas").decision is None


def test_domain_duplicate_mode(valid_row) -> None:
    lead = LeadRow.model_validate(valid_row)
    result = apply_pre_filters(lead, frozenset(), {"example.com"}, canonical_project="example-saas", duplicate_mode="domain")
    assert result.decision is Decision.REJECT
