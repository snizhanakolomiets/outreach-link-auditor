from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from src.airtable_client import AirtableClient, AirtableError
from src.main import run
from src.models import AuditResult, Decision, RelevanceLevel


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}

    def json(self):
        return self._data


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def client(mapping, session, **kwargs):
    return AirtableClient(
        access_token="secret-token", base_id="appTest", table_name="Links",
        field_mapping=mapping, session=session, sleep=lambda _: None, **kwargs,
    )


def result(record_id: str) -> AuditResult:
    return AuditResult(
        record_id=record_id, audit_decision=Decision.REVIEW,
        reasons=["Review"], risk_flags=["DRY_RUN"],
        processed_at="2026-01-01T00:00:00Z", prompt_version="v2",
    )


def test_airtable_pagination_and_limit(mapping) -> None:
    session = Session(
        Response(data={"records": [{"id": "rec1", "fields": {"Domain": "one.com"}}], "offset": "next"}),
        Response(data={"records": [{"id": "rec2", "fields": {"Domain": "two.com"}}]}),
    )
    rows = client(mapping, session).list_records(view="Audit queue")
    assert [row["record_id"] for row in rows] == ["rec1", "rec2"]
    assert session.calls[1][2]["params"]["offset"] == "next"
    assert all(call[2]["params"]["view"] == "Audit queue" for call in session.calls)


def test_airtable_limit_stops_pagination(mapping) -> None:
    session = Session(Response(data={"records": [{"id": "rec1", "fields": {}}], "offset": "unused"}))
    rows = client(mapping, session).list_records(view="Audit queue", limit=1)
    assert len(rows) == 1
    assert len(session.calls) == 1


def test_selected_records_can_be_read_without_view_position(mapping) -> None:
    session = Session(Response(data={"records": [{"id": "rec1", "fields": {"Audit Selected": True}}]}))
    rows = client(mapping, session).list_records(
        view=None,
        filter_formula="{Audit Selected}=1",
    )
    assert rows[0]["audit_selected"] is True
    params = session.calls[0][2]["params"]
    assert "view" not in params
    assert params["filterByFormula"] == "{Audit Selected}=1"


def test_get_record_is_restricted_to_configured_view(mapping) -> None:
    session = Session(Response(data={"records": [{"id": "recAbc1234567890", "fields": {"Domain": "one.com"}}]}))
    row = client(mapping, session).get_record(view="Audit queue", record_id="recAbc1234567890")
    assert row["record_id"] == "recAbc1234567890"
    params = session.calls[0][2]["params"]
    assert params["view"] == "Audit queue"
    assert params["filterByFormula"] == "RECORD_ID()='recAbc1234567890'"


def test_get_record_rejects_invalid_id_without_request(mapping) -> None:
    session = Session()
    with pytest.raises(ValueError, match="record ID"):
        client(mapping, session).get_record(view="Audit queue", record_id="not a record")
    assert session.calls == []


def test_airtable_read_error_is_beginner_friendly_and_secret_safe(mapping) -> None:
    with pytest.raises(AirtableError) as caught:
        client(mapping, Session(Response(status=404)), max_retries=0).list_records(view="Missing")
    assert "not found" in str(caught.value)
    assert "secret-token" not in str(caught.value)


def test_429_and_5xx_are_retried(mapping) -> None:
    sleeps = []
    session = Session(
        Response(status=429, headers={"Retry-After": "0"}),
        Response(status=503),
        Response(data={"records": []}),
    )
    api = AirtableClient(
        access_token="secret", base_id="appTest", table_name="Links",
        field_mapping=mapping, session=session, sleep=sleeps.append, max_retries=3,
    )
    assert api.list_records(view="v") == []
    assert sleeps == [0.0, 2.0]


def test_updates_are_batched_by_ten_and_allowlisted(mapping) -> None:
    session = Session(Response(), Response())
    api = client(mapping, session)
    succeeded, failures = api.update_records([result(f"rec{i}") for i in range(12)])
    assert (succeeded, failures) == (12, [])
    assert [len(call[2]["json"]["records"]) for call in session.calls] == [10, 2]
    fields = session.calls[0][2]["json"]["records"][0]["fields"]
    assert set(fields) <= set(mapping.values())
    assert "Domain" not in fields
    assert "Email Draft" not in fields  # empty results never overwrite existing values


def test_skipped_partner_link_is_not_written(mapping) -> None:
    session = Session()
    skipped = result("rec1")
    skipped.skipped = True
    succeeded, failures = client(mapping, session).update_records([skipped])
    assert (succeeded, failures) == (0, [])
    assert session.calls == []


def test_successful_selected_record_is_unchecked_after_write(mapping) -> None:
    session = Session(Response())
    succeeded, failures = client(mapping, session).update_records(
        [result("rec1")],
        clear_selection=True,
    )
    assert (succeeded, failures) == (1, [])
    fields = session.calls[0][2]["json"]["records"][0]["fields"]
    assert fields["Audit Selected"] is False


def test_successful_result_clears_stale_error_flags_and_drafts(mapping) -> None:
    api = client(mapping, Session())
    successful = result("rec1")
    successful.relevance_level = RelevanceLevel.WEAK
    successful.relevance_score = 55
    successful.risk_flags = []
    fields = api._write_fields(successful, set(mapping.values()))
    assert fields["Risk Flags"] == "[]"
    assert fields["Audit Error"] == ""
    assert fields["Email Draft"] == ""


def test_write_failure_preserves_other_batches(mapping) -> None:
    session = Session(Response(), Response(status=500))
    succeeded, failures = client(mapping, session, max_retries=0).update_records([result(f"rec{i}") for i in range(12)])
    assert succeeded == 10
    assert failures == ["rec10", "rec11"]


def test_schema_reports_missing_output_fields(mapping) -> None:
    session = Session(Response(data={"tables": [{"id": "tbl1", "name": "Links", "fields": [{"name": "Audit Decision"}]}]}))
    missing = client(mapping, session).missing_output_fields()
    assert "Email Draft" in missing
    assert "Audit Decision" not in missing


def test_dry_run_makes_zero_airtable_writes(settings, mapping, monkeypatch, tmp_path) -> None:
    class FakeAirtable:
        writes = 0
        def __init__(self, **kwargs): pass
        def list_records(self, **kwargs): return []
        def update_records(self, *args, **kwargs):
            FakeAirtable.writes += 1
            return 0, []
    monkeypatch.setattr("src.main.AirtableClient", FakeAirtable)
    args = Namespace(
        source="airtable", input=None, output=tmp_path / "preview.csv", campaign_id="c",
        project="example-saas", limit=5, record_id=None, max_ai_calls=None, dry_run=True,
        skip_link_check=True, airtable_write=False, no_airtable_write=False,
    )
    summary = run(args, settings)
    assert FakeAirtable.writes == 0
    assert summary.records_read == 0
    assert (tmp_path / "preview.csv").exists()
