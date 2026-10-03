# AI Outreach Link Auditor

AI Outreach Link Auditor is an internal Python application that checks existing backlinks stored in Airtable, evaluates their quality and relevance, and writes structured audit results back to approved Airtable fields.

The application never sends or schedules email. It may prepare draft text for manual review only.

## Start here: the normal team workflow

1. Add or update link records in the Airtable `Add Links` view.
2. Complete at least `Article URL`, `Link URL`, and `Project`.
3. Enable `Audit Selected` only for the records you want to check.
4. On macOS, double-click `AI Link Auditor.command`.
5. Run menu option `6` first: selected records, analysis only, no Airtable writes.
6. Review the terminal summary and the CSV backup in the `output` folder.
7. If the results are correct, run option `7`: selected records with Airtable writes.
8. Review the completed rows in the Airtable `Audit Results` view.

For one record, use menu option `3` without writes or option `4` with writes. You can paste either an Airtable record ID beginning with `rec` or the full Airtable record URL.

## What the project owner must provide to each teammate

Give every teammate:

- access to the private GitHub repository;
- access to the correct Airtable Base;
- the approved Airtable Base, table, and view names or IDs;
- the required Airtable field names;
- instructions for creating their own Airtable Personal Access Token;
- the team's approved method for obtaining an OpenAI API key;
- the allowed names used in the `Project` field;
- this README and [TEAM_ONBOARDING.md](TEAM_ONBOARDING.md).

Do not send a completed `.env` file through GitHub, Slack, email, or a shared document. Each teammate should keep their own `.env` locally. Never commit API keys or tokens.

## What the application does

For every selected record, the application:

1. validates the required Airtable fields;
2. opens the public article page and looks for the target link;
3. detects whether the link is follow, nofollow, sponsored, or UGC;
4. extracts the anchor text into the existing `Link Anchor Text` field;
5. asks OpenAI to evaluate public page context and project relevance;
6. produces a decision such as `ACCEPT`, `REVIEW`, or `REJECT`;
7. creates a local CSV backup;
8. writes the successful result to Airtable only when write mode is explicitly selected.

If a record cannot be checked, its audit result fields remain unchanged. The terminal explains what needs to be fixed. The record stays selected so it can be retried.

## Data sent to OpenAI

The application may send:

- Article URL;
- target Link URL;
- detected anchor and surrounding public text;
- the matching project profile;
- public page metadata used for relevance analysis.

The application does not intentionally send:

- Airtable or OpenAI API keys;
- private contact details;
- your complete Airtable Base;
- unrelated records.

## Requirements

- macOS or Windows;
- Python 3.12;
- Google Chrome for the optional browser fallback;
- access to the private GitHub repository or a trusted ZIP copy;
- an OpenAI API key with available API balance;
- an Airtable Personal Access Token with access to the intended Base.

## Installation on macOS

### 1. Install Python 3.12

