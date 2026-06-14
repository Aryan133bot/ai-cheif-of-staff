import logging
from datetime import datetime, timezone
from uuid import uuid4

from models import RawEmail
from processor import EmailProcessor


def _read_multiline_body() -> str:
    print("Paste email body. Type a single line with END to finish:")
    lines: list[str] = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    print("Manual Email Tester")
    print("-------------------")
    sender = input("From: ").strip() or "manual@test.local"
    subject = input("Subject: ").strip() or "(no subject)"
    body = _read_multiline_body()

    if not body:
        print("No email body provided. Exiting.")
        return

    email = RawEmail(
        email_id=f"manual-{uuid4().hex[:12]}",
        subject=subject,
        sender=sender,
        body=body,
        received_at=datetime.now(timezone.utc),
    )

    processor = EmailProcessor()
    labels = processor.get_prefilter_labels(email)
    relevant = len(labels) > 0
    print(f"\nPrefilter decision: {'PASS' if relevant else 'SKIP'}")
    if labels:
        print(f"Detected categories: {', '.join(labels)}")

    if not relevant:
        print("Email skipped by prefilter (no strong meeting/deadline/event signals found).")
        return

    result = processor.process_email(email)
    print("\nProcessing result:")
    print(f"Extracted: {result['extracted_count']}")
    print(f"Created:   {result['created']}")
    print(f"Updated:   {result['updated']}")

    print("\nExtracted tasks preview:")
    tasks = processor.task_engine.top_priorities(limit=10)
    for idx, task in enumerate(tasks, start=1):
        if task["source_email_id"] != email.email_id:
            continue
        review_tag = " [REVIEW]" if task["review_required"] else ""
        print(
            f"{idx}. {task['title']} | urgency={task['urgency']} "
            f"| deadline={task['deadline_date'] or '-'}{review_tag}"
        )


if __name__ == "__main__":
    main()

