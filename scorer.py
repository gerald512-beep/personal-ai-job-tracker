"""
scorer.py — Two-gate job scorer.

Gate 1 (free): keyword filter on job title.
Gate 2 (LLM):  gpt-4o-mini scores jobs that pass Gate 1.

Usage:
    python scorer.py           # score all unscored jobs in DB
    python scorer.py --test    # score 3 fake jobs, print results
"""

import json
import logging
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

import httpx
import openai
from bs4 import BeautifulSoup

import database

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEYWORDS = [
    "product manager",
    "product operations",
    "data analytics",
    "logistics manager",
    "program manager",
]

# ~4 chars per token; 800 tokens cap for description
MAX_DESC_CHARS = 3200

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    return json.loads(Path("config.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Daily LLM usage tracking (llm_usage table, created on first use)
# ---------------------------------------------------------------------------

def _usage_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_usage "
        "(date TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def _get_llm_calls_today() -> int:
    today = date.today().isoformat()
    with _usage_conn() as conn:
        row = conn.execute(
            "SELECT calls FROM llm_usage WHERE date = ?", (today,)
        ).fetchone()
        return row["calls"] if row else 0


def _increment_llm_calls() -> None:
    today = date.today().isoformat()
    with _usage_conn() as conn:
        conn.execute(
            "INSERT INTO llm_usage (date, calls) VALUES (?, 1) "
            "ON CONFLICT(date) DO UPDATE SET calls = calls + 1",
            (today,),
        )


# ---------------------------------------------------------------------------
# Gate 1 — keyword filter
# ---------------------------------------------------------------------------

def passes_keyword_gate(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in KEYWORDS)


# ---------------------------------------------------------------------------
# Description fetcher (on-demand, based on URL pattern)
# ---------------------------------------------------------------------------

def _httpx_get(url: str) -> Optional[httpx.Response]:
    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def fetch_description(job_url: str) -> str:
    """
    Fetch plain-text description from source API.
    Supports Greenhouse and Ashby boards; returns "" on failure.
    """
    if not job_url:
        return ""

    # Greenhouse: https://job-boards.greenhouse.io/{slug}/jobs/{id}
    gh = re.search(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", job_url)
    if gh:
        slug, job_id = gh.group(1), gh.group(2)
        resp = _httpx_get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        )
        if resp:
            content = resp.json().get("content", "") or ""
            return _strip_html(content)[:MAX_DESC_CHARS]

    # Ashby: https://jobs.ashbyhq.com/{slug}/{uuid}
    ashby = re.search(r"ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", job_url)
    if ashby:
        slug, job_id = ashby.group(1), ashby.group(2)
        resp = _httpx_get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}/postings/{job_id}"
        )
        if resp:
            plain = resp.json().get("descriptionPlain", "") or ""
            return plain[:MAX_DESC_CHARS]

    return ""


# ---------------------------------------------------------------------------
# Gate 2 — LLM scoring
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a job-fit evaluator. Score the job for the candidate based on "
    "their resume summary. Return ONLY valid JSON with no markdown or extra text."
)

USER_TEMPLATE = """\
Resume summary:
{resume_summary}

Job title: {title}
Company:   {company}
Job description (truncated to 800 tokens):
{description}

Return exactly this JSON and nothing else:
{{
  "score": <integer 0-100>,
  "reason": "<one sentence>",
  "visa_signal": "<SPONSOR | NO | ND>",
  "salary_min": <integer or null>,
  "salary_max": <integer or null>
}}

Rules:
- score: 0-100 reflecting fit between resume and role
- visa_signal: SPONSOR only if sponsorship is explicitly offered, \
NO only if explicitly denied, ND otherwise
- salary_min / salary_max: only if explicit numbers appear in the description, \
otherwise null
"""


