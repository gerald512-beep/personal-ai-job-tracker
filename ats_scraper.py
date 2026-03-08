"""
ats_scraper.py — Unified job fetcher routing by ATS type.

Entry point:
    from ats_scraper import fetch_jobs
    jobs = fetch_jobs("Stripe", "https://boards.greenhouse.io/stripe", config)

CLI test:
    python ats_scraper.py --test
"""

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ats_detector import detect_ats

# ---------------------------------------------------------------------------
# Private helpers (duplicated from scraper.py to avoid circular imports)
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

_SALARY_PATTERN = re.compile(
    r"\$\s?([\d,]+)\s*(?:k|K)?(?:\s*[-\u2013]\s*\$?\s*([\d,]+)\s*(?:k|K)?)?",
    re.I,
)
_VISA_POSITIVE = re.compile(r"\b(visa\s+sponsor(?:ship)?|will\s+sponsor)\b", re.I)
_VISA_NEGATIVE = re.compile(r"\b(no\s+visa|not\s+sponsor|must\s+be\s+authorized)\b", re.I)

_US_LOCATION = re.compile(
    r"\b(US|USA|United States|Remote|San Francisco|New York|Seattle|Austin|Boston|Chicago|Denver|LA|Los Angeles)\b",
    re.I,
)


def _httpx_get(url: str, headers: Optional[dict] = None) -> Optional[httpx.Response]:
    h = {**_HEADERS, **(headers or {})}
    try:
        resp = httpx.get(url, headers=h, follow_redirects=True, timeout=20)
        resp.raise_for_status()
        return resp
    except Exception as exc:
        print(f"[ats_scraper] GET {url} failed: {exc}")
        return None


def _httpx_post(url: str, json_body: dict, headers: Optional[dict] = None) -> Optional[httpx.Response]:
    h = {**_HEADERS, "Content-Type": "application/json", **(headers or {})}
    try:
        resp = httpx.post(url, json=json_body, headers=h, follow_redirects=True, timeout=20)
        resp.raise_for_status()
        return resp
    except Exception as exc:
        print(f"[ats_scraper] POST {url} failed: {exc}")
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_date(raw) -> str:
    if not raw:
        return _now_iso()
    try:
        if isinstance(raw, str):
            # Handle Z suffix
            raw = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(raw).isoformat()
        return _now_iso()
    except Exception:
        return _now_iso()


def _strip_html(html: str) -> str:
    try:
        return BeautifulSoup(html, "lxml").get_text(separator=" ")
    except Exception:
        return html


def _truncate(text: str, limit: int = 2000) -> str:
    return text[:limit] if text else text


def _extract_salary(text: str):
    m = _SALARY_PATTERN.search(text)
    if not m:
        return None, None

    def _parse(val):
        if val is None:
            return None
        num = float(val.replace(",", ""))
        return num * 1000 if num < 1000 else num

    return _parse(m.group(1)), _parse(m.group(2))


def _extract_visa(text: str) -> str:
    if _VISA_POSITIVE.search(text):
        return "SPONSOR"
    if _VISA_NEGATIVE.search(text):
        return "NO"
    return "ND"


def _is_us_or_remote(location: str) -> bool:
    """Return True if location is US/Remote or empty."""
    if not location or not location.strip():
        return True
    return bool(_US_LOCATION.search(location))


# ---------------------------------------------------------------------------
# Per-ATS fetchers
# ---------------------------------------------------------------------------

