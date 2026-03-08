import json
import logging
from datetime import datetime, timedelta, timezone

import database
from scraper import run_all_scrapers

log = logging.getLogger(__name__)


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> dict:
    config = load_config()
    database.DB_PATH = config["database"]["path"]
    database.init_db()

    backfill_days = config["scheduler"].get("new_company_backfill_days", 3)
    cutoff = datetime.now(timezone.utc) - timedelta(days=backfill_days)

    started_at = datetime.now(timezone.utc).isoformat()
    raw_jobs = run_all_scrapers(config)
    log.info("Scraped %d raw jobs across all companies", len(raw_jobs))

    new_count = 0
    for company in config.get("companies", config.get("target_companies", [])):
        company_name = company["name"]
        company_jobs = [j for j in raw_jobs if j["company"] == company_name]

        if database.is_new_company(company_name):
            before = len(company_jobs)
            company_jobs = [
                j for j in company_jobs
                if datetime.fromisoformat(j["posted_date"]) >= cutoff
            ]
            log.info(
                "[%s] New company -- kept %d/%d jobs within %d-day window",
                company_name, len(company_jobs), before, backfill_days,
            )

        company_new = 0
        for job in company_jobs:
            _, is_new = database.insert_or_update_job(job)
            if is_new:
                new_count += 1
                company_new += 1

        log.info("[%s] %d fetched, %d new", company_name, len(company_jobs), company_new)

    database.log_run(started_at, datetime.now(timezone.utc).isoformat(), new_count)
    log.info("Scrape complete -- new: %d / raw total: %d", new_count, len(raw_jobs))
    return {"raw": len(raw_jobs), "new": new_count}


if __name__ == "__main__":
    main()
