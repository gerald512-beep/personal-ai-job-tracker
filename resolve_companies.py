"""
resolve_companies.py — Auto-discover ATS careers URLs for companies in config.json.

Usage:
    python resolve_companies.py                # resolve all companies missing a url
    python resolve_companies.py --dry-run      # print results without writing config

Rules:
    - Skips companies that already have a "url" field
    - Probes known ATS patterns with httpx (5s timeout)
    - Retries once on 429
    - Falls back to https://www.{slug}.com/careers if all ATS probes fail
    - Updates config.json in-place
    - Writes company_resolution_report.txt
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# ATS probe templates  (ordered by most-common first)
# For Workday we probe several wd-numbers since tenants vary.
# ---------------------------------------------------------------------------

ATS_PROBES: list[tuple[str, str]] = [
    ("greenhouse", "https://boards.greenhouse.io/{slug}"),
    ("greenhouse", "https://job-boards.greenhouse.io/{slug}"),
    ("lever",      "https://jobs.lever.co/{slug}"),
    ("ashby",      "https://jobs.ashbyhq.com/{slug}"),
    ("smartrecruiters", "https://careers.smartrecruiters.com/{Slug}"),   # SmartRecruiters uses Title-Case
    ("workday",    "https://{slug}.wd1.myworkdayjobs.com"),
    ("workday",    "https://{slug}.wd5.myworkdayjobs.com"),
    ("workday",    "https://{slug}.wd12.myworkdayjobs.com"),
]

CONFIG_PATH = Path("config.json")
REPORT_PATH = Path("company_resolution_report.txt")
TIMEOUT     = 5      # seconds per probe
RETRY_WAIT  = 2      # seconds to wait after 429


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Lower-case, spaces → hyphens, strip non-alphanumeric except hyphens."""
    slug = name.lower().strip()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    return slug


def _title_slug(name: str) -> str:
    """Title-case single-word slug for SmartRecruiters."""
    return name.strip().replace(" ", "")


def _probe(url: str) -> bool:
    """Return True if the URL returns HTTP 200 (or 3xx that resolves to 200)."""
    try:
        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-tracker/1.0)"},
        )
        if resp.status_code == 429:
            print(f"    [429] {url} — waiting {RETRY_WAIT}s then retrying...")
            time.sleep(RETRY_WAIT)
            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-tracker/1.0)"},
            )
        # 200 = found; anything in 4xx/5xx = not found
        return resp.status_code == 200
    except Exception:
        return False


def resolve_company(name: str) -> tuple[str | None, str]:
    """
    Probe ATS patterns for `name`.
    Returns (url, ats_type) or (None, "failed").
    """
    slug = _slugify(name)

    for ats_type, template in ATS_PROBES:
        url = template.format(slug=slug, Slug=_title_slug(name))
        print(f"    probe {ats_type:<16} {url}")
        if _probe(url):
            return url, ats_type

    # Generic careers page fallback
    fallback_slug = re.sub(r"\s+", "", name.lower())   # no hyphens for domain
    fallback_url  = f"https://www.{fallback_slug}.com/careers"
    print(f"    fallback                 {fallback_url}")
    if _probe(fallback_url):
        return fallback_url, "scrape"

    return None, "failed"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run: bool = False) -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    companies: list[dict] = config.get("companies", [])

    resolved_api:     list[tuple[str, str]] = []   # (name, ats_type)
    resolved_scrape:  list[tuple[str, str]] = []
    failed:           list[str]             = []
    skipped:          list[str]             = []

    for entry in companies:
        name = entry["name"]

        if entry.get("url"):
            print(f"[SKIP]     {name} — already has url")
            skipped.append(name)
            continue

        print(f"[RESOLVING] {name}...")
        url, ats_type = resolve_company(name)

        if url and ats_type != "failed":
            entry["url"]      = url
            entry["ats_type"] = ats_type

            if ats_type == "scrape":
                print(f"[FALLBACK]  {name} -> scrape -> {url}")
                resolved_scrape.append((name, ats_type))
            else:
                print(f"[FOUND]     {name} -> {ats_type} -> {url}")
                resolved_api.append((name, ats_type))
        else:
            print(f"[FAILED]    {name} -> could not resolve, skipping")
            failed.append(name)

    # Add ats_type to already-resolved entries that are missing it
    for entry in companies:
        if entry.get("url") and not entry.get("ats_type"):
            from ats_detector import detect_ats
            result = detect_ats(entry["url"])
            entry["ats_type"] = result["type"]

    # ------------------------------------------------------------------
    # Write config.json
    # ------------------------------------------------------------------
    if not dry_run:
        config["companies"] = companies
        CONFIG_PATH.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nconfig.json updated ({len(companies)} companies).")
    else:
        print("\n[DRY RUN] config.json not modified.")

    # ------------------------------------------------------------------
    # Write report
    # ------------------------------------------------------------------
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"Company Resolution Report — {now}",
        "=" * 55,
        "",
        f"RESOLVED (API)  [{len(resolved_api)}]:",
    ]
    for name, ats in sorted(resolved_api):
        lines.append(f"    {name:<25} -> {ats}")

    lines += [
        "",
        f"RESOLVED (scrape fallback)  [{len(resolved_scrape)}]:",
    ]
    for name, ats in sorted(resolved_scrape):
        lines.append(f"    {name:<25} -> scrape")

    lines += [
        "",
        f"SKIPPED (url already present)  [{len(skipped)}]:",
    ]
    for name in skipped:
        lines.append(f"    {name}")

    lines += [
        "",
        f"FAILED (manual review needed)  [{len(failed)}]:",
    ]
    for name in failed:
        lines.append(f"    {name}")

    lines += [
        "",
        f"TOTAL: {len(companies)} companies  |  "
        f"Resolved: {len(resolved_api) + len(resolved_scrape)}  |  "
        f"Failed: {len(failed)}  |  Skipped: {len(skipped)}",
    ]

    report_text = "\n".join(lines) + "\n"

    if not dry_run:
        REPORT_PATH.write_text(report_text, encoding="utf-8")
        print(f"Report written to {REPORT_PATH}")

    print()
    print(report_text)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
