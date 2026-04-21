#!/usr/bin/env python3
"""
Whalers Madness — pull current NBA playoff stats from basketball-reference.com
and write the JSON files the static site reads.

Inputs:
  data/rosters.json       — owner rosters (seed, hand-maintained)

Outputs:
  data/players.json       — per-player stats + fantasy points (drafted only)
  data/leaderboard.json   — owners ranked by total fantasy points
  data/history.json       — daily snapshot of each owner's cumulative total
  data/meta.json          — {season, last_updated, source}
  data/.cache/boxscores/  — per-game cache so we don't refetch completed games
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / ".cache"
BOX_CACHE = CACHE / "boxscores"

SEASON = os.environ.get("WM_SEASON", "2026")
BASE = "https://www.basketball-reference.com"
UA = "WhalersMadnessBot/1.0 (static-site updater; contact via repo issues)"
REQUEST_DELAY = 3.5  # seconds between requests (respect BR rate limit)

# Fantasy scoring formula — copied verbatim from the source spreadsheet
# (Master ScoreBoard!K2): PTS + 1.75*ORB + 1.25*DRB + 2*AST + 2.5*BLK + 3*STL
# - 0.5*TOV + 16.75*TripleDoubles
WEIGHTS = {
    "PTS": 1.0, "ORB": 1.75, "DRB": 1.25, "AST": 2.0,
    "BLK": 2.5, "STL": 3.0, "TOV": -0.5, "TD": 16.75,
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
_last_request = 0.0

def http_get(url: str, *, retries: int = 3) -> str:
    global _last_request
    for attempt in range(retries):
        wait = REQUEST_DELAY - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        req = Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        try:
            with urlopen(req, timeout=45) as resp:
                _last_request = time.monotonic()
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            _last_request = time.monotonic()
            if e.code == 429 and attempt < retries - 1:
                time.sleep(30 * (attempt + 1))
                continue
            if e.code == 404:
                raise
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except URLError:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"failed to fetch {url}")


# ---------------------------------------------------------------------------
# Basketball-Reference HTML quirks
# ---------------------------------------------------------------------------
# BR wraps many secondary tables inside HTML comments so the default DOM
# parser ignores them. Stripping the comment wrappers exposes the tables.
COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

def uncomment(html: str) -> str:
    return COMMENT_RE.sub(lambda m: m.group(1), html)


class TableParser(HTMLParser):
    """Extract a single <table id=...> as list of row dicts keyed by data-stat."""

    def __init__(self, table_id: str):
        super().__init__(convert_charrefs=True)
        self.table_id = table_id
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cell_stat: str | None = None
        self.cell_text: list[str] = []
        self.cell_href: str | None = None
        self.current_row: dict[str, Any] = {}
        self.rows: list[dict[str, Any]] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") == self.table_id:
            self.in_table = True
            self._depth = 0
        elif self.in_table and tag == "table":
            self._depth += 1
        elif self.in_table and self._depth == 0:
            if tag == "tr":
                cls = a.get("class", "")
                if "thead" in cls:
                    self.in_row = False
                    return
                self.in_row = True
                self.current_row = {}
            elif self.in_row and tag in ("td", "th"):
                self.in_cell = True
                self.cell_stat = a.get("data-stat")
                self.cell_text = []
                self.cell_href = None
            elif self.in_cell and tag == "a":
                self.cell_href = a.get("href")

    def handle_endtag(self, tag):
        if tag == "table":
            if self.in_table and self._depth == 0:
                self.in_table = False
            elif self.in_table and self._depth > 0:
                self._depth -= 1
            return
        if not self.in_table or self._depth > 0:
            return
        if tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
        elif tag in ("td", "th") and self.in_cell:
            text = "".join(self.cell_text).strip()
            if self.cell_stat:
                self.current_row[self.cell_stat] = text
                if self.cell_href:
                    self.current_row[f"{self.cell_stat}__href"] = self.cell_href
            self.in_cell = False
            self.cell_stat = None
            self.cell_href = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text.append(data)


def parse_table(html: str, table_id: str) -> list[dict[str, Any]]:
    """Slice the named <table>...</table> out of the document first.

    The naive approach of feeding the whole page to HTMLParser fails on
    basketball-reference because inline <script> bodies contain unescaped
    markup that desynchronizes the state machine.
    """
    cleaned = uncomment(html)
    pat = re.compile(
        rf'<table[^>]*id="{re.escape(table_id)}"[^>]*>.*?</table>',
        re.DOTALL,
    )
    m = pat.search(cleaned)
    if not m:
        return []
    p = TableParser(table_id)
    p.feed(m.group(0))
    return p.rows


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------
def num(x, *, default=0):
    if x is None or x == "":
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def normalize_name(name: str) -> str:
    """Folded/ASCII-only lowercase form used for lookup across sources."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def fetch_playoff_totals() -> dict[str, dict]:
    """Returns {normalized_name: {player, team, G, MP, PTS, ORB, DRB, AST, STL, BLK, TOV}}."""
    url = f"{BASE}/playoffs/NBA_{SEASON}_totals.html"
    html = http_get(url)
    rows = parse_table(html, "totals_stats") or parse_table(html, "totals-playoffs")
    out: dict[str, dict] = {}
    for r in rows:
        player = r.get("name_display") or r.get("player")
        if not player:
            continue
        key = normalize_name(player)
        team = r.get("team_name_abbr") or r.get("team_id") or ""
        record = {
            "player": player,
            "team": team,
            "G": int(num(r.get("g"))),
            "MP": int(num(r.get("mp"))),
            "PTS": int(num(r.get("pts"))),
            "ORB": int(num(r.get("orb"))),
            "DRB": int(num(r.get("drb"))),
            "TRB": int(num(r.get("trb"))),
            "AST": int(num(r.get("ast"))),
            "STL": int(num(r.get("stl"))),
            "BLK": int(num(r.get("blk"))),
            "TOV": int(num(r.get("tov"))),
        }
        # BR lists traded players with "TOT" and individual team rows — keep TOT
        if key in out and team != "TOT":
            continue
        out[key] = record
    return out


