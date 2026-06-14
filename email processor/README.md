# Email Processor (Phase 1)

This module implements a Phase 1 pipeline:

1. Read incoming emails (JSON input for now)
2. Extract deadlines/tasks (`classifier.py`)
3. Confidence-gate low-confidence tasks for review
4. Deduplicate and store tasks in SQLite (`task_engine.py`)
5. Show top priorities for dashboard/reminders

## Run

```bash
python main.py --source json --input sample_emails.json --db phase1_tasks.db
```

## Manual tester

To paste one real email manually and run prefilter + extraction:

```bash
python tester_main.py
```

## Input format

`sample_emails.json` is a list of objects:

- `email_id` (string)
- `subject` (string)
- `sender` (string)
- `received_at` (ISO datetime)
- `body` (string)

## Notes

- If `ANTHROPIC_API_KEY` is present, extraction uses Claude.
- If not present (or API call fails), the system uses a conservative rule-based fallback so local testing still works.
