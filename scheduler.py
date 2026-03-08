"""
scheduler.py — APScheduler daemon for job-tracker.

Cron schedule (all times local):
  00:00  scrape
  00:10  score
  06:00  scrape
  06:10  score
  11:05  morning email digest
  12:00  scrape
  12:10  score
  18:00  scrape
  18:05  evening email digest
  18:10  score

Run:
    python scheduler.py
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "scheduler.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)


def _config() -> dict:
    return json.loads(Path("config.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Job functions (each re-loads config so live edits take effect)
# ---------------------------------------------------------------------------

def job_scrape() -> None:
    log.info("=== SCRAPE START ===")
    t0 = time.monotonic()
    try:
        from main import main
        stats = main()
        elapsed = time.monotonic() - t0
        log.info("=== SCRAPE DONE in %.1fs -- new=%d raw=%d ===", elapsed, stats["new"], stats["raw"])
    except Exception as exc:
        log.exception("=== SCRAPE FAILED after %.1fs: %s ===", time.monotonic() - t0, exc)


def job_score() -> None:
    log.info("=== SCORE START ===")
    t0 = time.monotonic()
    try:
        from scorer import score_unscored_jobs
        llm_calls = score_unscored_jobs(_config())
        elapsed = time.monotonic() - t0
        log.info("=== SCORE DONE in %.1fs -- llm_calls=%d ===", elapsed, llm_calls)
    except Exception as exc:
        log.exception("=== SCORE FAILED after %.1fs: %s ===", time.monotonic() - t0, exc)


def job_morning_email() -> None:
    log.info("=== MORNING EMAIL START ===")
    t0 = time.monotonic()
    try:
        from emailer import send_morning_digest
        send_morning_digest(_config())
        log.info("=== MORNING EMAIL DONE in %.1fs ===", time.monotonic() - t0)
    except Exception as exc:
        log.exception("=== MORNING EMAIL FAILED after %.1fs: %s ===", time.monotonic() - t0, exc)


def job_evening_email() -> None:
    log.info("=== EVENING EMAIL START ===")
    t0 = time.monotonic()
    try:
        from emailer import send_evening_digest
        send_evening_digest(_config())
        log.info("=== EVENING EMAIL DONE in %.1fs ===", time.monotonic() - t0)
    except Exception as exc:
        log.exception("=== EVENING EMAIL FAILED after %.1fs: %s ===", time.monotonic() - t0, exc)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler()

    # Scraper: midnight, 6am, noon, 6pm
    for hour in (0, 6, 12, 18):
        scheduler.add_job(
            job_scrape,
            CronTrigger(hour=hour, minute=0),
            id=f"scrape_{hour:02d}00",
            name=f"Scrape at {hour:02d}:00",
        )

    # Scorer: 10 min after each scrape
    for hour in (0, 6, 12, 18):
        scheduler.add_job(
            job_score,
            CronTrigger(hour=hour, minute=10),
            id=f"score_{hour:02d}10",
            name=f"Score at {hour:02d}:10",
        )

    # Email digests
    scheduler.add_job(
        job_morning_email,
        CronTrigger(hour=11, minute=5),
        id="email_morning",
        name="Morning digest at 11:05",
    )
    scheduler.add_job(
        job_evening_email,
        CronTrigger(hour=18, minute=5),
        id="email_evening",
        name="Evening digest at 18:05",
    )

    return scheduler


def _run_ats_validation() -> None:
    from ats_detector import detect_ats
    cfg = _config()
    companies = cfg.get("companies", cfg.get("target_companies", []))
    for company in companies:
        url = company.get("url", "")
        if not url:
            log.info("[ATS DETECTION] %s -> legacy config (no url)", company["name"])
            continue
        result = detect_ats(url)
        log.info("[ATS DETECTION] %s -> %s (slug=%s)", company["name"], result["type"], result["slug"])


if __name__ == "__main__":
    _run_ats_validation()
    scheduler = build_scheduler()

    now_utc = datetime.now(timezone.utc)
    log.info("Scheduler initializing. Upcoming jobs:")
    for job in scheduler.get_jobs():
        next_run = job.trigger.get_next_fire_time(None, now_utc)
        log.info("  %-35s next run: %s", job.name, next_run.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if next_run else "N/A")
    log.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
