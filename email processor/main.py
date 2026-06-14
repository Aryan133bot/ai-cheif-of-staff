import argparse
import logging
from pathlib import Path

from processor import EmailProcessor, load_emails_from_json


def _print_summary(processor: EmailProcessor, results: list[dict], top_n: int) -> None:
    total_extracted = sum(row["extracted_count"] for row in results)
    total_created = sum(row["created"] for row in results)
    total_updated = sum(row["updated"] for row in results)

    print(f"Processed emails: {len(results)}")
    print(f"Extracted tasks: {total_extracted}")
    print(f"Created tasks : {total_created}")
    print(f"Updated tasks : {total_updated}")
    print("")
    print("Top priorities:")

    for idx, task in enumerate(processor.task_engine.top_priorities(limit=top_n), start=1):
        review_tag = " [REVIEW]" if task["review_required"] else ""
        print(
            f"{idx}. {task['title']} | urgency={task['urgency']} "
            f"| deadline={task['deadline_date'] or '-'}{review_tag}"
        )


def run_local_json(input_path: str, db_path: str, top_n: int) -> None:
    emails = load_emails_from_json(input_path)
    processor = EmailProcessor(db_path=db_path)
    results = processor.process_batch(emails)
    _print_summary(processor, results, top_n)


def run_gmail(
    db_path: str,
    top_n: int,
    gmail_max: int,
    gmail_mode: str,
    gmail_query: str | None,
    gmail_credentials: str | None,
) -> None:
    from gmail_client import GmailClient

    client = GmailClient(credentials_path=gmail_credentials)
    emails = client.fetch_recent(
        max_results=gmail_max,
        mode=gmail_mode,
        custom_query=gmail_query,
    )
    processor = EmailProcessor(db_path=db_path)
    results = processor.process_batch(emails)
    _print_summary(processor, results, top_n)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Chief of Staff - Email Processor (Phase 1)")
    parser.add_argument(
        "--source",
        choices=["json", "gmail"],
        default="json",
        help="Input source type",
    )
    parser.add_argument(
        "--input",
        default="sample_emails.json",
        help="Path to input JSON emails file",
    )
    parser.add_argument(
        "--db",
        default="phase1_tasks.db",
        help="SQLite DB path",
    )
    parser.add_argument(
        "--top",
        default=5,
        type=int,
        help="How many top priorities to print",
    )
    parser.add_argument(
        "--gmail-mode",
        choices=["unread", "recent"],
        default="unread",
        help="Gmail fetch mode when --source gmail",
    )
    parser.add_argument(
        "--gmail-max",
        default=10,
        type=int,
        help="Max Gmail messages to fetch",
    )
    parser.add_argument(
        "--gmail-query",
        default=None,
        help="Optional custom Gmail query (overrides --gmail-mode)",
    )
    parser.add_argument(
        "--gmail-credentials",
        default=None,
        help="Path to OAuth client credentials JSON (optional)",
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging verbosity level',
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    script_dir = Path(__file__).resolve().parent
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (script_dir / db_path).resolve()

    if args.source == "json":
        input_path = Path(args.input)
        if not input_path.is_absolute():
            # Make relative input paths work regardless of current working directory.
            input_path = (script_dir / input_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(
                f"Input file not found: {input_path}. Try --input sample_emails.json"
            )
        run_local_json(str(input_path), str(db_path), args.top)
    else:
        run_gmail(
            db_path=str(db_path),
            top_n=args.top,
            gmail_max=args.gmail_max,
            gmail_mode=args.gmail_mode,
            gmail_query=args.gmail_query,
            gmail_credentials=args.gmail_credentials,
        )


if __name__ == "__main__":
    main()
