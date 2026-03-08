"""
emailer.py — Gmail SMTP digest sender.

Two daily digests triggered by APScheduler:
  Morning (11:05am): jobs created between yesterday 6pm and today 11am UTC
  Evening (6:05pm):  jobs created between today 11am and today 6pm UTC

Only un-emailed jobs are included. After sending, emailed = 1 is set.
If no jobs fall in the window, the digest is silently skipped.

Manual trigger:
    python emailer.py morning
    python emailer.py evening
"""

import json
import smtplib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    return json.loads(Path("config.json").read_text(encoding="utf-8"))


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_salary(low: Optional[float], high: Optional[float]) -> str:
    if low is None and high is None:
        return "ND"
    if low and high:
        return f"${int(low):,} - ${int(high):,}"
    if low:
        return f"${int(low):,}+"
    return f"up to ${int(high):,}"


def _fmt_score(score: Optional[int]) -> str:
    return f"{score}/100" if score is not None else "ND"


# ---------------------------------------------------------------------------
# Email body builder (plain text + HTML)
# ---------------------------------------------------------------------------

def _build_bodies(jobs: list[dict]) -> tuple[str, str]:
    """Return (plain_text, html) for the email body."""
    n = len(jobs)
    today_str = date.today().isoformat()

    # --- Plain text ---
    lines = [f"Job Tracker — {n} new job(s) for {today_str}\n", "-" * 60]
    for job in jobs:
        lines += [
            f"Title:   {job['title']}",
            f"Company: {job['company']}",
            f"URL:     {job['url']}",
            f"Score:   {_fmt_score(job['match_score'])}",
            f"Visa:    {job['visa_flag']}",
            f"Salary:  {_fmt_salary(job['salary_min'], job['salary_max'])}",
            "-" * 60,
        ]
    plain = "\n".join(lines)

    # --- HTML ---
    rows_html = ""
    for job in jobs:
        rows_html += (
            "<tr>"
            f"<td><a href='{job['url']}'>{job['title']}</a></td>"
            f"<td>{job['company']}</td>"
            f"<td>{_fmt_score(job['match_score'])}</td>"
            f"<td>{job['visa_flag']}</td>"
            f"<td>{_fmt_salary(job['salary_min'], job['salary_max'])}</td>"
            "</tr>"
        )

    html = f"""<html><body>
<p><strong>Job Tracker — {n} new job(s) for {today_str}</strong></p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
  <thead>
    <tr style="background:#f0f0f0;">
      <th>Title</th><th>Company</th>
      <th>Score</th><th>Visa</th><th>Salary</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
</body></html>"""

    return plain, html


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------

def _send_email(config: dict, subject: str, plain: str, html: str) -> None:
    gmail = config["gmail"]
    sender = gmail["sender_address"]
    recipient = gmail["recipient_address"]
    password = gmail["app_password"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(sender, password)
        smtp.sendmail(sender, recipient, msg.as_string())


# ---------------------------------------------------------------------------
# Window query + mark emailed
# ---------------------------------------------------------------------------

def _query_window(window_start: datetime, window_end: datetime) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE emailed = 0
              AND match_score > 0
              AND created_at >= ?
              AND created_at <= ?
            ORDER BY match_score DESC, created_at DESC
            """,
            (window_start.isoformat(), window_end.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def _mark_emailed(job_ids: list[int]) -> None:
    if not job_ids:
        return
    placeholders = ",".join("?" * len(job_ids))
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET emailed = 1 WHERE id IN ({placeholders})",
            job_ids,
        )


# ---------------------------------------------------------------------------
# Public digest functions
# ---------------------------------------------------------------------------

def send_morning_digest(config: dict) -> None:
    """
    11:05am digest.
    Window: yesterday 18:00 UTC -> today 11:00 UTC.
    """
    database.DB_PATH = config["database"]["path"]

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    window_start = datetime(yesterday.year, yesterday.month, yesterday.day,
                            18, 0, 0, tzinfo=timezone.utc)
    window_end   = datetime(today.year, today.month, today.day,
                            11, 0, 0, tzinfo=timezone.utc)

    jobs = _query_window(window_start, window_end)
    if not jobs:
        print("[emailer] Morning digest: no new jobs in window — skipping")
        return

    plain, html = _build_bodies(jobs)
    subject = f"Job Tracker — {len(jobs)} new job(s) ({today})"
    _send_email(config, subject, plain, html)
    _mark_emailed([j["id"] for j in jobs])
    print(f"[emailer] Morning digest sent: {len(jobs)} job(s)")


def send_evening_digest(config: dict) -> None:
    """
    6:05pm digest.
    Window: today 11:00 UTC -> today 18:00 UTC.
    """
    database.DB_PATH = config["database"]["path"]

    today = datetime.now(timezone.utc).date()

    window_start = datetime(today.year, today.month, today.day,
                            11, 0, 0, tzinfo=timezone.utc)
    window_end   = datetime(today.year, today.month, today.day,
                            18, 0, 0, tzinfo=timezone.utc)

    jobs = _query_window(window_start, window_end)
    if not jobs:
        print("[emailer] Evening digest: no new jobs in window — skipping")
        return

    plain, html = _build_bodies(jobs)
    subject = f"Job Tracker — {len(jobs)} new job(s) ({today})"
    _send_email(config, subject, plain, html)
    _mark_emailed([j["id"] for j in jobs])
    print(f"[emailer] Evening digest sent: {len(jobs)} job(s)")


# ---------------------------------------------------------------------------
# Manual trigger
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    config = _load_config()
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if mode == "evening":
        send_evening_digest(config)
    else:
        send_morning_digest(config)
