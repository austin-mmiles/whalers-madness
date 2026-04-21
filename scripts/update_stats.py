#!/usr/bin/env python3
"""
Whalers Madness — pull current NBA playoff stats from NBA.com's CDN JSON
feeds and write the JSON files the static site reads.

Sources (all static JSON, no key required):
  - https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json
      Full-season schedule with per-game status + seriesText (playoff label).
  - https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json
      Today's games w/ live scores and status.
  - https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gameId}.json
      Final or in-progress box score for a single game.

Inputs:
  data/rosters.json       — owner rosters (seed, hand-maintained)

Outputs:
  data/players.json       — per-player stats + fantasy points (drafted only)
  data/leaderboard.json   — owners ranked by total fantasy points
  data/history.json       — daily snapshot of each owner's cumulative total
  data/meta.json          — {season, last_updated, source}
  data/series.json        — playoff series status
  data/today.json         — today's games
  data/.cache/boxscores/  — per-game cache so we don't refetch completed games
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / ".cache"
BOX_CACHE = CACHE / "boxscores"

SEASON = os.environ.get("WM_SEASON", "2026")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

CDN = "https://cdn.nba.com/static/json"
SCHEDULE_URL = f"{CDN}/staticData/scheduleLeagueV2_1.json"
SCOREBOARD_URL = f"{CDN}/liveData/scoreboard/todaysScoreboard_00.json"
BOXSCORE_URL = f"{CDN}/liveData/boxscore/boxscore_{{game_id}}.json"

# NBA uses "PHX" for Phoenix; rosters.json (seeded from basketball-reference)
# uses "PHO". Normalize NBA → roster tricodes at ingest so every downstream
# comparison matches without the frontend caring which source is underneath.
TRICODE_MAP = {"PHX": "PHO"}

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
_NBA_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def http_json(url: str, *, retries: int = 4, timeout: int = 60) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        req = Request(url, headers=_NBA_HEADERS)
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            last = e
            if e.code == 404:
                raise
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
        except (URLError, OSError, json.JSONDecodeError) as e:
            # OSError covers socket.timeout on py3.9 as well as py3.10+ TimeoutError.
            last = e
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
    raise RuntimeError(f"failed to fetch {url}: {last}")


# ---------------------------------------------------------------------------
# Parsing helpers
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


def tricode(code: str | None) -> str:
    if not code:
        return ""
    return TRICODE_MAP.get(code, code)


_ISO_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")


def parse_iso_minutes(s: str) -> float:
    """Parse ISO 8601 durations like 'PT37M26.80S' to a float of minutes."""
    if not s:
        return 0.0
    # Plain "MM:SS" fallback (shouldn't happen with NBA feeds but safe).
    if ":" in s and not s.startswith("PT"):
        m, sec = s.split(":", 1)
        return num(m) + num(sec) / 60.0
    m = _ISO_DUR.match(s)
    if not m:
        return 0.0
    h, mm, ss = m.group(1), m.group(2), m.group(3)
    return (num(h) * 60.0) + num(mm) + (num(ss) / 60.0)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
# Playoff (non play-in) games are prefixed with "004" in the NBA gameId.
PLAYOFF_PREFIX = "004"


def fetch_schedule() -> list[dict]:
    """Return all playoff games (gameId prefix '004') across the season."""
    data = http_json(SCHEDULE_URL)
    games: list[dict] = []
    for gd in data.get("leagueSchedule", {}).get("gameDates", []):
        for g in gd.get("games", []):
            gid = str(g.get("gameId") or "")
            if not gid.startswith(PLAYOFF_PREFIX):
                continue
            games.append(g)
    return games


def game_date(g: dict) -> str:
    """Return the ET calendar date of a game as 'YYYY-MM-DD'."""
    # gameDateEst is midnight-UTC-tagged ET date, e.g. "2026-04-18T00:00:00Z".
    est = g.get("gameDateEst") or g.get("gameDateTimeEst") or ""
    return est[:10] if est else ""


# ---------------------------------------------------------------------------
# Box scores
# ---------------------------------------------------------------------------
def fetch_boxscore(game_id: str, *, live: bool = False) -> dict:
    """Return a game record with per-player stat lines grouped by team.

    Shape (stable across sources):
      {"date": "YYYY-MM-DD", "teams": ["ABC", "XYZ"], "final": bool,
       "players": [
         {"key": ..., "name": ..., "team": "ABC", "opp": "XYZ",
          "MP": 33.5, "PTS": ..., "ORB": ..., ...}
       ]}

    Final games are cached permanently under data/.cache/boxscores/.
    Live games skip the cache so each cron run picks up fresh stats.
    """
    cache_file = BOX_CACHE / f"{game_id}.nba.json"
    if not live and cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    try:
        raw = http_json(BOXSCORE_URL.format(game_id=game_id))
    except HTTPError:
        return {"date": "", "teams": [], "final": False, "players": []}

    g = raw.get("game", {})
    away = g.get("awayTeam", {}) or {}
    home = g.get("homeTeam", {}) or {}
    away_tc = tricode(away.get("teamTricode"))
    home_tc = tricode(home.get("teamTricode"))
    date = (g.get("gameEt") or g.get("gameTimeEst") or "")[:10]
    status = g.get("gameStatus", 0)
    final = status == 3

    players: list[dict] = []
    for side, tc, opp_tc in ((away, away_tc, home_tc), (home, home_tc, away_tc)):
        for p in side.get("players", []) or []:
            # Skip DNPs and players who haven't entered the game.
            if not p.get("played") or p.get("played") == "0":
                continue
            s = p.get("statistics", {}) or {}
            name = p.get("name") or f"{p.get('firstName','')} {p.get('familyName','')}".strip()
            if not name:
                continue
            pts = int(num(s.get("points")))
            orb = int(num(s.get("reboundsOffensive")))
            drb = int(num(s.get("reboundsDefensive")))
            trb = int(num(s.get("reboundsTotal"))) or (orb + drb)
            ast = int(num(s.get("assists")))
            stl = int(num(s.get("steals")))
            blk = int(num(s.get("blocks")))
            tov = int(num(s.get("turnovers")))
            mp = round(parse_iso_minutes(s.get("minutes", "")), 1)
            is_td = sum(1 for v in (pts, trb, ast, stl, blk) if v >= 10) >= 3
            players.append({
                "key": normalize_name(name),
                "name": name,
                "team": tc,
                "opp": opp_tc,
                "MP": mp,
                "PTS": pts, "ORB": orb, "DRB": drb, "TRB": trb,
                "AST": ast, "STL": stl, "BLK": blk, "TOV": tov,
                "TD": 1 if is_td else 0,
            })

    out = {
        "date": date,
        "teams": [away_tc, home_tc],
        "final": final,
        "players": players,
    }
    if final:
        BOX_CACHE.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out))
    return out


# ---------------------------------------------------------------------------
# Series & elimination
# ---------------------------------------------------------------------------
_ROUND_LABEL_RE = re.compile(r"(First Round|Conf(?:erence)? Semifinals|Conf(?:erence)? Finals|NBA Finals|Finals)", re.I)


def _round_label(series_text: str, game_label: str) -> str:
    for src in (game_label or "", series_text or ""):
        m = _ROUND_LABEL_RE.search(src)
        if m:
            return m.group(1)
    return "Playoffs"


def build_series(schedule: list[dict]) -> tuple[list[dict], set[str]]:
    """Aggregate schedule rows into one record per series + eliminated teams.

    Returns (series_list, eliminated_teams).
    """
    # Group games by unordered team pair.
    groups: dict[tuple[str, str], list[dict]] = {}
    for g in schedule:
        a = tricode(g.get("awayTeam", {}).get("teamTricode"))
        h = tricode(g.get("homeTeam", {}).get("teamTricode"))
        if not a or not h:
            continue
        key = tuple(sorted([a, h]))
        groups.setdefault(key, []).append(g)

    series: list[dict] = []
    eliminated: set[str] = set()
    for (a, b), games in groups.items():
        # Tally wins from completed games only (status==3).
        wins = {a: 0, b: 0}
        played = 0
        rnd = "Playoffs"
        last_series_text = ""
        for g in games:
            st = g.get("gameStatus", 0)
            if st != 3:
                continue
            played += 1
            away = tricode(g["awayTeam"].get("teamTricode"))
            home = tricode(g["homeTeam"].get("teamTricode"))
            as_ = int(num(g["awayTeam"].get("score")))
            hs_ = int(num(g["homeTeam"].get("score")))
            if as_ == 0 and hs_ == 0:
                continue
            winner = away if as_ > hs_ else home
            if winner in wins:
                wins[winner] += 1
            rnd = _round_label(g.get("seriesText", ""), g.get("gameLabel", ""))
            last_series_text = g.get("seriesText") or last_series_text

        aw, bw = wins[a], wins[b]
        over = aw == 4 or bw == 4
        if aw == bw:
            leader, trailer, lw, tw, status = a, b, aw, bw, "vs"
        elif aw > bw:
            leader, trailer, lw, tw = a, b, aw, bw
            status = "defeated" if over else "lead"
        else:
            leader, trailer, lw, tw = b, a, bw, aw
            status = "defeated" if over else "lead"

        winner_team = leader if over else ""
        if over:
            eliminated.add(trailer)

        series.append({
            "round": rnd,
            "teams": [a, b],
            "leader": leader,
            "trailer": trailer,
            "wins": [aw, bw],
            "status": status,
            "over": over,
            "winner": winner_team,
            "seriesText": last_series_text,
            "gamesPlayed": played,
        })

    # Only report series that have actually started (avoid showing empty
    # future-round placeholders that the schedule may pre-populate).
    series = [s for s in series if s["gamesPlayed"] > 0 or s["round"] != "Playoffs"]
    series.sort(key=lambda s: (0 if s["over"] else 1, -sum(s["wins"])))
    return series, eliminated


# ---------------------------------------------------------------------------
# Today's games (live scoreboard)
# ---------------------------------------------------------------------------
_STATUS_NAME = {1: "scheduled", 2: "live", 3: "final"}


def fetch_todays_schedule() -> list[dict]:
    """Return every playoff game on today's live scoreboard.

    Each game:
      {"teams": [away, home], "scores": [a, h] | None,
       "status": "final"|"live"|"scheduled", "gameId": str,
       "clock": str, "period": int}
    """
    try:
        sb = http_json(SCOREBOARD_URL)
    except Exception:
        return []
    out: list[dict] = []
    for g in sb.get("scoreboard", {}).get("games", []) or []:
        gid = str(g.get("gameId") or "")
        if not gid.startswith(PLAYOFF_PREFIX):
            continue
        away = tricode(g.get("awayTeam", {}).get("teamTricode"))
        home = tricode(g.get("homeTeam", {}).get("teamTricode"))
        as_ = g.get("awayTeam", {}).get("score")
        hs_ = g.get("homeTeam", {}).get("score")
        status_code = g.get("gameStatus", 1)
        status = _STATUS_NAME.get(status_code, "scheduled")
        have_scores = (as_ is not None and hs_ is not None) and (
            status_code != 1 or (int(num(as_)) + int(num(hs_))) > 0
        )
        out.append({
            "teams": [away, home],
            "scores": [int(num(as_)), int(num(hs_))] if have_scores else None,
            "status": status,
            "gameId": gid,
            "clock": g.get("gameClock") or "",
            "period": int(num(g.get("period"))),
        })
    return out


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


def build_player_records(rosters: dict) -> tuple[dict, dict, list[dict], set[str], list[dict]]:
    """Fetch everything from the NBA CDN and assemble per-player records.

    Returns (players_out, extras, series, eliminated_teams, todays_games).
    """
    schedule = fetch_schedule()
    series, eliminated_teams = build_series(schedule)
    print(f"  playoff games in schedule: {len(schedule)} | eliminated teams: {sorted(eliminated_teams)}")

    # Live scoreboard — overrides schedule status for today's games.
    todays_games = fetch_todays_schedule()
    live_ids = frozenset(t["gameId"] for t in todays_games if t["status"] == "live")
    if live_ids:
        print(f"  live game(s): {sorted(live_ids)}")

    # Which games do we need box scores for? Everything with status >= 2
    # (live or final) from the schedule, plus anything flagged live on the
    # scoreboard (schedule static JSON lags a bit when a game tips off).
    gids_with_data: set[str] = set()
    for g in schedule:
        if g.get("gameStatus", 1) >= 2:
            gids_with_data.add(str(g["gameId"]))
    for t in todays_games:
        if t["status"] in ("live", "final"):
            gids_with_data.add(t["gameId"])

    drafted = drafted_names(rosters)
    needed_keys = {normalize_name(n) for n in drafted}
    key_to_name = {normalize_name(n): n for n in drafted}

    # Aggregate totals from box scores (replaces the former BR totals page).
    totals: dict[str, dict] = {}
    td_counts: dict[str, int] = {}
    daily_fp: dict[str, dict[str, float]] = {}
    game_logs: dict[str, list[dict]] = {}

    print(f"  box scores to fetch: {len(gids_with_data)}")
    for gid in sorted(gids_with_data):
        box = fetch_boxscore(gid, live=(gid in live_ids))
        if not box.get("players"):
            continue
        date = box["date"]
        bucket = daily_fp.setdefault(date, {})
        for line in box["players"]:
            key = line["key"]
            # Roll into aggregated player totals for every player (so
            # free-agent stats aren't lost if they're added to a roster
            # mid-playoffs).
            t = totals.setdefault(key, {
                "player": line["name"], "team": line["team"],
                "G": 0, "MP": 0.0, "PTS": 0, "ORB": 0, "DRB": 0, "TRB": 0,
                "AST": 0, "STL": 0, "BLK": 0, "TOV": 0,
            })
            t["team"] = line["team"]  # latest team (handles mid-playoff trades)
            if line["MP"] > 0:
                t["G"] += 1
            t["MP"] += line["MP"]
            for stat in ("PTS", "ORB", "DRB", "TRB", "AST", "STL", "BLK", "TOV"):
                t[stat] += line[stat]

            if key not in needed_keys:
                continue
            if line["TD"]:
                td_counts[key] = td_counts.get(key, 0) + 1
            fp = fantasy_points(line)
            bucket[key] = bucket.get(key, 0.0) + fp
            pick_name = key_to_name.get(key)
            if pick_name:
                game_logs.setdefault(pick_name, []).append({
                    "date": date,
                    "team": line["team"],
                    "opp": line["opp"],
                    "home": line["team"] == box["teams"][1],
                    "MP": line["MP"],
                    "PTS": line["PTS"], "ORB": line["ORB"], "DRB": line["DRB"],
                    "TRB": line["TRB"], "AST": line["AST"], "STL": line["STL"],
                    "BLK": line["BLK"], "TOV": line["TOV"], "TD": line["TD"],
                    "FP": round(fp, 2),
                })

    for log in game_logs.values():
        log.sort(key=lambda g: g["date"])

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
        stat = totals.get(key, {})
        td = td_counts.get(key, 0)
        rec = {
            "name": pick_name,
            "pos": roster_pos.get(pick_name, ""),
            "team": stat.get("team", ""),
            "G": stat.get("G", 0),
            "MP": int(round(stat.get("MP", 0))),
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
        salary = next(
            (p.get("salary") or 0 for o in rosters["owners"] for p in o["roster"] if p["name"] == pick_name),
            0,
        )
        rec["salary"] = salary
        rec["FPperDollar"] = round(rec["FP"] / salary, 4) if salary else 0.0
        rec["gameLog"] = game_logs.get(pick_name, [])
        players_out[pick_name] = rec

    extras = {
        "eliminated_teams": sorted(eliminated_teams),
        "daily_fp": daily_fp,
    }
    return players_out, extras, series, eliminated_teams, todays_games


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


def attach_movement(leaderboard: list[dict], history: dict) -> None:
    """Compare each owner's current rank to their rank on the most recent
    *prior* day in the history. The result lets the UI show ▲/▼ arrows."""
    days = history.get("days", [])
    if len(days) < 2:
        for row in leaderboard:
            row["prevRank"] = row["rank"]
            row["rankDelta"] = 0
        return
    prev_totals = days[-2]["totals"]
    prev_sorted = sorted(prev_totals.items(), key=lambda kv: kv[1], reverse=True)
    prev_rank = {name: i + 1 for i, (name, _) in enumerate(prev_sorted)}
    for row in leaderboard:
        pr = prev_rank.get(row["owner"], row["rank"])
        row["prevRank"] = pr
        row["rankDelta"] = pr - row["rank"]  # positive = moved up


def build_history(rosters: dict, daily_fp: dict[str, dict[str, float]]) -> dict:
    """Rebuild the full day-by-day history from per-game stat lines."""
    owner_keys: dict[str, set[str]] = {}
    for owner in rosters["owners"]:
        owner_keys[owner["name"]] = {normalize_name(p["name"]) for p in owner["roster"]}

    days_sorted = sorted(d for d in daily_fp.keys() if d)
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

    print(f"Fetching playoff data for {SEASON} (source: nba.com CDN)...")
    players, extras, series, eliminated_teams, todays_games = build_player_records(rosters)
    leaderboard = build_leaderboard(rosters, players)
    history = build_history(rosters, extras["daily_fp"])
    attach_movement(leaderboard, history)

    (DATA / "players.json").write_text(json.dumps({
        "season": SEASON,
        "players": sorted(players.values(), key=lambda p: p["FP"], reverse=True),
    }, indent=2, ensure_ascii=False))

    (DATA / "leaderboard.json").write_text(json.dumps({
        "season": SEASON,
        "leaderboard": leaderboard,
    }, indent=2, ensure_ascii=False))

    (DATA / "history.json").write_text(json.dumps(history, indent=2, ensure_ascii=False))

    (DATA / "series.json").write_text(json.dumps({
        "season": SEASON, "series": series,
    }, indent=2, ensure_ascii=False))

    (DATA / "today.json").write_text(json.dumps({
        "date": datetime.now(timezone.utc).date().isoformat(),
        "games": todays_games,
    }, indent=2, ensure_ascii=False))

    (DATA / "meta.json").write_text(json.dumps({
        "season": SEASON,
        "league": rosters.get("league", "Whalers Madness"),
        "lastUpdated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "nba.com",
        "scoring": WEIGHTS,
        "eliminatedTeams": extras["eliminated_teams"],
    }, indent=2, ensure_ascii=False))

    print(f"Wrote data for {len(players)} players, {len(leaderboard)} owners.")
    if leaderboard:
        top = leaderboard[0]
        print(f"Leader: {top['owner']} with {top['FP']} FP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
