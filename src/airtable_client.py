from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import quote

import requests
from pydantic import BaseModel, ConfigDict, Field

from .models import INPUT_FIELDS, OUTPUT_FIELDS, AuditResult


LOGGER = logging.getLogger(__name__)


class AirtableRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    fields: dict[str, Any] = Field(default_factory=dict)


class AirtablePage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    records: list[AirtableRecord]
    offset: str | None = None


class AirtableError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


class AirtableClient:
    def __init__(
        self,
        *,
        access_token: str,
        base_id: str,
        table_name: str,
        field_mapping: dict[str, str],
        session: requests.Session | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not access_token:
            raise ValueError("AIRTABLE_ACCESS_TOKEN is missing.")
        self._token = access_token
        self.base_id = base_id
        self.table_name = table_name
        self.mapping = field_mapping
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.table_url = f"https://api.airtable.com/v0/{quote(base_id, safe='')}/{quote(table_name, safe='')}"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(method, url, headers=self.headers, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise AirtableError(f"Airtable request failed ({type(exc).__name__}).") from exc
                self.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    delay = float(response.headers.get("Retry-After", 30 if response.status_code == 429 else 2 ** attempt))
                    LOGGER.warning("Airtable returned HTTP %s; retrying after %.1f seconds.", response.status_code, delay)
                    self.sleep(delay)
                    continue
            if response.status_code >= 400:
                if response.status_code in {401, 403}:
                    message = "Airtable authorization failed. Check the token scopes and base access."
                elif response.status_code == 404:
                    message = "Airtable Base, table, or View was not found. Check AIRTABLE_* settings."
                elif response.status_code == 422:
                    message = "Airtable rejected the request (HTTP 422). Check the table, View, and field mapping names."
                elif response.status_code == 429:
                    message = "Airtable rate limit persisted after retries. Try again later."
                else:
                    message = f"Airtable request failed with HTTP {response.status_code}."
                raise AirtableError(message, status_code=response.status_code)
            return response
        raise AirtableError("Airtable request failed after retries.")

    def list_records(
        self,
        *,
        view: str | None,
        limit: int | None = None,
        filter_formula: str | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset: str | None = None
        while limit is None or len(records) < limit:
            remaining = None if limit is None else limit - len(records)
            params: dict[str, Any] = {"pageSize": min(100, remaining) if remaining else 100}
            if view:
                params["view"] = view
            if filter_formula:
                params["filterByFormula"] = filter_formula
            if offset:
                params["offset"] = offset
            response = self._request("GET", self.table_url, params=params)
            try:
                page = AirtablePage.model_validate(response.json())
            except (ValueError, TypeError) as exc:
                raise AirtableError("Airtable returned an invalid records response.") from exc
            for record in page.records:
                mapped = {internal: record.fields.get(external, "") for internal, external in self.mapping.items() if internal in INPUT_FIELDS}
                mapped["record_id"] = record.id
                records.append(mapped)
                if limit is not None and len(records) >= limit:
                    break
            offset = page.offset
            if not offset:
                break
        return records

    def get_record(self, *, view: str, record_id: str) -> dict[str, Any]:
        """Read one record only when it belongs to the configured Airtable View."""
        if not re.fullmatch(r"rec[A-Za-z0-9]{6,61}", record_id):
            raise ValueError("Airtable record ID must start with 'rec' and contain only letters and numbers.")
        response = self._request(
            "GET",
            self.table_url,
            params={
                "view": view,
                "filterByFormula": f"RECORD_ID()='{record_id}'",
                "pageSize": 1,
                "maxRecords": 1,
            },
        )
        try:
            page = AirtablePage.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise AirtableError("Airtable returned an invalid records response.") from exc
        if not page.records:
            raise AirtableError(f"Airtable record {record_id} was not found in the configured View.")
        record = page.records[0]
        mapped = {
            internal: record.fields.get(external, "")
            for internal, external in self.mapping.items()
            if internal in INPUT_FIELDS
        }
        mapped["record_id"] = record.id
        return mapped

    def table_field_names(self) -> set[str]:
        url = f"https://api.airtable.com/v0/meta/bases/{quote(self.base_id, safe='')}/tables"
        response = self._request("GET", url)
        try:
            tables = response.json()["tables"]
            table = next(item for item in tables if item.get("id") == self.table_name or item.get("name") == self.table_name)
            return {field["name"] for field in table["fields"]}
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise AirtableError("Airtable table schema could not be read or the configured table was not found.") from exc

    def missing_output_fields(self) -> list[str]:
        available = self.table_field_names()
        return sorted(self.mapping[name] for name in OUTPUT_FIELDS if self.mapping[name] not in available)

    def _write_fields(self, result: AuditResult, available_fields: set[str] | None) -> dict[str, Any]:
        raw = result.model_dump(mode="json", exclude={"record_id"})
        fields: dict[str, Any] = {}
        incomplete = (
            bool(result.audit_error)
            or result.relevance_level.value == "UNKNOWN"
            or bool(set(result.risk_flags) & {"LLM_ERROR", "DRY_RUN", "AI_BUDGET_REACHED", "UNKNOWN_PROJECT"})
        )
        clearable_after_success = {
            "risk_flags", "better_fit_project", "personalization_fact", "outreach_angle",
            "email_subject", "email_draft", "audit_error",
        }
        for internal in OUTPUT_FIELDS:
            external = self.mapping[internal]
            if available_fields is not None and external not in available_fields:
                continue
            value = raw[internal]
            if value in ("", None, [], {}):
                if incomplete or internal not in clearable_after_success:
                    continue
                value = "[]" if internal == "risk_flags" else ""
            if isinstance(value, (list, dict)):
                import json
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            fields[external] = value
        return fields

    def update_records(
        self,
        results: list[AuditResult],
        *,
        available_fields: set[str] | None = None,
        clear_selection: bool = False,
    ) -> tuple[int, list[str]]:
        updates = []
        for result in results:
            if not result.record_id or result.skipped:
                continue
            fields = self._write_fields(result, available_fields)
            selected_field = self.mapping.get("audit_selected", "")
            if clear_selection and selected_field and (
                available_fields is None or selected_field in available_fields
            ):
                fields[selected_field] = False
            updates.append({"id": result.record_id, "fields": fields})
        succeeded = 0
        failures: list[str] = []
        for batch in _chunks(updates, 10):
            try:
                self._request("PATCH", self.table_url, json={"records": batch, "typecast": False})
                succeeded += len(batch)
            except AirtableError as exc:
                ids = [item["id"] for item in batch]
                failures.extend(ids)
                LOGGER.error("Airtable update failed for record IDs %s: %s", ", ".join(ids), exc)
        return succeeded, failures