Download the current Python 3.12 installer from [python.org](https://www.python.org/downloads/macos/) and run it.

Verify the installation in Terminal:

```bash
python3.12 --version
```

If the command is not found, close and reopen Terminal after installation.

### 2. Download the project

Preferred method:

```bash
git clone YOUR_PRIVATE_GITHUB_REPOSITORY_URL
cd outreach-link-auditor
```

If Git is not available, download the repository as a ZIP from GitHub, extract it, open Terminal, type `cd ` with a trailing space, drag the extracted folder into Terminal, and press Enter.

### 3. Create the virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

When the environment is active, the Terminal prompt normally begins with `(.venv)`.

### 4. Create the local configuration

```bash
cp .env.example .env
open -e .env
```

Fill in your own keys and the approved Airtable settings, save the file, and close TextEdit.

### 5. Run the tests

```bash
source .venv/bin/activate
python -m pytest -q
```

### 6. Start the application

Double-click `AI Link Auditor.command` in Finder.

If macOS blocks the first launch, right-click the file, choose **Open**, and confirm. If double-clicking does not work, run:

```bash
chmod +x "AI Link Auditor.command"
./AI\ Link\ Auditor.command
```

## Installation on Windows

### 1. Install Python 3.12

Download Python from [python.org](https://www.python.org/downloads/windows/). During installation, enable **Add python.exe to PATH**.

Open PowerShell and verify:

```powershell
py -3.12 --version
```

### 2. Download the project

With Git:

```powershell
git clone YOUR_PRIVATE_GITHUB_REPOSITORY_URL
cd outreach-link-auditor
```

Or download the repository ZIP from GitHub and extract it. In File Explorer, open the extracted folder, click the address bar, type `powershell`, and press Enter.

### 3. Install the project

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation for the current window, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 4. Create the local configuration

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in the values, save, and close Notepad.

### 5. Run the tests

```powershell
python -m pytest -q
```

Windows does not use the macOS `.command` launcher. Use the commands in the Windows usage section below.

## Creating API credentials

### OpenAI API key

1. Open [OpenAI API keys](https://platform.openai.com/api-keys).
2. Sign in to the approved organization or project.
3. Create a new secret key.
4. Copy it immediately and place it only in your local `.env` as `OPENAI_API_KEY`.
5. Confirm that the API project has available billing or credits.

### Airtable Personal Access Token

1. Open [Airtable developer tokens](https://airtable.com/create/tokens).
2. Create a Personal Access Token for this application.
3. Add these scopes:
   - `data.records:read`
   - `data.records:write`
   - `schema.bases:read`
4. Restrict access to the intended Base whenever possible.
5. Copy it into your local `.env` as `AIRTABLE_ACCESS_TOKEN`.

Every teammate should use an individual token when company policy allows it. This makes access revocation and auditing safer.

## Configure `.env`

Minimal example:

```dotenv
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-5-mini

AIRTABLE_ACCESS_TOKEN=your_airtable_token
AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX
AIRTABLE_TABLE_NAME=Links
AIRTABLE_VIEW_NAME=AI Audit Queue
AIRTABLE_WRITE_RESULTS=false
```

Friendly labels are optional and are displayed in the macOS menu:

```dotenv
AIRTABLE_BASE_LABEL=Your Link Audit Base
AIRTABLE_TABLE_LABEL=Links
AIRTABLE_VIEW_LABEL=AI Audit Queue
```

Important settings:

```dotenv
MAX_AI_CALLS=50
PREVIEW_LIMIT=5
BROWSER_FALLBACK_ENABLED=true
BROWSER_FALLBACK_TIMEOUT_SECONDS=25
```

`AIRTABLE_WRITE_RESULTS=false` is the safe default. The explicit `--airtable-write` command or a write menu option is still required before results are written.

## Prepare Airtable

The recommended setup uses one table named `Links` and separate views:

- `Add Links` — data entry and selection;
- `AI Audit Queue` — records available to the application;
- `Audit Results` — processed records and audit output.

`AI Audit Queue` is only a filtered view, not a second table. It keeps the operational queue separate from data entry and results. You may use a differently named view by changing `AIRTABLE_VIEW_NAME`.

Each record should contain:

- `Domain`;
- `Project`;
- `Article URL`;
- `Link URL`;
- `Audit Selected` checkbox.

The application also uses the existing `Link Anchor Text` field for the detected anchor. Do not create an additional `Our anchor` field.

Typical output fields include:

- `Audit Decision`;
- `Relevance Score`;
- `Relevance Level`;
- `Confidence`;
- `Risk Flags`;
- `Link Status`;
- `Link Found`;
- `Link Rel`;
- `Link Anchor Text`;
- `Next Human Action`;
- `Processed At`;
- `Audit Error`.

Exact field mappings are stored in `config/airtable_fields.json`. The application does not create missing Airtable fields automatically. Field types must match the values being written.

Missing contact details are workflow guidance, not a backlink risk. They belong in `Next Human Action`, not `Risk Flags`.

## macOS menu reference

| Option | Action | Airtable writes |
|---|---|---:|
| `1` | Run automated tests | No |
| `2` | Preview the configured number of records without AI | No |
| `3` | Analyze one record | No |
| `4` | Analyze one record and save the result | Yes, after confirmation |
| `5` | Open the local `output` folder | No |
| `6` | Analyze all selected records | No |
| `7` | Analyze all selected records and save successful results | Yes, after confirmation |
| `0` | Exit | No |

## Audit selected Airtable records

1. Open the Airtable `Add Links` view.
2. Fill in the required fields for every intended record.
3. Enable `Audit Selected` only on those records.
4. Start the application.
5. Choose option `6` for a no-write review.
6. Read the summary. If the application reports records requiring action, fix them first.
7. Open the CSV backup with option `5` and review the proposed results.
8. Return to the menu and choose option `7`.
9. Enter `1` when asked to confirm the Airtable write.
10. Open `Audit Results` in Airtable and verify the completed rows.

Successful records are written and normally removed from the selection queue. Failed records are not partially filled and remain selected for retry.

## Audit one Airtable record

1. Open the Airtable record.
2. Copy the full record URL, or copy its ID beginning with `rec`.
3. Choose option `3` to analyze without writing.
4. Paste the record URL or ID and press Enter.
5. Review the CSV and terminal result.
6. Choose option `4` when you are ready to write.
7. Confirm with `1`, then paste the same record URL or ID.

## Windows usage

Activate the environment first:

```powershell
.\.venv\Scripts\Activate.ps1
```

Analyze selected rows without writing:

```powershell
python -m src.main --source airtable --selected-only --no-airtable-write
```

Analyze selected rows and write successful results:

```powershell
python -m src.main --source airtable --selected-only --airtable-write
```

Analyze one record without writing:

```powershell
python -m src.main --source airtable --record-id recXXXXXXXXXXXXXX --max-ai-calls 1 --no-airtable-write
```

Analyze one record and write the result:

```powershell
python -m src.main --source airtable --record-id recXXXXXXXXXXXXXX --max-ai-calls 1 --airtable-write
```

Safe preview without OpenAI or Airtable writes:

```powershell
python -m src.main --source airtable --limit 5 --dry-run
```

## When a record cannot be checked

The application does not fill audit fields with placeholders such as `UNKNOWN` after an unsuccessful attempt. Instead, it:

- leaves the audit fields unchanged;
- lists the record in the final corrective-action block;
- describes the missing or inaccessible item;
- keeps the record selected for a later retry.

Common causes:

- `Article URL` is empty or invalid;
- `Link URL` is empty or invalid;
- `Project` does not match a configured project profile;
- the page returns HTTP 403 or blocks automated access;
- the target link is not present on the page;
- a required Airtable field is missing or has the wrong type;
- the Airtable token lacks permission;
- the OpenAI key is invalid or has no available balance.

Follow the concrete instruction printed for that record, correct the Airtable data, leave `Audit Selected` enabled, and run the audit again.

## Browser fallback for blocked pages

When normal page retrieval is blocked, the application can open installed Google Chrome in a clean temporary profile and retry the public page.

The fallback:

- uses a temporary profile without personal cookies, passwords, or extensions;
- does not use your normal Chrome profile;
- does not bypass CAPTCHA or other access controls;
- blocks private and local network addresses;
- closes automatically after the attempt.

If the public page still cannot be accessed, the record is left unchanged and the terminal explains what to do manually.

## Decisions and risk flags

Typical decisions:

- `ACCEPT` — the link is valid and sufficiently relevant;
- `REVIEW` — the link exists, but a human should evaluate a limitation;
- `REJECT` — the backlink fails an important quality or relevance rule.

Typical risk flags represent real backlink or page risks, for example:

- target link not found;
- nofollow, sponsored, or UGC relationship;
- redirect mismatch;
- weak or irrelevant context;
- unsafe or disallowed destination;
- duplicate placement, depending on configuration.

Missing email or contact information is not a risk flag.

## Project profiles

Project-specific rules are stored in `config/project_profiles.json`. The public repository contains fictional examples only. Replace them with your own profiles before auditing real projects. The Airtable project value must match a configured project name or alias.

Keep client-specific domains, relevance rules, anchor guidance, and prohibited topics in a local `config/project_profiles.private.json` file instead of hard-coding them in Python. Private configuration files are ignored by Git and automatically preferred when present.

## Local backups and cache

- Every run creates a CSV backup in `output/`.
- AI results may be cached in `cache/audit_cache.sqlite3`.
- Repeating an unchanged audit can use the cache and avoid a new OpenAI call.
- Use the `Output backup path` printed in the terminal to locate the exact file.

The `output/`, `cache/`, `.env`, and virtual environment are local working files and should not be committed.

## Troubleshooting

### `python3.12: command not found`

Install Python 3.12 from python.org, restart Terminal, and verify the version again.

### `brew: command not found`

Homebrew is not required. Use the official Python installer.

### `ModuleNotFoundError`

Activate `.venv` and reinstall dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.\.venv\Scripts\Activate.ps1`.

### Airtable returns 401 or 403

Check the token, its scopes, and its Base access. Do not paste the token into chat or a ticket.

### The application cannot find a record

Confirm that the record is visible in the configured `AIRTABLE_VIEW_NAME`. For selected mode, confirm that `Audit Selected` is enabled.

### Airtable writes remain zero

Confirm that you used a write command, that the record passed validation, and that all output fields exist with compatible types.

### The article page returns HTTP 403

Allow the visible Chrome fallback to finish. If it still fails, open the Article URL manually and follow the corrective instruction in the terminal.

### Tests show import file mismatch

Run tests from the repository folder only. Do not run one command from a parent folder containing another copy of the same project.

## Security rules

- Keep the GitHub repository private because it contains internal project profiles and workflow details.
- Never commit `.env`.
- Never share API keys in chat, screenshots, tickets, or documentation.
- Give Airtable tokens the minimum required scopes and Base access.
- Revoke credentials immediately when a teammate leaves or a secret may have leaked.
- Run no-write mode before the first write in a new Base or after changing field mappings.
- Review a small selected batch before processing many records.

## Repository structure

```text
AI Link Auditor.command     macOS launcher
TEAM_ONBOARDING.md          short guide to share with teammates
src/                        application code
tests/                      automated tests
config/                     Airtable mappings and project profiles
prompts/                    OpenAI system prompt
output/                     local CSV backups, ignored by Git
cache/                      local response cache, ignored by Git
.env.example                safe configuration template
requirements.txt            Python dependencies
```

## Recommended rollout to a new Airtable Base

1. Copy or create the required fields and views.
2. Update `.env` with the new Base ID, table, and queue view.
3. Confirm project profiles and field mappings.
4. Add one test record and enable `Audit Selected`.
5. Run automated tests.
6. Run selected no-write mode.
7. Inspect the CSV.
8. Run selected write mode for that one record.
9. Verify every written Airtable field.
10. Increase the batch size gradually.

Do not start with a large write batch in a new Base.