def score_with_llm(
    client: openai.OpenAI,
    config: dict,
    title: str,
    company: str,
    description: str,
) -> dict:
    resume_summary = config.get("resume_summary", "")
    user_msg = USER_TEMPLATE.format(
        resume_summary=resume_summary,
        title=title,
        company=company,
        description=description[:MAX_DESC_CHARS],
    )

    resp = client.chat.completions.create(
        model=config["openai"]["model"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=config["openai"].get("max_tokens", 300),
        temperature=config["openai"].get("scoring_temperature", 0.2),
        response_format={"type": "json_object"},
    )

    return json.loads(resp.choices[0].message.content.strip())


# ---------------------------------------------------------------------------
# DB update helper
# ---------------------------------------------------------------------------

def _update_job(
    job_id: int,
    score: Optional[int],
    reason: str,
    visa_flag: Optional[str] = None,
    salary_min: Optional[float] = None,
    salary_max: Optional[float] = None,
) -> None:
    with sqlite3.connect(database.DB_PATH) as conn:
        if visa_flag is not None:
            conn.execute(
                "UPDATE jobs SET match_score=?, match_reason=?, "
                "visa_flag=?, salary_min=?, salary_max=? WHERE id=?",
                (score, reason, visa_flag, salary_min, salary_max, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET match_score=?, match_reason=? WHERE id=?",
                (score, reason, job_id),
            )


# ---------------------------------------------------------------------------
# Main scoring loop
# ---------------------------------------------------------------------------

def score_unscored_jobs(config: dict) -> int:
    """
    Score all jobs with match_score IS NULL.
    Returns number of LLM calls made.
    """
    database.DB_PATH = config["database"]["path"]
    max_calls = config["scoring"].get("max_llm_calls_per_day", 50)
    client = openai.OpenAI(api_key=config["openai"]["api_key"])

    with sqlite3.connect(database.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        unscored = conn.execute(
            "SELECT id, title, company, url FROM jobs "
            "WHERE match_score IS NULL ORDER BY created_at DESC"
        ).fetchall()

    log.info("[scorer] %d unscored jobs found", len(unscored))
    llm_calls_made = 0

    for row in unscored:
        job_id, title, company, url = row["id"], row["title"], row["company"], row["url"]

        # Gate 1
        if not passes_keyword_gate(title):
            _update_job(job_id, score=0, reason="filtered by keyword")
            continue

        # Daily limit check
        calls_today = _get_llm_calls_today()
        if calls_today >= max_calls:
            _update_job(job_id, score=None, reason="daily limit reached")
            log.info("[scorer] Daily LLM limit (%d) reached -- stopping", max_calls)
            break

        # Gate 2
        description = fetch_description(url)

        try:
            result = score_with_llm(client, config, title, company, description)
            _increment_llm_calls()
            llm_calls_made += 1

            _update_job(
                job_id,
                score=result.get("score"),
                reason=result.get("reason", ""),
                visa_flag=result.get("visa_signal", "ND"),
                salary_min=result.get("salary_min"),
                salary_max=result.get("salary_max"),
            )
            log.info(
                "[scorer] %-50s @ %-20s score=%-3s | %s",
                title[:50], company, result.get("score"), result.get("reason", "")[:60],
            )

        except Exception as exc:
            _update_job(job_id, score=None, reason="scoring error")
            log.exception("[scorer] Error scoring '%s': %s", title, exc)

    log.info("[scorer] Done. LLM calls this run: %d", llm_calls_made)
    return llm_calls_made


# ---------------------------------------------------------------------------
# --test mode: 3 fake jobs, real API calls, printed results
# ---------------------------------------------------------------------------

_FAKE_JOBS = [
    {
        "title": "Senior Product Manager, Growth",
        "company": "Acme Corp",
        "description": (
            "We are looking for a Senior PM to lead our growth initiatives. "
            "Define product strategy, partner with engineering and design, drive OKRs. "
            "5+ years PM experience required. Salary: $150,000-$180,000. "
            "Visa sponsorship available for qualified candidates."
        ),
    },
    {
        "title": "Logistics Manager",
        "company": "FreightCo",
        "description": (
            "Manage day-to-day logistics and warehouse operations. "
            "Coordinate with carriers, optimize last-mile routes, lead a team of 15. "
            "Must be authorized to work in the US — no visa sponsorship provided."
        ),
    },
    {
        "title": "Backend Software Engineer",
        "company": "TechStartup",
        "description": (
            "Build scalable microservices in Go. Strong CS fundamentals, "
            "5+ years backend experience, Kubernetes familiarity a plus."
        ),
    },
]


def _run_test() -> None:
    config = _load_config()
    client = openai.OpenAI(api_key=config["openai"]["api_key"])

    print("=== scorer.py --test ===\n")

    for job in _FAKE_JOBS:
        title, company, description = job["title"], job["company"], job["description"]
        print(f"Job:     {title}")
        print(f"Company: {company}")

        if not passes_keyword_gate(title):
            print("Gate 1:  FAIL")
            print("Score:   0 | reason: filtered by keyword\n")
            continue

        print("Gate 1:  PASS")

        try:
            result = score_with_llm(client, config, title, company, description)
            print(f"Score:   {result.get('score')}/100")
            print(f"Reason:  {result.get('reason')}")
            print(f"Visa:    {result.get('visa_signal')}")
            sal_lo = result.get("salary_min")
            sal_hi = result.get("salary_max")
            print(f"Salary:  {sal_lo} - {sal_hi}")
        except Exception as exc:
            print(f"Error:   {exc}")

        print()


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_test()
    else:
        config = _load_config()
        score_unscored_jobs(config)
