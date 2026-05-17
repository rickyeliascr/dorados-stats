"""
Scrape Dorados de Chihuahua season stats from Baseball Reference and write
a markdown snapshot file. Designed to run inside the GitHub Actions workflow
update-stats.yml.

Baseball Reference (a Sports Reference site) hides most of its data tables
inside HTML comments — a real browser strips them out via JavaScript, but a
plain HTTP fetcher sees them as <!-- ...table... -->. We extract those
comments first, then parse the inner HTML with BeautifulSoup.

If the fetch ever stops working (anti-bot, layout change, etc.), the script
writes a "failure" header into the file WITHOUT destroying the previous
tables, so the project always has the most recent good data.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Comment

URL = os.environ.get(
    "DORADOS_TEAM_URL",
    "https://www.baseball-reference.com/register/team.cgi?id=1181cb66",
)
OUTPUT_PATH = Path(__file__).parent / "Dorados_Stats.md"
MADRID = ZoneInfo("Europe/Madrid")

# Identify-as-a-real-browser headers. Sports Reference returns 403 for default
# Python user agents.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}


def fetch_html(url: str, attempts: int = 3) -> str:
    """GET the page, retrying briefly on transient failures."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(2 ** i)
    raise RuntimeError(f"Failed to fetch {url}: {last_exc}")


def unwrap_commented_tables(html: str) -> BeautifulSoup:
    """Sports Reference wraps secondary tables inside <!-- ... -->. Pull
    those out and re-parse so BeautifulSoup can find them normally."""
    soup = BeautifulSoup(html, "lxml")
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        text = str(comment)
        if "<table" in text:
            fragment = BeautifulSoup(text, "lxml")
            comment.replace_with(fragment)
    # Re-serialize and re-parse so positions of unwrapped nodes settle.
    return BeautifulSoup(str(soup), "lxml")


def find_table(soup: BeautifulSoup, candidates: list[str]) -> "BeautifulSoup | None":
    """Return the first <table> matching any id in candidates."""
    for table_id in candidates:
        t = soup.find("table", id=table_id)
        if t is not None:
            return t
    return None


def extract_team_header(soup: BeautifulSoup) -> str:
    """Pull the team name / season / record line from the page header."""
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else "Dorados de Chihuahua"
    # Record/standing usually appears in a <p> right after the h1.
    parts: list[str] = [title]
    if h1:
        for sib in h1.find_all_next(["p", "div"], limit=4):
            txt = sib.get_text(" ", strip=True)
            if txt and any(k in txt.lower() for k in ("record", "standing", "rs", "ra", "diff", "place")):
                parts.append(txt)
                break
    return "  \n".join(parts)


def table_to_markdown(table) -> str:
    """Convert a BeautifulSoup <table> to a GitHub-flavoured markdown table.
    Uses the LAST <thead> row as headers (Sports Reference sometimes has a
    spanner row above the real one). Skips header rows interleaved in tbody."""
    # Headers
    thead = table.find("thead")
    header_cells: list[str] = []
    if thead:
        rows = thead.find_all("tr")
        if rows:
            header_cells = [
                th.get_text(" ", strip=True) for th in rows[-1].find_all(["th", "td"])
            ]
    if not header_cells:
        return ""

    # Body rows
    body_rows: list[list[str]] = []
    tbody = table.find("tbody")
    if tbody:
        for tr in tbody.find_all("tr"):
            # Skip mid-table header rows
            if "thead" in (tr.get("class") or []):
                continue
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            row = [c.get_text(" ", strip=True) for c in cells]
            # Pad / trim to header width
            if len(row) < len(header_cells):
                row += [""] * (len(header_cells) - len(row))
            elif len(row) > len(header_cells):
                row = row[: len(header_cells)]
            body_rows.append(row)

    # Footer (team totals)
    tfoot = table.find("tfoot")
    if tfoot:
        for tr in tfoot.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            row = [c.get_text(" ", strip=True) for c in cells]
            if len(row) < len(header_cells):
                row += [""] * (len(header_cells) - len(row))
            elif len(row) > len(header_cells):
                row = row[: len(header_cells)]
            body_rows.append(row)

    if not body_rows:
        return ""

    # Escape pipes in cell content
    def esc(s: str) -> str:
        return s.replace("|", "\\|")

    lines = [
        "| " + " | ".join(esc(h) for h in header_cells) + " |",
        "|" + "|".join(["---"] * len(header_cells)) + "|",
    ]
    for row in body_rows:
        lines.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(lines)


