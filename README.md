# Personal AI Job Tracker

A fully automated job search pipeline built to solve a real problem: the signal-to-noise ratio of job boards is terrible, and manually tracking applications across dozens of company career pages is unsustainable.

This tool scrapes, scores, and surfaces the most relevant job postings every day — so the only jobs I review are ones worth reviewing.

---

## The Problem It Solves

Job searching at scale means dealing with hundreds of postings across multiple platforms, most of which are irrelevant. Manually checking company career pages, copying links into spreadsheets, and remembering where each application stands is friction that compounds over time.

This project eliminates that friction end-to-end:

- **Discovery** — automatically pulls new postings from target companies every 6 hours
- **Filtering** — scores each job against a resume using an LLM, so only high-signal roles surface
- **Tracking** — a lightweight web UI manages the full application pipeline in one place
- **Alerting** — morning and evening email digests deliver the day's best matches

---

## How It Works

```
Scrape (every 6h) → Score (GPT-4o-mini) → Digest email → Web UI review → Status tracking
```

1. **Scraper** pulls job listings from Greenhouse and Ashby career board APIs. Adding a new target company requires one line in a config file — no code changes.
2. **Scorer** runs a two-gate filter: keyword match first, then LLM relevance scoring (0–100) against a parsed resume summary. This keeps API costs low while maintaining accuracy.
3. **Emailer** sends HTML digests twice a day with only new, high-scoring jobs.
4. **Web UI** provides a Kanban-style status board to move jobs through the pipeline: New → Saved → Applied → Interviewing → Rejected / Offer / Dismissed.

---

## Features

- **Zero-noise dashboard** — default view hides dismissed and unscored jobs; only actionable listings shown
- **Live status updates** — change application status inline without page reloads (HTMX)
- **Deduplication** — SHA256 hashing ensures the same job never appears twice across scrape runs
- **Resume-aware scoring** — parses PDF or plain-text resume, summarizes it, and uses that context for every LLM scoring call
- **Daily LLM budget tracking** — prevents runaway API costs with a per-day usage cap
- **Visa sponsorship tagging** — flags each role as Sponsor / No / ND for quick filtering

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Database | SQLite (WAL mode) |
| Scraping | Requests + JSON APIs (Greenhouse, Ashby) |
| Scoring | OpenAI GPT-4o-mini |
| Scheduling | APScheduler + Windows Task Scheduler |
| Web UI | Flask + HTMX |
| Email | Gmail SMTP |
| Resume parsing | pdfplumber + GPT-4o-mini |

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure `config.json`
```json
{
  "openai_api_key": "sk-...",
  "gmail_user": "you@gmail.com",
  "gmail_app_password": "...",
  "digest_recipient": "you@gmail.com",
  "companies": [
    { "name": "Notion", "scraper": "ashby_api", "board_slug": "notion" },
    { "name": "Discord", "scraper": "greenhouse_api", "board_slug": "discord" }
  ]
}
```

### 3. Parse your resume
```bash
python parse_resume.py resume.pdf
```

### 4. Run the web UI
```bash
python app.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

### 5. Start the scheduler (optional)
```bash
python scheduler.py
```

---

## Project Structure

```
job-tracker/
├── app.py              # Flask web UI
├── scraper.py          # Greenhouse + Ashby API scrapers
├── scorer.py           # Two-gate keyword + LLM scoring
├── emailer.py          # Morning/evening digest emails
├── scheduler.py        # APScheduler job runner
├── database.py         # SQLite schema + helpers
├── parse_resume.py     # Resume parsing + summarization
├── main.py             # One-off scrape + score entry point
├── templates/          # Jinja2 HTML templates
└── static/             # CSS
```

---

## Why I Built This

I wanted to apply PM skills — defining a problem, scoping an MVP, iterating on the solution — to my own job search. The result is a tool that would have taken days to assemble manually (tracking sheet + job alerts + email filters) but now runs autonomously and surfaces only what matters.

It's also a practical demonstration of how to integrate LLMs into a workflow where cost, accuracy, and automation all need to be balanced.

---

*Built by Gerald Velasquez*