GAME_HREF_RE = re.compile(r"/boxscores/(\d{8}0[A-Z]{3})\.html")

# Earliest possible date to scan. We iterate date-by-date, so this just has
# to be *before* the first playoff game of the season.
PLAYOFF_SCAN_START = {
    "2026": (2026, 4, 15),
}


def fetch_completed_game_ids() -> list[str]:
    """Return all completed *playoff* boxscore IDs (not play-in).

    Strategy:
      1. Start from the playoff bracket page, which is authoritative for
         "is this a real playoff game?" but sometimes lags on the most
         recent games.
      2. For every date on or after the earliest bracket game, also scan
         the per-day scoreboard — this picks up any game the bracket page
         hasn't re-rendered yet.
    Play-in games fall before the earliest bracket date, so they're filtered
    out automatically.
    """
    from datetime import date, timedelta
    bracket_html = http_get(f"{BASE}/playoffs/NBA_{SEASON}.html")
    bracket_ids = sorted(set(GAME_HREF_RE.findall(bracket_html)))

    # Derive playoff start from the bracket, with a sane fallback.
    if bracket_ids:
        first = bracket_ids[0]
        start = date(int(first[0:4]), int(first[4:6]), int(first[6:8]))
    else:
        start = date(*PLAYOFF_SCAN_START.get(SEASON, (int(SEASON), 4, 15)))

    today = datetime.now(timezone.utc).date()
    all_ids = set(bracket_ids)
    d = start
    while d <= today:
        url = f"{BASE}/boxscores/?month={d.month}&day={d.day}&year={d.year}"
        try:
            html = http_get(url)
        except HTTPError:
            html = ""
        all_ids.update(GAME_HREF_RE.findall(html))
        d += timedelta(days=1)
    return sorted(all_ids)


