"""
ats_detector.py — Pure URL pattern matching, no HTTP calls.

Usage:
    from ats_detector import detect_ats
    info = detect_ats("https://boards.greenhouse.io/stripe")
    # -> {"type": "greenhouse", "slug": "stripe", "api_url": "..."}

CLI test:
    python ats_detector.py --test
"""

import re
import sys
from urllib.parse import urlparse


def detect_ats(url: str) -> dict:
    """
    Detect ATS type from a careers URL.

    Returns:
        {
            "type":    str        — ATS name or "scrape"
            "slug":    str|None   — company/board slug
            "api_url": str|None   — pre-built API URL (None for workday/scrape)
        }
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    path_parts = [p for p in parsed.path.split("/") if p]

    # ------------------------------------------------------------------
    # 1. Greenhouse
    # ------------------------------------------------------------------
    if (
        hostname in ("boards.greenhouse.io", "job-boards.greenhouse.io")
        or hostname.endswith(".greenhouse.io")
    ):
        if hostname in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
            slug = path_parts[0] if path_parts else None
        else:
            slug = hostname.split(".")[0]
        api_url = (
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            if slug else None
        )
        return {"type": "greenhouse", "slug": slug, "api_url": api_url}

    # ------------------------------------------------------------------
    # 2. Lever
    # ------------------------------------------------------------------
    if hostname == "jobs.lever.co":
        slug = path_parts[0] if path_parts else None
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json" if slug else None
        return {"type": "lever", "slug": slug, "api_url": api_url}

    # ------------------------------------------------------------------
    # 3. Ashby
    # ------------------------------------------------------------------
    if hostname == "jobs.ashbyhq.com":
        slug = path_parts[0] if path_parts else None
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}" if slug else None
        return {"type": "ashby", "slug": slug, "api_url": api_url}

    # ------------------------------------------------------------------
    # 4. Teamtailor
    # ------------------------------------------------------------------
    if hostname.endswith(".teamtailor.com"):
        slug = hostname.split(".")[0]
        api_url = "https://api.teamtailor.com/v1/jobs"
        return {"type": "teamtailor", "slug": slug, "api_url": api_url}

    # ------------------------------------------------------------------
    # 5. Workday  (*.wd{N}.myworkdayjobs.com)
    # ------------------------------------------------------------------
    _workday_re = re.compile(r"^(.+?)\.wd\d+\.myworkdayjobs\.com$")
    m = _workday_re.match(hostname)
    if m:
        slug = m.group(1)
        # api_url is built at fetch time (keyword-specific POST)
        return {"type": "workday", "slug": slug, "api_url": None,
                "_hostname": hostname, "_path_parts": path_parts}

    # ------------------------------------------------------------------
    # 6. SmartRecruiters
    # ------------------------------------------------------------------
    if hostname == "careers.smartrecruiters.com":
        slug = path_parts[0] if path_parts else None
        api_url = (
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
            if slug else None
        )
        return {"type": "smartrecruiters", "slug": slug, "api_url": api_url}

    # ------------------------------------------------------------------
    # 7. Jobvite
    # ------------------------------------------------------------------
    if hostname == "jobs.jobvite.com":
        slug = path_parts[0] if path_parts else None
        api_url = f"https://jobs.jobvite.com/{slug}/feed" if slug else None
        return {"type": "jobvite", "slug": slug, "api_url": api_url}

    if hostname.endswith(".jobvite.com"):
        slug = hostname.split(".")[0]
        api_url = f"https://{slug}.jobvite.com/api/jobfeed"
        return {"type": "jobvite", "slug": slug, "api_url": api_url}

    # ------------------------------------------------------------------
    # 8. Generic HTML fallback
    # ------------------------------------------------------------------
    return {"type": "scrape", "slug": None, "api_url": None}


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------

_TEST_URLS = [
    ("greenhouse",      "https://boards.greenhouse.io/stripe"),
    ("lever",           "https://jobs.lever.co/openai"),
    ("ashby",           "https://jobs.ashbyhq.com/notion"),
    ("teamtailor",      "https://acme.teamtailor.com/jobs"),
    ("workday",         "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"),
    ("smartrecruiters", "https://careers.smartrecruiters.com/Google"),
    ("jobvite",         "https://jobs.jobvite.com/acme/jobs"),
]

if __name__ == "__main__" and "--test" in sys.argv:
    print(f"{'URL':<55}  {'type':<16}  {'slug'}")
    print("-" * 90)
    for expected, test_url in _TEST_URLS:
        result = detect_ats(test_url)
        match = "OK" if result["type"] == expected else f"FAIL (expected {expected})"
        print(f"{test_url:<55}  {result['type']:<16}  {result['slug']}  [{match}]")
