"""
parse_resume.py — One-time resume parser.

Usage:
    python parse_resume.py resume.txt
    python parse_resume.py resume.pdf

Reads the resume, calls gpt-4o-mini to compress it into a structured
500-token summary, and writes the result to config.json["resume_summary"].
"""

import json
import sys
from pathlib import Path

import openai

SYSTEM_PROMPT = """\
You are a resume analyzer for a PM job search. Extract a dense, structured \
plain-text summary of at most 500 tokens. Cover every section below — be \
specific, not generic. Use numbers wherever the resume has them.

1. EXPERIENCE OVERVIEW
   Total years of experience. How many years specifically in PM roles vs \
operations vs analytics. Most recent title and employer.

2. ROLE HISTORY (last 3 positions)
   For each: title | company | approx dates | one-line description of scope \
(team size, budget, product area).

3. PM SKILLS & METHODS
   List concrete tools and methods: roadmapping tools (Jira, Productboard, \
etc.), data tools (SQL, Tableau, Amplitude, Mixpanel, etc.), frameworks \
(OKRs, Agile, Scrum, PRDs, A/B testing), any coding or technical depth.

4. DOMAIN EXPERTISE
   Industries and product verticals (e.g. logistics, telecoms, SaaS, \
e-commerce, fintech, consumer, B2B, marketplace).

5. QUANTIFIED ACHIEVEMENTS
   Up to 4 bullet points, each with a specific number or dollar figure.

6. EDUCATION
   Degree, school, year. Relevant certifications if any.

7. TARGET ROLE SIGNAL
   Based on the resume, what type of PM roles is this person best suited for \
(e.g. growth PM, platform PM, ops PM, data PM)? One sentence.

Write in terse, factual prose. No markdown symbols, no headers, no bullets — \
just dense labeled paragraphs matching the numbered sections above."""


def _extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise SystemExit(
                "pdfplumber is required for PDF parsing.\n"
                "Run: pip install pdfplumber"
            )
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages)
    else:
        return path.read_text(encoding="utf-8")


def parse_resume(resume_path: str, config_path: str = "config.json") -> None:
    config_file = Path(config_path)
    config = json.loads(config_file.read_text(encoding="utf-8"))

    client = openai.OpenAI(api_key=config["openai"]["api_key"])
    model = config["openai"]["model"]

    path = Path(resume_path)
    if not path.exists():
        raise SystemExit(f"File not found: {resume_path}")

    print(f"Reading {path} ...")
    raw_text = _extract_text(path)

    # Truncate to ~6000 words to stay within context safely
    truncated = raw_text[:24000]

    print("Calling OpenAI to compress resume ...")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": truncated},
        ],
        max_tokens=600,
        temperature=0.2,
    )

    summary = resp.choices[0].message.content.strip()

    config["resume_summary"] = summary
    config_file.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nResume summary saved to {config_path}:\n")
    print(summary)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_resume.py <resume.txt|resume.pdf>")
        sys.exit(1)
    parse_resume(sys.argv[1])
