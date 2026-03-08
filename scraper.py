import re
from datetime import datetime, timezone
from typing import TypedDict

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

SALARY_PATTERN = re.compile(
    r"\$\s?([\d,]+)\s*(?:k|K)?(?:\s*[-\u2013]\s*\$?\s*([\d,]+)\s*(?:k|K)?)?",
    re.I,
)
VISA_POSITIVE = re.compile(r"\b(visa\s+sponsor(?:ship)?|will\s+sponsor)\b", re.I)
VISA_NEGATIVE = re.compile(
    r"\b(no\s+visa|not\s+sponsor|must\s+be\s+authorized)\b", re.I
)


class RawJob(TypedDict):
    title: str
    company: str
    url: str
    sources: str
    posted_date: str
    salary_min: float | None
    salary_max: float | None
    visa_flag: str


def extract_salary(text: str) -> tuple[float | None, float | None]:
    m = SALARY_PATTERN.search(text)
    if not m:
        return None, None

    def _parse(val: str | None) -> float | None:
        if val is None:
            return None
        num = float(val.replace(",", ""))
        return num * 1000 if num < 1000 else num

    return _parse(m.group(1)), _parse(m.group(2))


def extract_visa_flag(text: str) -> str:
    if VISA_POSITIVE.search(text):
        return "SPONSOR"
    if VISA_NEGATIVE.search(text):
        return "NO"
    return "ND"


def _httpx_get(url: str) -> httpx.Response | None:
    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        return resp
    except Exception as exc:
        print(f"[httpx] GET {url} failed: {exc}")
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Scraper: Greenhouse JSON API
#   Works for any company hosted on Greenhouse.
#   Config field: board_slug (e.g. "greenhouse", "discord", "stripe")
#   API: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
#   Returns: title, url, posted_date from first_published
# ---------------------------------------------------------------------------

def scrape_greenhouse_api(company: dict) -> list[RawJob]:
    slug = company.get("board_slug", company["name"].lower())
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

    resp = _httpx_get(url)
    if resp is None:
        return []

    data = resp.json()
    raw = data.get("jobs", [])
    jobs: list[RawJob] = []

    for item in raw:
        title = item.get("title", "").strip()
        job_url = item.get("absolute_url", "").strip()
        if not title or not job_url:
            continue

        # first_published is ISO-8601; fall back to scrape time
        posted_date = item.get("first_published") or _now_iso()

        jobs.append(
            RawJob(
                title=title,
                company=company["name"],
                url=job_url,
                sources=company["name"],
                posted_date=posted_date,
                salary_min=None,
                salary_max=None,
                visa_flag="ND",
            )
        )

    print(f"[{company['name']}] Greenhouse API -> {len(jobs)} jobs")
    return jobs


# ---------------------------------------------------------------------------
# Scraper: Ashby JSON API
#   Works for any company hosted on Ashby.
#   Config field: board_slug (e.g. "notion", "linear")
#   API: https://api.ashbyhq.com/posting-api/job-board/{slug}
#   Returns: title, url, posted_date from publishedAt, salary/visa from description
# ---------------------------------------------------------------------------

def scrape_ashby_api(company: dict) -> list[RawJob]:
    slug = company.get("board_slug", company["name"].lower())
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    resp = _httpx_get(url)
    if resp is None:
        return []

    data = resp.json()
    raw = data.get("jobs", [])
    jobs: list[RawJob] = []

    for item in raw:
        if not item.get("isListed", True):
            continue

        title = item.get("title", "").strip()
        job_url = item.get("jobUrl", "").strip()
        if not title or not job_url:
            continue

        posted_date = item.get("publishedAt") or _now_iso()

        # Extract salary/visa from plain-text description when available
        description = item.get("descriptionPlain", "") or ""
        salary_min, salary_max = extract_salary(description)
        visa_flag = extract_visa_flag(description)

        jobs.append(
            RawJob(
                title=title,
                company=company["name"],
                url=job_url,
                sources=company["name"],
                posted_date=posted_date,
                salary_min=salary_min,
                salary_max=salary_max,
                visa_flag=visa_flag,
            )
        )

    print(f"[{company['name']}] Ashby API -> {len(jobs)} jobs")
    return jobs


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

SCRAPER_REGISTRY: dict[str, callable] = {
    "greenhouse_api": scrape_greenhouse_api,
    "ashby_api":      scrape_ashby_api,
}


def run_all_scrapers(config: dict) -> list[RawJob]:
    import time
    from ats_scraper import fetch_jobs

    all_jobs: list[RawJob] = []
    companies = config.get("companies", config.get("target_companies", []))

    for company in companies:
        name = company["name"]
        url  = company.get("url", "")
        if not url:
            # legacy format: build URL from board_slug for backward compat
            scraper_key = company.get("scraper", "")
            if scraper_key in SCRAPER_REGISTRY:
                jobs = SCRAPER_REGISTRY[scraper_key](company)
                all_jobs.extend(jobs)
            continue
        try:
            jobs = fetch_jobs(name, url, config)
            for job in jobs:
                job.setdefault("company", name)
            all_jobs.extend(jobs)
        except Exception as exc:
            print(f"[dispatcher] {name} crashed: {exc}")
        time.sleep(1)

    return all_jobs
