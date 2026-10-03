from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .airtable_client import AirtableClient, AirtableError
from .cache import ResultCache
from .config import Settings, load_field_mapping
from .llm_client import OutreachLLMClient
from .models import OUTPUT_FIELDS
from .processor import LeadProcessor, RunSummary, write_backup
from .project_profiles import ProjectProfiles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="AI Outreach Link Auditor",
        description="Audit existing links and create manual-review drafts. This tool never sends email.",
    )
    parser.add_argument("--source", choices=("airtable", "csv"), default="airtable")
    parser.add_argument("--input", type=Path, help="Input CSV path when --source csv.")
    parser.add_argument("--output", type=Path, help="CSV result/preview path.")
    parser.add_argument("--campaign-id", default="default", help="Stable cache namespace.")
    parser.add_argument("--project", default="", help="Fallback project; each record's Project wins.")
    parser.add_argument("--limit", type=int, help="Process at most N records.")
    parser.add_argument("--record-id", help="Process one Airtable record from the configured View.")
    parser.add_argument("--selected-only", action="store_true", help="Process only records checked in the configured audit-selection field.")
    parser.add_argument("--max-ai-calls", type=int, help="Override MAX_AI_CALLS.")
    parser.add_argument("--dry-run", action="store_true", help="No OpenAI calls and no Airtable writes.")
    parser.add_argument("--skip-link-check", action="store_true", help="Make no page HTTP requests.")
    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument("--airtable-write", action="store_true", help="Explicitly allow result writes to Airtable.")
    write_group.add_argument("--no-airtable-write", action="store_true", help="Never write results to Airtable.")
    return parser


def _processor(settings: Settings, args: argparse.Namespace, profiles: ProjectProfiles, cache: ResultCache) -> LeadProcessor:
    llm = None
    if not args.dry_run:
        llm = OutreachLLMClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_output_tokens=settings.max_output_tokens,
            system_prompt_path=settings.system_prompt_path,
        )
    return LeadProcessor(
        settings, campaign_id=args.campaign_id, project_profiles=profiles,
        llm_client=llm, cache=cache, project=args.project, dry_run=args.dry_run,
        max_ai_calls=args.max_ai_calls, skip_link_check=args.skip_link_check,
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run(args: argparse.Namespace, settings: Settings | None = None) -> RunSummary:
    settings = settings or Settings.from_env()
    selected_only = bool(getattr(args, "selected_only", False))
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1.")
    if args.max_ai_calls is not None and args.max_ai_calls < 1:
        raise ValueError("--max-ai-calls must be at least 1.")
    if args.record_id and args.source != "airtable":
        raise ValueError("--record-id can only be used with --source airtable.")
    if selected_only and args.source != "airtable":
        raise ValueError("--selected-only can only be used with --source airtable.")
    if selected_only and args.record_id:
        raise ValueError("Use either --selected-only or --record-id, not both.")
    mapping = load_field_mapping(settings.airtable_fields_path)
    profiles = ProjectProfiles(settings.project_profiles_path)

    with ResultCache(settings.cache_path) as cache:
        processor = _processor(settings, args, profiles, cache)
        if args.source == "csv":
            if args.input is None or args.output is None:
                raise ValueError("CSV mode requires --input and --output.")
            _, summary = processor.process_csv(args.input, args.output, mapping, args.limit)
            return summary

        settings.validate_airtable()
        client = AirtableClient(
            access_token=settings.airtable_access_token, base_id=settings.airtable_base_id,
            table_name=settings.airtable_table_name, field_mapping=mapping,
        )
        if args.record_id:
            records = [client.get_record(view=settings.airtable_view_name, record_id=args.record_id)]
        elif selected_only:
            selected_field = mapping["audit_selected"]
            records = client.list_records(
                view=None,
                limit=args.limit,
                filter_formula=f"{{{selected_field}}}=1",
            )
            if not records:
                raise ValueError(f"No records are checked in the Airtable field {selected_field!r}.")
        else:
            records = client.list_records(view=settings.airtable_view_name, limit=args.limit)
        results, summary = processor.process_records(records)
        output_path = args.output or settings.project_root / "output" / f"audit_backup_{_timestamp()}.csv"
        try:
            write_backup(records, results, output_path, mapping)
            summary.backup_path = str(output_path)
        except Exception as exc:
            raise OSError(f"Output backup could not be written to {output_path}: {exc}") from exc

        should_write = (
            (settings.airtable_write_results or args.airtable_write)
            and not args.no_airtable_write
            and not args.dry_run
        )
        if should_write:
            available: set[str] | None = None
            if settings.airtable_validate_schema:
                available = client.table_field_names()
                missing = sorted(mapping[name] for name in OUTPUT_FIELDS if mapping[name] not in available)
                if missing:
                    print("Missing Airtable output fields (create them manually; schema was not changed): " + ", ".join(missing), file=sys.stderr)
            summary.airtable_updates, failures = client.update_records(
                results,
                available_fields=available,
                clear_selection=selected_only,
            )
            summary.airtable_write_failures = len(failures)
            if failures:
                print("Some Airtable updates failed. Their record IDs and complete results remain in the backup CSV.", file=sys.stderr)
        return summary


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = Settings.from_env()
        logging.basicConfig(
            level=getattr(logging, settings.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        summary = run(args, settings)
        print(summary.display())
        print("Email safety: drafts only; this application never sends or schedules email.")
        if summary.action_messages:
            print("\nWHAT TO FIX:")
            for number, message in enumerate(summary.action_messages, start=1):
                print(f"{number}. {message}")
            print("After correcting the records, leave Audit Selected enabled and run the audit again.")
        return 3 if summary.action_required else 0
    except (FileNotFoundError, ValueError, AirtableError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Correct the issue above and retry. No Airtable result was written by this failed run.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled. No email was sent.", file=sys.stderr)
        return 130
    except Exception as exc:
        logging.exception("Unexpected failure")
        print(f"UNEXPECTED ERROR ({type(exc).__name__}): {exc}", file=sys.stderr)
        print("No email was sent. Any completed local backup remains available.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