def fetch_boxscore(game_id: str) -> dict:
    """Return ``{"date": "YYYY-MM-DD", "players": [{name_key, PTS, …}]}``.

    Cached per-game because completed box scores never change.
    """
    cache_file = BOX_CACHE / f"{game_id}.v2.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    # game_id format: YYYYMMDD0XXX — e.g. 202604180OKC
    date = f"{game_id[0:4]}-{game_id[4:6]}-{game_id[6:8]}"

    url = f"{BASE}/boxscores/{game_id}.html"
    try:
        html = http_get(url)
    except HTTPError:
        return {"date": date, "players": []}

    players: list[dict] = []
    for m in re.finditer(r'id="(box-[A-Z]{3}-game-basic)"', html):
        tid = m.group(1)
        for r in parse_table(html, tid):
            player = r.get("name_display") or r.get("player")
            if not player or r.get("reason"):
                continue
            pts = int(num(r.get("pts")))
            orb = int(num(r.get("orb")))
            drb = int(num(r.get("drb")))
            trb = int(num(r.get("trb"))) or (orb + drb)
            ast = int(num(r.get("ast")))
            stl = int(num(r.get("stl")))
            blk = int(num(r.get("blk")))
            tov = int(num(r.get("tov")))
            is_td = sum(1 for v in (pts, trb, ast, stl, blk) if v >= 10) >= 3
            players.append({
                "key": normalize_name(player),
                "PTS": pts, "ORB": orb, "DRB": drb, "AST": ast,
                "STL": stl, "BLK": blk, "TOV": tov, "TD": 1 if is_td else 0,
            })

    out = {"date": date, "players": players}
    BOX_CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(out))
    return out


SERIES_OVER_RE = re.compile(
    r'<td[^>]*>\s*<strong><a[^>]*>([A-Z]{3})</a></strong>[^<]*<small>\(4\)</small>\s*over',
    re.IGNORECASE,
)


def fetch_eliminated_teams() -> set[str]:
    """Parse series results from the playoffs page.

    Each completed series renders as ``<strong>WIN (4)</strong> over
    <a>LOSE</a> (n)`` — the team without the bold link is eliminated.
    """
    url = f"{BASE}/playoffs/NBA_{SEASON}.html"
    html = http_get(url)
    eliminated: set[str] = set()
    # Look for "4-3", "4-2" patterns with two team abbrs
    pat = re.compile(
        r"/teams/([A-Z]{3})/\d{4}\.html[^<]*</a>\s*\(4\).*?/teams/([A-Z]{3})/\d{4}\.html[^<]*</a>\s*\([0-3]\)",
        re.DOTALL,
    )
    for winner, loser in pat.findall(html):
        eliminated.add(loser)
    # Alternate series format
    pat2 = re.compile(
        r"<strong>([A-Z]{3})</strong>.{0,40}?\(4\).{0,120}?over.{0,120}?<a[^>]*>([A-Z]{3})</a>.{0,40}?\([0-3]\)",
        re.DOTALL,
    )
    for winner, loser in pat2.findall(html):
        eliminated.add(loser)
    return eliminated


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def fantasy_points(s: dict[str, int | float]) -> float:
    return (
        s.get("PTS", 0) * WEIGHTS["PTS"]
        + s.get("ORB", 0) * WEIGHTS["ORB"]
        + s.get("DRB", 0) * WEIGHTS["DRB"]
        + s.get("AST", 0) * WEIGHTS["AST"]
        + s.get("BLK", 0) * WEIGHTS["BLK"]
        + s.get("STL", 0) * WEIGHTS["STL"]
        + s.get("TOV", 0) * WEIGHTS["TOV"]
        + s.get("TD", 0) * WEIGHTS["TD"]
    )


