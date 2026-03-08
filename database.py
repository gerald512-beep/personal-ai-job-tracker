import hashlib
import sqlite3
from datetime import datetime, timezone

DB_PATH: str = "jobs.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                hash         TEXT UNIQUE NOT NULL,
                title        TEXT NOT NULL,
                company      TEXT NOT NULL,
                url          TEXT NOT NULL,
                sources      TEXT NOT NULL DEFAULT '',
                posted_date  TEXT NOT NULL,
                salary_min   REAL,
                salary_max   REAL,
                visa_flag    TEXT NOT NULL DEFAULT 'ND',
                match_score  INTEGER,
                match_reason TEXT,
                status       TEXT NOT NULL DEFAULT 'New',
                created_at   TEXT NOT NULL,
                emailed      INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at   TEXT NOT NULL,
                completed_at TEXT,
                jobs_found   INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_hash    ON jobs(hash);
            CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_emailed ON jobs(emailed);
            CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
        """)


def compute_hash(title: str, company: str, url: str) -> str:
    raw = f"{title.strip().lower()}|{company.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _merge_sources(existing: str, new: str) -> str:
    parts = [s.strip() for s in existing.split("|") if s.strip()]
    if new not in parts:
        parts.append(new)
    return "|".join(parts)


def insert_or_update_job(job: dict) -> tuple[str, bool]:
    """Insert a new job or update sources on duplicate. Returns (hash, is_new)."""
    h = compute_hash(job["title"], job["company"], job["url"])
    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT id, sources FROM jobs WHERE hash = ?", (h,)
        ).fetchone()

        if existing:
            merged = _merge_sources(existing["sources"], job.get("sources", ""))
            conn.execute(
                "UPDATE jobs SET sources = ? WHERE hash = ?", (merged, h)
            )
            return h, False
        else:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO jobs
                   (hash, title, company, url, sources, posted_date,
                    salary_min, salary_max, visa_flag, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    h,
                    job["title"],
                    job["company"],
                    job["url"],
                    job.get("sources", ""),
                    job["posted_date"],
                    job.get("salary_min"),
                    job.get("salary_max"),
                    job.get("visa_flag", "ND"),
                    now,
                ),
            )
            return h, True


def get_job_by_hash(hash_val: str) -> sqlite3.Row | None:
    with _get_conn() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE hash = ?", (hash_val,)
        ).fetchone()


def get_recent_runs(limit: int = 1) -> list[sqlite3.Row]:
    with _get_conn() as conn:
        return conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()


def log_run(started_at: str, completed_at: str, jobs_found: int) -> int:
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, completed_at, jobs_found) VALUES (?, ?, ?)",
            (started_at, completed_at, jobs_found),
        )
        return cur.lastrowid


def is_new_company(company_name: str) -> bool:
    """Return True if zero jobs exist for this company."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE company = ?", (company_name,)
        ).fetchone()
        return row[0] == 0


def get_jobs_for_digest() -> list[sqlite3.Row]:
    """Phase 3 stub — return new, un-emailed jobs."""
    with _get_conn() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE status = 'New' AND emailed = 0 ORDER BY created_at DESC"
        ).fetchall()


def mark_jobs_emailed(job_ids: list[int]) -> None:
    """Phase 3 stub — mark jobs as emailed."""
    if not job_ids:
        return
    placeholders = ",".join("?" * len(job_ids))
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET emailed = 1 WHERE id IN ({placeholders})", job_ids
        )