def _fetch_greenhouse(ats_info: dict) -> list[dict]:
    resp = _httpx_get(ats_info["api_url"])
    if resp is None:
        return []

    raw = resp.json().get("jobs", [])
    jobs = []
    for item in raw:
        title = item.get("title", "").strip()
        url = item.get("absolute_url", "").strip()
        if not title or not url:
            continue

        location = (item.get("location") or {}).get("name", "") or ""
        if not _is_us_or_remote(location):
            continue

        content_html = item.get("content", "") or ""
        content_text = _truncate(_strip_html(content_html))
        salary_min, salary_max = _extract_salary(content_text)
        visa_flag = _extract_visa(content_text)

        jobs.append({
            "title": title,
            "url": url,
            "sources": "greenhouse",
            "posted_date": _safe_date(item.get("first_published")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "visa_flag": visa_flag,
            "description": content_text,
            "location": location,
        })

    return jobs


def _fetch_lever(ats_info: dict) -> list[dict]:
    resp = _httpx_get(ats_info["api_url"])
    if resp is None:
        return []

    raw = resp.json()
    if not isinstance(raw, list):
        raw = raw.get("postings", [])

    jobs = []
    for item in raw:
        title = item.get("text", "").strip()
        url = item.get("hostedUrl", "").strip()
        if not title or not url:
            continue

        location = (item.get("categories") or {}).get("location", "") or ""
        if not _is_us_or_remote(location):
            continue

        created_ms = item.get("createdAt", 0) or 0
        posted_date = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()

        desc = item.get("descriptionPlain", "") or ""
        desc = _truncate(desc)
        salary_min, salary_max = _extract_salary(desc)
        visa_flag = _extract_visa(desc)

        jobs.append({
            "title": title,
            "url": url,
            "sources": "lever",
            "posted_date": posted_date,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "visa_flag": visa_flag,
            "description": desc,
            "location": location,
        })

    return jobs


def _fetch_ashby(ats_info: dict) -> list[dict]:
    resp = _httpx_post(ats_info["api_url"], {"jobPostings": True})
    if resp is None:
        # Try GET as fallback
        resp = _httpx_get(ats_info["api_url"])
    if resp is None:
        return []

    raw = resp.json().get("jobs", [])
    jobs = []
    for item in raw:
        if not item.get("isListed", True):
            continue

        title = item.get("title", "").strip()
        url = item.get("jobUrl", "").strip()
        if not title or not url:
            continue

        location = item.get("location", "") or item.get("locationName", "") or ""
        if not _is_us_or_remote(location):
            continue

        desc = item.get("descriptionPlain", "") or ""
        desc = _truncate(desc)
        salary_min, salary_max = _extract_salary(desc)
        visa_flag = _extract_visa(desc)

        jobs.append({
            "title": title,
            "url": url,
            "sources": "ashby",
            "posted_date": _safe_date(item.get("publishedAt")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "visa_flag": visa_flag,
            "description": desc,
            "location": location,
        })

    return jobs


def _fetch_teamtailor(ats_info: dict, token: str) -> list[dict]:
    if not token:
        print(f"[teamtailor] No API token configured — skipping {ats_info['slug']}")
        return []

    headers = {
        "Authorization": f"Token token={token}",
        "X-Api-Version": "20161108",
    }
    resp = _httpx_get(ats_info["api_url"], headers=headers)
    if resp is None:
        return []

    raw = resp.json().get("data", [])
    jobs = []
    for item in raw:
        attrs = item.get("attributes", {})
        title = attrs.get("title", "").strip()
        url = (item.get("links") or {}).get("careersite-job-url", "").strip()
        if not title or not url:
            continue

        location = attrs.get("location", "") or ""
        if not _is_us_or_remote(location):
            continue

        body = attrs.get("body", "") or ""
        body_text = _truncate(_strip_html(body))
        salary_min, salary_max = _extract_salary(body_text)
        visa_flag = _extract_visa(body_text)

        jobs.append({
            "title": title,
            "url": url,
            "sources": "teamtailor",
            "posted_date": _safe_date(attrs.get("created-at")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "visa_flag": visa_flag,
            "description": body_text,
            "location": location,
        })

    return jobs


def _fetch_workday(ats_info: dict) -> list[dict]:
    hostname = ats_info.get("_hostname", "")
    path_parts = ats_info.get("_path_parts", [])
    company = ats_info.get("slug", "")

    # Extract wd-number from hostname: e.g. salesforce.wd12.myworkdayjobs.com
    import re as _re
    wd_match = _re.search(r"\.(wd\d+)\.", hostname)
    wd_num = wd_match.group(1) if wd_match else "wd1"

    # Board identifier is typically the last path segment
    board = path_parts[-1] if path_parts else "jobs"

    keywords = ["product manager", "operations manager", "data analytics", "logistics manager"]
    seen_urls: set[str] = set()
    jobs = []

    for keyword in keywords:
        offset = 0
        while True:
            post_url = (
                f"https://{hostname}/wday/cxs/{company}/{board}/jobs"
            )
            body = {
                "searchText": keyword,
                "limit": 20,
                "offset": offset,
                "appliedFacets": {},
            }
            resp = _httpx_post(post_url, body)
            if resp is None:
                break

            data = resp.json()
            postings = data.get("jobPostings", [])
            if not postings:
                break

            for item in postings:
                url = item.get("externalPath", "") or item.get("bulletFields", [""])[0]
                if not url:
                    continue
                if not url.startswith("http"):
                    url = f"https://{hostname}{url}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title = item.get("title", "").strip()
                location = item.get("locationsText", "") or ""

                # Parse "Posted X Days Ago"
                posted_raw = item.get("postedOn", "") or ""
                posted_date = _now_iso()
                days_match = _re.search(r"(\d+)\s+day", posted_raw, _re.I)
                if days_match:
                    try:
                        days_ago = int(days_match.group(1))
                        dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
                        posted_date = dt.isoformat()
                    except Exception:
                        pass

                jobs.append({
                    "title": title,
                    "url": url,
                    "sources": "workday",
                    "posted_date": posted_date,
                    "salary_min": None,
                    "salary_max": None,
                    "visa_flag": "ND",
                    "description": None,
                    "location": location,
                })

            if len(postings) < 20:
                break
            offset += 20

    return jobs


def _fetch_smartrecruiters(ats_info: dict) -> list[dict]:
    offset = 0
    jobs = []

    while True:
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{ats_info['slug']}"
            f"/postings?limit=100&offset={offset}"
        )
        resp = _httpx_get(url)
        if resp is None:
            break

        data = resp.json()
        total = data.get("totalFound", 0)
        items = data.get("content", [])
        if not items:
            break

        for item in items:
            title = item.get("name", "").strip()
            ref = item.get("ref", "").strip()
            if not title or not ref:
                continue

            loc = item.get("location") or {}
            location = f"{loc.get('city', '')}, {loc.get('country', '')}".strip(", ")
            if not _is_us_or_remote(location):
                continue

            comp = item.get("compensation") or {}
            salary_min = comp.get("min")
            salary_max = comp.get("max")
            if salary_min:
                salary_min = float(salary_min)
            if salary_max:
                salary_max = float(salary_max)

            jobs.append({
                "title": title,
                "url": ref,
                "sources": "smartrecruiters",
                "posted_date": _safe_date(item.get("releasedDate")),
                "salary_min": salary_min,
                "salary_max": salary_max,
                "visa_flag": "ND",
                "description": None,
                "location": location,
            })

        offset += len(items)
        if offset >= total:
            break

    return jobs


def _fetch_jobvite(ats_info: dict, original_url: str) -> list[dict]:
    resp = _httpx_get(ats_info["api_url"])
    if resp is None:
        return _html_fallback(original_url)

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        print(f"[jobvite] XML parse error: {exc}")
        return _html_fallback(original_url)

    jobs = []
    # Jobvite feeds use <job> elements; field names vary slightly
    ns = {"jv": "http://www.jobvite.com/jobfeed"}
    for job_el in root.iter("job"):
        def _text(tag):
            el = job_el.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        title = _text("title")
        url = _text("apply-url") or _text("jobvite-url")
        if not title or not url:
            continue

        desc = _truncate(_strip_html(_text("description")))
        salary_min, salary_max = _extract_salary(desc)
        visa_flag = _extract_visa(desc)

        jobs.append({
            "title": title,
            "url": url,
            "sources": "jobvite",
            "posted_date": _safe_date(_text("date")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "visa_flag": visa_flag,
            "description": desc,
            "location": _text("location"),
        })

    return jobs


def _html_fallback(url: str) -> list[dict]:
    """Best-effort HTML scrape. Returns [] on any failure."""
    try:
        resp = _httpx_get(url)
        if resp is None:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []

        # Look for common job listing patterns
        for tag in soup.find_all(["a"], href=True):
            href = tag.get("href", "")
            text = tag.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 120:
                continue
            if not href.startswith("http"):
                continue
            # Heuristic: job links often contain /jobs/ or /careers/
            if not any(seg in href for seg in ("/job/", "/jobs/", "/career/", "/careers/", "/posting/")):
                continue
            jobs.append({
                "title": text,
                "url": href,
                "sources": "scrape",
                "posted_date": _now_iso(),
                "salary_min": None,
                "salary_max": None,
                "visa_flag": "ND",
                "description": None,
                "location": None,
            })

        # Deduplicate by URL
        seen: set[str] = set()
        unique = []
        for j in jobs:
            if j["url"] not in seen:
                seen.add(j["url"])
                unique.append(j)

        return unique
    except Exception as exc:
        print(f"[html_fallback] {url} failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_ROUTER = {
    "greenhouse":      lambda ats, cfg, url: _fetch_greenhouse(ats),
    "lever":           lambda ats, cfg, url: _fetch_lever(ats),
    "ashby":           lambda ats, cfg, url: _fetch_ashby(ats),
    "teamtailor":      lambda ats, cfg, url: _fetch_teamtailor(ats, cfg.get("teamtailor_token", "")),
    "workday":         lambda ats, cfg, url: _fetch_workday(ats),
    "smartrecruiters": lambda ats, cfg, url: _fetch_smartrecruiters(ats),
    "jobvite":         lambda ats, cfg, url: _fetch_jobvite(ats, url),
    "scrape":          lambda ats, cfg, url: _html_fallback(url),
}


def fetch_jobs(company_name: str, url: str, config: dict) -> list[dict]:
    """
    Detect ATS, fetch jobs, inject company_name into every record.
    On any unexpected exception: log warning + HTML fallback, never crash.
    """
    ats_info = detect_ats(url)
    ats_type = ats_info["type"]

    try:
        handler = _ROUTER.get(ats_type, _ROUTER["scrape"])
        jobs = handler(ats_info, config, url)
    except Exception as exc:
        print(f"[ats_scraper] {company_name} ({ats_type}) crashed: {exc} — trying HTML fallback")
        try:
            jobs = _html_fallback(url)
        except Exception:
            jobs = []

    for job in jobs:
        job["company"] = company_name

    print(f"[{company_name}] {ats_type} -> {len(jobs)} jobs")
    return jobs


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------

if __name__ == "__main__" and "--test" in sys.argv:
    import json
    from pathlib import Path

    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))

    print("=== Greenhouse (Stripe) ===")
    gh_jobs = fetch_jobs("Stripe", "https://boards.greenhouse.io/stripe", cfg)
    for j in gh_jobs[:3]:
        print(f"  {j['title'][:60]}  |  {j['location']}  |  {j['posted_date'][:10]}")

    print()
    print("=== Lever (OpenAI) ===")
    lv_jobs = fetch_jobs("OpenAI", "https://jobs.lever.co/openai", cfg)
    for j in lv_jobs[:3]:
        print(f"  {j['title'][:60]}  |  {j['location']}  |  {j['posted_date'][:10]}")