NOTES_BLOCK = """## Notes for future chats

- This project tracks Dorados de Chihuahua (Liga Mexicana de Béisbol) for general-manager-style work.
- This file is the canonical, freshest source of season stats for the roster. Read it before answering roster/performance questions.
- If this file says "awaiting first successful pull" or the Last updated date is more than 2 days old, the scheduled task hasn't completed successfully — flag this to the user.
"""


def render_markdown(header_block: str, batting_md: str, pitching_md: str, stamp: str) -> str:
    return f"""# Dorados de Chihuahua — 2026 LMB Season Stats

**Source:** [Baseball Reference team page]({URL})
**Last updated:** {stamp}
**Update cadence:** Daily at 16:00 Madrid time (overwrite-in-place snapshot, via GitHub Actions)

---

> This file is auto-maintained by a GitHub Actions workflow. Each run pulls the latest season-to-date batting and pitching stats from Baseball Reference and overwrites the sections below. Do not edit by hand — any manual edits will be lost on the next run.

---

## Team Record

{header_block}

## Batting Stats (Season to Date)

{batting_md if batting_md else "_No batting table found on the page this run._"}

## Pitching Stats (Season to Date)

{pitching_md if pitching_md else "_No pitching table found on the page this run._"}

---

{NOTES_BLOCK}"""


def write_failure_marker(reason: str) -> None:
    """Prepend a failure notice but keep the existing tables intact."""
    stamp = datetime.now(MADRID).strftime("%Y-%m-%d %H:%M %Z")
    notice = (
        f"> ⚠️ **Update failed at {stamp}** — {reason}. Showing last successful pull below.\n\n"
    )
    if OUTPUT_PATH.exists():
        existing = OUTPUT_PATH.read_text(encoding="utf-8")
        # Strip any prior failure notice
        existing = re.sub(r"^> ⚠️ \*\*Update failed.*?\n\n", "", existing, flags=re.DOTALL)
        OUTPUT_PATH.write_text(notice + existing, encoding="utf-8")
    else:
        OUTPUT_PATH.write_text(
            notice + render_markdown("_no data yet_", "", "", stamp), encoding="utf-8"
        )


def main() -> int:
    try:
        html = fetch_html(URL)
    except Exception as exc:  # noqa: BLE001
        print(f"FETCH FAILED: {exc}", file=sys.stderr)
        write_failure_marker(f"could not fetch page: {exc}")
        return 1

    soup = unwrap_commented_tables(html)

    # Sports Reference register pages typically use these ids. We try a few
    # in case the page layout shifts.
    batting_table = find_table(
        soup,
        ["team_batting", "players_standard_batting", "batting_standard", "team_batting_register"],
    )
    pitching_table = find_table(
        soup,
        ["team_pitching", "players_standard_pitching", "pitching_standard", "team_pitching_register"],
    )

    if batting_table is None and pitching_table is None:
        print("No batting or pitching tables found — page layout may have changed.", file=sys.stderr)
        write_failure_marker("could not find batting or pitching tables in the page HTML")
        return 1

    batting_md = table_to_markdown(batting_table) if batting_table is not None else ""
    pitching_md = table_to_markdown(pitching_table) if pitching_table is not None else ""
    header_block = extract_team_header(soup)
    stamp = datetime.now(MADRID).strftime("%Y-%m-%d %H:%M %Z")

    OUTPUT_PATH.write_text(
        render_markdown(header_block, batting_md, pitching_md, stamp),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH} at {stamp}")
    print(f"  batting rows: {batting_md.count(chr(10)) - 1 if batting_md else 0}")
    print(f"  pitching rows: {pitching_md.count(chr(10)) - 1 if pitching_md else 0}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
