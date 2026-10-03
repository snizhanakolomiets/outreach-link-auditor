# AI Link Auditor: Team Onboarding

This is the short guide to share with a teammate. The complete installation, configuration, and troubleshooting guide is in [README.md](README.md).

## What the project owner gives you

- access to the private GitHub repository;
- access to the approved Airtable Base;
- the approved Base, table, and view names or IDs;
- the allowed names for the `Project` field;
- instructions for obtaining your own OpenAI API key and Airtable Personal Access Token.

The owner should not send you a completed `.env` file or another person's API keys.

## What you create yourself

1. An OpenAI API key in the approved OpenAI project.
2. An Airtable Personal Access Token with:
   - `data.records:read`
   - `data.records:write`
   - `schema.bases:read`
3. A local `.env` copied from `.env.example`.

Never commit or share `.env`.

## One-time setup

1. Install Python 3.12 and Google Chrome.
2. Clone the private repository or download its trusted ZIP.
3. Create `.venv` and install `requirements.txt` by following the operating-system instructions in the README.
4. Copy `.env.example` to `.env` and add your own credentials.
5. Run `python -m pytest -q`.

## Normal audit workflow

1. Open Airtable and go to `Add Links`.
2. Complete `Article URL`, `Link URL`, and `Project`.
3. Enable `Audit Selected` only for the records you want to process.
4. On macOS, open `AI Link Auditor.command`.
5. Choose option `6` to analyze selected records without changing Airtable.
6. Review the terminal summary and CSV in `output/`.
7. Fix any records listed in the final corrective-action block.
8. Choose option `7` and confirm with `1` to save successful results.
9. Verify the records in `Audit Results`.

On Windows, use:

```powershell
python -m src.main --source airtable --selected-only --no-airtable-write
python -m src.main --source airtable --selected-only --airtable-write
```

## Safety behavior

- The application never sends or schedules email.
- An unsuccessful audit does not fill audit fields with `UNKNOWN` or partial data.
- Failed records remain selected and the terminal explains what must be fixed.
- Missing contact information is guidance in `Next Human Action`, not a `Risk Flag`.
- The detected anchor is written to the existing `Link Anchor Text` field.
- A blocked public page may open in a clean temporary Chrome profile; personal Chrome data is not used.

If you are unsure, stop after no-write mode and ask the project owner to review the CSV before you run write mode.