def drafted_names(rosters: dict) -> set[str]:
    names: set[str] = set()
    for owner in rosters["owners"]:
        for pick in owner["roster"]:
            names.add(pick["name"])
    return names


def build_player_records(rosters: dict) -> tuple[dict, dict]:
    totals = fetch_playoff_totals()
    eliminated_teams = fetch_eliminated_teams()
    print(f"  playoff totals: {len(totals)} players | eliminated teams: {sorted(eliminated_teams)}")

    drafted = drafted_names(rosters)
    needed_keys = {normalize_name(n) for n in drafted}
    totals_for_drafted = {k: v for k, v in totals.items() if k in needed_keys}

    # Per-game stat lines drive both the triple-double column and the
    # per-day history snapshots. Every playoff box score is fetched once
    # and cached forever under data/.cache/boxscores/.
    td_counts: dict[str, int] = {}
    daily_fp: dict[str, dict[str, float]] = {}  # {date: {name_key: fp_added}}
    if totals_for_drafted:
        game_ids = fetch_completed_game_ids()
        print(f"  completed playoff games: {len(game_ids)}")
        for gid in game_ids:
            box = fetch_boxscore(gid)
            date = box["date"]
            bucket = daily_fp.setdefault(date, {})
            for line in box["players"]:
                if line["key"] not in needed_keys:
                    continue
                if line["TD"]:
                    td_counts[line["key"]] = td_counts.get(line["key"], 0) + 1
                bucket[line["key"]] = bucket.get(line["key"], 0.0) + fantasy_points(line)
    extras_daily = daily_fp

    players_out: dict[str, dict] = {}
    ownership: dict[str, list[str]] = {}
    for owner in rosters["owners"]:
        for pick in owner["roster"]:
            ownership.setdefault(pick["name"], []).append(owner["name"])

    # Position (G/F/C) from rosters seed — authoritative since slot encodes it
    roster_pos: dict[str, str] = {}
    for owner in rosters["owners"]:
        for pick in owner["roster"]:
            roster_pos.setdefault(pick["name"], pick.get("position") or pick["slot"][0])

    for pick_name in drafted:
        key = normalize_name(pick_name)
        stat = totals_for_drafted.get(key, {})
        td = td_counts.get(key, 0)
        rec = {
            "name": pick_name,
            "pos": roster_pos.get(pick_name, ""),
            "team": stat.get("team", ""),
            "G": stat.get("G", 0),
            "MP": stat.get("MP", 0),
            "PTS": stat.get("PTS", 0),
            "ORB": stat.get("ORB", 0),
            "DRB": stat.get("DRB", 0),
            "TRB": stat.get("TRB", stat.get("ORB", 0) + stat.get("DRB", 0)),
            "AST": stat.get("AST", 0),
            "STL": stat.get("STL", 0),
            "BLK": stat.get("BLK", 0),
            "TOV": stat.get("TOV", 0),
            "TD": td,
        }
        rec["FP"] = round(fantasy_points(rec), 2)
        rec["FPPG"] = round(rec["FP"] / rec["G"], 2) if rec["G"] else 0.0
        rec["MPPG"] = round(rec["MP"] / rec["G"], 1) if rec["G"] else 0.0
        team = rec["team"] or next(
            (p["team"] for o in rosters["owners"] for p in o["roster"] if p["name"] == pick_name),
            "",
        )
        rec["team"] = team
        rec["active"] = bool(team) and team not in eliminated_teams
        rec["ownedBy"] = sorted(ownership.get(pick_name, []))
        rec["ownership"] = len(rec["ownedBy"]) / len(rosters["owners"])
        players_out[pick_name] = rec

    return players_out, {
        "eliminated_teams": sorted(eliminated_teams),
        "daily_fp": extras_daily,
    }


