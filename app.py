import json
import sqlite3
import subprocess
from pathlib import Path
from flask import Flask, request, render_template, redirect, url_for

import database

app = Flask(__name__)

STATUSES = ["New", "Saved", "Applied", "Interviewing", "Rejected", "Offer", "Dismissed"]
ALLOWED_SORT_COLS = {"created_at", "match_score", "company", "title", "posted_date"}


def _load_config():
    return json.loads(Path("config.json").read_text(encoding="utf-8"))


def _get_conn():
    config = _load_config()
    database.DB_PATH = config["database"]["path"]
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.template_global()
def score_class(score):
    if score is None:
        return "nd"
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


@app.template_global()
def fmt_salary(low, high):
    if low is None and high is None:
        return "ND"
    parts = []
    if low is not None:
        parts.append(f"${low:,.0f}")
    if high is not None:
        parts.append(f"${high:,.0f}")
    return " - ".join(parts)


@app.route("/")
def index():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    visa = request.args.get("visa", "").strip()
    score_min = request.args.get("score_min", "1").strip()
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc").lower()

    if sort not in ALLOWED_SORT_COLS:
        sort = "created_at"
    if direction not in ("asc", "desc"):
        direction = "desc"

    conditions = []
    params = {}

    if search:
        conditions.append("(title LIKE :search OR company LIKE :search)")
        params["search"] = f"%{search}%"

    if status:
        conditions.append("status = :status")
        params["status"] = status
    else:
        conditions.append("status != 'Dismissed'")

    if visa:
        conditions.append("visa_flag = :visa")
        params["visa"] = visa

    if score_min:
        try:
            params["score_min"] = float(score_min)
            conditions.append("match_score >= :score_min")
        except ValueError:
            pass

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT * FROM jobs {where} ORDER BY {sort} {direction}"

    conn = _get_conn()
    jobs = conn.execute(query, params).fetchall()

    stats = {
        "total": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "applied": conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'Applied'").fetchone()[0],
        "interviewing": conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'Interviewing'").fetchone()[0],
        "new_today": conn.execute("SELECT COUNT(*) FROM jobs WHERE date(created_at) = date('now')").fetchone()[0],
        "avg_score": conn.execute("SELECT ROUND(AVG(match_score), 1) FROM jobs WHERE match_score IS NOT NULL").fetchone()[0],
    }
    conn.close()

    # Build toggle direction for sort links
    toggle_dir = "asc" if direction == "desc" else "desc"

    return render_template(
        "index.html",
        jobs=jobs,
        stats=stats,
        statuses=STATUSES,
        search=search,
        status=status,
        visa=visa,
        score_min=score_min,
        sort=sort,
        direction=direction,
        toggle_dir=toggle_dir,
    )


@app.route("/jobs/<int:job_id>/status", methods=["POST"])
def update_status(job_id):
    new_status = request.form.get("status", "")
    if new_status not in STATUSES:
        return "", 400

    conn = _get_conn()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (new_status, job_id))
    conn.commit()
    conn.close()

    # Return just the re-rendered <select> for HTMX swap
    options_html = "\n".join(
        f'<option value="{s}"{" selected" if s == new_status else ""}>{s}</option>'
        for s in STATUSES
    )
    return (
        f'<select hx-post="/jobs/{job_id}/status" '
        f'hx-trigger="change" hx-target="closest td" hx-swap="innerHTML" name="status">'
        f"{options_html}</select>"
    )


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    conn = _get_conn()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()

    if job is None:
        return "Job not found", 404

    return render_template("job_detail.html", job=job)


@app.route("/status")
def status():
    # Scheduler task status
    task_status = "Unknown"
    task_last_run = ""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "LIST", "/tn", "JobTrackerScheduler"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.splitlines():
                if line.startswith("Status:"):
                    task_status = line.split(":", 1)[1].strip()
                if "Last Run Time" in line:
                    task_last_run = line.split(":", 1)[1].strip()
        else:
            task_status = "Not registered"
    except Exception as e:
        task_status = f"Error: {e}"

    # Last 60 lines of scheduler.log
    log_path = Path("scheduler.log")
    log_lines = []
    if log_path.exists():
        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        log_lines = all_lines[-60:]

    # Recent runs from DB
    conn = _get_conn()
    try:
        runs = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
    except Exception:
        runs = []
    conn.close()

    return render_template(
        "status.html",
        task_status=task_status,
        task_last_run=task_last_run,
        log_lines=log_lines,
        runs=runs,
    )


@app.route("/status/start", methods=["POST"])
def start_scheduler():
    try:
        subprocess.run(
            ["schtasks", "/run", "/tn", "JobTrackerScheduler"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass
    return redirect(url_for("status"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
