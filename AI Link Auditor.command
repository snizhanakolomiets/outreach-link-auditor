#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR" || exit 1

PYTHON="${AI_LINK_AUDITOR_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
ENV_FILE="$PROJECT_DIR/.env"

env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v key="$key" '
    $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/^([[:space:]]|\")+|([[:space:]]|\")+$/, "")
      print
      exit
    }
  ' "$ENV_FILE"
}

preview_limit() {
  local value
  value="$(env_value PREVIEW_LIMIT)"
  if [[ "$value" == <-> ]] && (( value >= 1 )); then
    echo "$value"
  else
    echo "5"
  fi
}

airtable_target() {
  local base table view
  base="$(env_value AIRTABLE_BASE_LABEL)"
  table="$(env_value AIRTABLE_TABLE_LABEL)"
  view="$(env_value AIRTABLE_VIEW_LABEL)"

  [[ -n "$base" ]] || base="$(env_value AIRTABLE_BASE_ID)"
  [[ -n "$table" ]] || table="$(env_value AIRTABLE_TABLE_NAME)"
  [[ -n "$view" ]] || view="$(env_value AIRTABLE_VIEW_NAME)"

  echo "${base:-not configured} → ${table:-not configured} → ${view:-not configured}"
}

pause() {
  echo
  read -r "reply?Press Enter to return to the menu..."
}

require_python() {
  if [[ -x "$PYTHON" ]]; then
    return 0
  fi
  echo "The .venv virtual environment was not found."
  echo "Complete the installation steps in README.md first."
  pause
  return 1
}

read_record_id() {
  local record_input
  while true; do
    echo
    read -r "record_input?Paste an Airtable record ID/URL (0 to cancel): "
    if [[ "$record_input" == "0" ]]; then
      echo "Cancelled."
      return 1
    fi
    if [[ "$record_input" =~ '(rec[A-Za-z0-9]{6,61})' ]]; then
      RECORD_ID="$match[1]"
      echo "Selected record: $RECORD_ID"
      return 0
    fi
    echo "No record ID was found. It must begin with rec. Try again."
  done
}

run_command() {
  echo
  echo "Running..."
  echo
  "$@"
  local exit_code=$?
  echo
  if (( exit_code == 0 )); then
    echo "Completed successfully. The CSV location is shown after Output backup path."
  elif (( exit_code == 3 )); then
    echo "Some selected records require correction."
    echo "The required steps are shown in the corrective-action block above."
    echo "Audit fields for those records were not changed."
  else
    echo "The command failed with exit code $exit_code."
  fi
  pause
}

while true; do
  clear
  echo "========================================"
  echo "          AI LINK AUDITOR"
  echo "========================================"
  echo "Airtable: $(airtable_target)"
  echo "Email is never sent."
  echo
  echo "1 — Run application tests"
  echo "2 — Safely preview the first $(preview_limit) records (no AI or writes)"
  echo "3 — Analyze one record without writing to Airtable"
  echo "4 — Analyze one record and write its result to Airtable"
  echo "5 — Open the results folder"
  echo "6 — Analyze selected records without writing"
  echo "7 — Analyze selected records and write successful results"
  echo "0 — Exit"
  echo
  read -r "choice?Choose an action: "

  case "$choice" in
    1)
      require_python && run_command "$PYTHON" -m pytest -q
      ;;
    2)
      require_python && run_command "$PYTHON" -m src.main --source airtable --limit "$(preview_limit)" --dry-run
      ;;
    3)
      if require_python && read_record_id; then
        echo
        echo "OpenAI receives the Article URL, target URL, project profile,"
        echo "and public link context. Contacts and API keys are not sent."
        echo "Starting analysis without Airtable writes."
        run_command "$PYTHON" -m src.main --source airtable --record-id "$RECORD_ID" --max-ai-calls 1 --no-airtable-write
      fi
      ;;
    4)
      if require_python; then
        echo
        echo "OpenAI receives the Article URL, target URL, project profile,"
        echo "and public link context. Contacts and API keys are not sent."
        echo "After confirmation, the application asks for a record ID and writes"
        echo "the result only to that record's audit fields."
        echo "1 — write the result; 0 — cancel"
        read -r "confirm?Your choice: "
        if [[ "$confirm" == "1" ]]; then
          if read_record_id; then
            run_command "$PYTHON" -m src.main --source airtable --record-id "$RECORD_ID" --max-ai-calls 1 --airtable-write
          fi
        else
          echo "Cancelled. Airtable was not changed."
          pause
        fi
      fi
      ;;
    5)
      mkdir -p "$PROJECT_DIR/output"
      open "$PROJECT_DIR/output"
      ;;
    6)
      if require_python; then
        echo
        echo "Only records with the audit-selection checkbox enabled are analyzed."
        echo "OpenAI receives URLs, the project profile, and public link context."
        echo "Airtable will not be changed."
        run_command "$PYTHON" -m src.main --source airtable --selected-only --no-airtable-write
      fi
      ;;
    7)
      if require_python; then
        echo
        echo "Only records with the audit-selection checkbox enabled are analyzed."
        echo "Successful results are written after a local CSV backup is created."
        echo "Failed records remain unchanged and selected for correction."
        echo "1 — start the batch write; 0 — cancel"
        read -r "confirm?Your choice: "
        if [[ "$confirm" == "1" ]]; then
          run_command "$PYTHON" -m src.main --source airtable --selected-only --airtable-write
        else
          echo "Cancelled. Airtable was not changed."
          pause
        fi
      fi
      ;;
    0)
      echo "Goodbye."
      exit 0
      ;;
    *)
      echo "Choose an option from 0 to 7."
      pause
      ;;
  esac
done
