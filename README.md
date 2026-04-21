# Whalers Madness — 2026 NBA Playoff Fantasy Site

A static, zero-cost website for the Whalers Madness fantasy NBA playoff
competition. Auto-refreshes every 30 minutes from
[basketball-reference.com](https://www.basketball-reference.com/) via GitHub
Actions, deploys to GitHub Pages.

## What's in here

```
.
├── index.html            ← Leaderboard (main page)
├── teams.html            ← All 14 rosters (grid)
├── team.html             ← Single team detail (?owner=Name)
├── players.html          ← Every drafted player, sortable/filterable
├── history.html          ← Cumulative-points chart by day
├── assets/
│   ├── css/style.css
│   └── js/               ← Vanilla ES-modules (no build step)
├── data/
│   ├── rosters.json      ← Owners + picks (hand-maintained seed)
│   ├── players.json      ← Auto-generated stats per drafted player
│   ├── leaderboard.json  ← Auto-generated standings
│   ├── history.json      ← Daily snapshot log
│   └── meta.json         ← {lastUpdated, season, scoring, …}
├── scripts/update_stats.py
└── .github/workflows/update.yml
```

Everything the browser fetches is under `data/`. The site is fully static —
no backend — so GitHub Pages serves it for free.

## Scoring

Identical to the source spreadsheet:

```
FP = PTS + 1.75·ORB + 1.25·DRB + 2·AST + 2.5·BLK + 3·STL − 0.5·TOV + 16.75·TripleDoubles
```

## First-time setup

1. **Create a public GitHub repo** and push this folder to `main`.
2. In the repo, go to **Settings → Pages** and set:
   - **Source**: *GitHub Actions*
3. **Settings → Actions → General → Workflow permissions**: select
   *Read and write permissions* (the action commits updated JSON back to the
   repo).
4. Trigger the first run: **Actions → "Update playoff data & deploy" → Run
   workflow**.
5. Your site is live at `https://<user>.github.io/<repo>/`.

That's it — the workflow's `*/30 * * * *` cron keeps the site fresh.

## Updating rosters

`data/rosters.json` is the source of truth for who owns whom. To change a
roster, edit that file and commit — the next scheduled run will pick it up.

If the league ever re-seeds from the master spreadsheet, re-run the snippet
at the top of [`scripts/seed_from_xlsx.py`](scripts/seed_from_xlsx.py) (see
below) with the new xlsx path.

## Running locally

```bash
python3 scripts/update_stats.py        # pulls current stats → writes data/*.json
python3 -m http.server 8000            # then open http://localhost:8000/
```

The updater is vanilla Python (stdlib only) — nothing to `pip install`.

## How stats are collected

`scripts/update_stats.py`:

1. Fetches the playoff totals table from
   `basketball-reference.com/playoffs/NBA_2026_totals.html`.
2. Fetches every completed game's box score (cached forever under
   `data/.cache/boxscores/` so the action stays cheap) and counts
   triple-doubles for drafted players.
3. Parses the playoffs bracket page to determine which teams have been
   eliminated (used for the "Alive / Out" pill).
4. Joins those three sources onto `rosters.json`, computes fantasy points
   using the scoring formula above, and writes:
   - `players.json` — per-player totals
   - `leaderboard.json` — owner rankings with per-roster breakdown
   - `history.json` — adds a snapshot for today's date
   - `meta.json` — last-updated timestamp

Rate-limits: 3.5s between requests to basketball-reference, with automatic
back-off on 429.

## Customisation tips

- **Change league name / season**: edit the header text in `index.html`,
  `teams.html`, etc., and set `WM_SEASON` env var in the workflow.
- **Add/remove stats columns**: edit `players.html` headers and the
  `buildRow` in `assets/js/players.js`.
- **Styling**: CSS custom properties at the top of `assets/css/style.css`
  control the whole colour palette.

## Seeding rosters from the source spreadsheet

If you start from a fresh xlsx dump:

```python
# scripts/seed_from_xlsx.py (one-liner shown, use in a scratch file)
from openpyxl import load_workbook
import json

wb = load_workbook("2026 Whalers Madness Fantasy NBA Playoffs Competition.xlsx", data_only=True)
meta = {r[1]: {"position": r[2], "team": r[3], "salary": r[4]}
        for r in wb["Master ScoreBoard"].iter_rows(min_row=2, values_only=True) if r[1]}
slots = ["G1","G2","G3","G4","F1","F2","F3","F4","C1","C2"]
owners = []
for row in wb["Team Breakdowns"].iter_rows(min_row=3, max_row=16, values_only=True):
    if not row[0]: continue
    owners.append({"name": row[0], "roster": [
        {"slot": s, "name": row[1+i], **meta.get(row[1+i], {})}
        for i, s in enumerate(slots) if row[1+i]
    ]})
json.dump({"season":"2026","league":"Whalers Madness","positions":slots,"owners":owners},
          open("data/rosters.json","w"), indent=2, ensure_ascii=False)
```