def build_leaderboard(rosters: dict, players: dict) -> list[dict]:
    rows: list[dict] = []
    for owner in rosters["owners"]:
        total_fp = 0.0
        total_games = 0
        total_minutes = 0
        players_left = 0
        salary_left = 0.0
        salary_total = 0.0
        roster_detail = []
        for pick in owner["roster"]:
            p = players.get(pick["name"], {})
            fp = p.get("FP", 0)
            g = p.get("G", 0)
            mp = p.get("MP", 0)
            active = p.get("active", False)
            sal = pick.get("salary") or 0
            total_fp += fp
            total_games += g
            total_minutes += mp
            salary_total += sal
            if active:
                players_left += 1
                salary_left += sal
            roster_detail.append({
                "slot": pick["slot"], "name": pick["name"],
                "team": p.get("team") or pick.get("team"),
                "position": pick.get("position"),
                "salary": sal, "G": g, "MP": mp, "FP": round(fp, 2),
                "FPPG": p.get("FPPG", 0), "active": active,
                "TD": p.get("TD", 0),
            })
        rows.append({
            "owner": owner["name"],
            "FP": round(total_fp, 2),
            "G": total_games,
            "MP": total_minutes,
            "playersLeft": players_left,
            "salaryLeftPct": round(salary_left / salary_total, 4) if salary_total else 0,
            "salaryLeft": round(salary_left, 2),
            "salaryTotal": round(salary_total, 2),
            "roster": roster_detail,
        })
    rows.sort(key=lambda r: r["FP"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def build_history(rosters: dict, daily_fp: dict[str, dict[str, float]]) -> dict:
    """Rebuild the full day-by-day history from per-game stat lines.

    This is derived purely from the cached box scores, so every run produces
    a consistent series back to the first playoff game — no drift from
    missed cron runs or timezone edges.
    """
    # owner → list of (player_key, salary_is_irrelevant)
    owner_keys: dict[str, set[str]] = {}
    for owner in rosters["owners"]:
        owner_keys[owner["name"]] = {normalize_name(p["name"]) for p in owner["roster"]}

    days_sorted = sorted(daily_fp.keys())
    out_days: list[dict] = []
    running = {name: 0.0 for name in owner_keys}
    for date in days_sorted:
        bucket = daily_fp[date]
        for owner, keys in owner_keys.items():
            running[owner] += sum(bucket.get(k, 0.0) for k in keys)
        out_days.append({
            "date": date,
            "totals": {o: round(v, 2) for o, v in running.items()},
        })
    return {"days": out_days}


def main() -> int:
    rosters_path = DATA / "rosters.json"
    if not rosters_path.exists():
        print(f"missing {rosters_path}", file=sys.stderr)
        return 1
    rosters = json.loads(rosters_path.read_text())

    print(f"Fetching playoff data for {SEASON}...")
    players, extras = build_player_records(rosters)
    leaderboard = build_leaderboard(rosters, players)
    history = build_history(rosters, extras["daily_fp"])

    (DATA / "players.json").write_text(json.dumps({
        "season": SEASON,
        "players": sorted(players.values(), key=lambda p: p["FP"], reverse=True),
    }, indent=2, ensure_ascii=False))

    (DATA / "leaderboard.json").write_text(json.dumps({
        "season": SEASON,
        "leaderboard": leaderboard,
    }, indent=2, ensure_ascii=False))

    (DATA / "history.json").write_text(json.dumps(history, indent=2, ensure_ascii=False))

    (DATA / "meta.json").write_text(json.dumps({
        "season": SEASON,
        "league": rosters.get("league", "Whalers Madness"),
        "lastUpdated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "basketball-reference.com",
        "scoring": WEIGHTS,
        "eliminatedTeams": extras["eliminated_teams"],
    }, indent=2, ensure_ascii=False))

    print(f"Wrote data for {len(players)} players, {len(leaderboard)} owners.")
    top = leaderboard[0]
    print(f"Leader: {top['owner']} with {top['FP']} FP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
