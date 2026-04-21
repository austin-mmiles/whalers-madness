import {
  $, el, loadJSON, renderHeader, renderUpdated, fmt, fmtInt, fmtPct,
  medalClass, teamBadge, posBadge, activePill,
} from "./common.js";

renderHeader("teams");
renderUpdated();

const params = new URLSearchParams(location.search);
const ownerName = params.get("owner");
const root = $("#team-page");

try {
  if (!ownerName) throw new Error("No owner specified.");
  const lb = (await loadJSON("leaderboard.json")).leaderboard;
  const entry = lb.find((r) => r.owner === ownerName);
  if (!entry) throw new Error(`Owner "${ownerName}" not found.`);

  document.title = `${ownerName} · Whalers Madness`;
  root.innerHTML = "";

  // Header summary
  const prev = lb[entry.rank - 2];
  const behindLeader = lb[0].FP - entry.FP;
  const subText = entry.rank === 1
    ? `#1 · leading by ${fmt(entry.FP - (lb[1]?.FP || 0), 2)} FP`
    : `#${entry.rank} · ${fmt(behindLeader, 2)} FP behind leader${prev ? ` · ${fmt(prev.FP - entry.FP, 2)} behind #${entry.rank - 1}` : ""}`;

  root.append(
    el("div", { class: "team-summary" },
      el("div", null,
        el("h2", null, ownerName),
        el("div", { class: "sub" }, subText),
      ),
      el("div", { style: "text-align:right" },
        el("div", { class: "big-fp" }, fmt(entry.FP, 2)),
        el("div", { class: "sub muted" }, "Fantasy Points"),
      ),
    ),
  );

  // Stat row
  const statBox = el("div", { class: "stat-row" },
    stat("Players Alive", `${entry.playersLeft} / 10`),
    stat("Games Played", fmtInt(entry.G)),
    stat("Minutes", fmtInt(entry.MP)),
    stat("Salary Alive", `${fmt(entry.salaryLeft, 2)} / ${fmt(entry.salaryTotal, 2)}`, fmtPct(entry.salaryLeftPct, 0)),
  );
  root.append(statBox);

  // Roster table
  root.append(
    el("h3", { class: "section-title" }, "Roster"),
    rosterTable(entry.roster),
  );

  // Comparison — ownership overlap with other owners (shared players)
  const players = (await loadJSON("players.json")).players;
  const myPlayers = new Set(entry.roster.map((p) => p.name));
  const overlaps = lb
    .filter((o) => o.owner !== ownerName)
    .map((o) => ({
      owner: o.owner,
      rank: o.rank,
      FP: o.FP,
      shared: o.roster.filter((p) => myPlayers.has(p.name)).map((p) => p.name),
    }))
    .filter((o) => o.shared.length > 0)
    .sort((a, b) => b.shared.length - a.shared.length);

  if (overlaps.length > 0) {
    root.append(
      el("h3", { class: "section-title" }, "Shared Players"),
      el("div", { class: "table-wrap" },
        el("div", { class: "table-scroll" },
          el("table", { class: "stats" },
            el("thead", null,
              el("tr", null,
                el("th", { class: "left" }, "Owner"),
                el("th", null, "Rank"),
                el("th", null, "FP"),
                el("th", { class: "left" }, `Shared players (${myPlayers.size} in my roster)`),
              ),
            ),
            el("tbody", null, ...overlaps.map((o) => el("tr", null,
              el("td", { class: "left" },
                el("a", { href: `team.html?owner=${encodeURIComponent(o.owner)}`, class: "owner-name" }, o.owner),
              ),
              el("td", null, `#${o.rank}`),
              el("td", { class: "muted" }, fmt(o.FP, 1)),
              el("td", { class: "left muted" }, o.shared.join(", ")),
            ))),
          ),
        ),
      ),
    );
  }
} catch (e) {
  console.error(e);
  root.innerHTML = `<div class="error-box">${e.message}</div>
    <p style="text-align:center"><a href="teams.html">Back to all teams</a></p>`;
}

function stat(label, value, sub) {
  return el("div", { class: "stat-card" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    sub ? el("div", { class: "sub" }, sub) : null,
  );
}

function rosterTable(roster) {
  // Order by slot (G1, G2, ...)
  const slotOrder = ["G1","G2","G3","G4","F1","F2","F3","F4","C1","C2"];
  const sorted = [...roster].sort(
    (a, b) => slotOrder.indexOf(a.slot) - slotOrder.indexOf(b.slot)
  );
  const rosterFP = sorted.reduce((s, p) => s + (p.FP || 0), 0) || 1;

  return el("div", { class: "table-wrap" },
    el("div", { class: "table-scroll" },
      el("table", { class: "stats" },
        el("thead", null,
          el("tr", null,
            el("th", { class: "left" }, "Slot"),
            el("th", { class: "left" }, "Player"),
            el("th", null, "Team"),
            el("th", null, "Salary"),
            el("th", null, "G"),
            el("th", null, "MP"),
            el("th", null, "FP"),
            el("th", null, "FPPG"),
            el("th", null, "TD"),
            el("th", null, "Status"),
            el("th", { class: "left" }, "Share of team"),
          ),
        ),
        el("tbody", null, ...sorted.map((p) => {
          const pct = (p.FP || 0) / rosterFP * 100;
          return el("tr", null,
            el("td", { class: "left" }, posBadge(p.slot)),
            el("td", { class: "left player-name" }, p.name),
            el("td", null, teamBadge(p.team)),
            el("td", { class: "muted" }, p.salary ? `$${fmt(p.salary, 2)}` : "—"),
            el("td", { class: "muted" }, fmtInt(p.G)),
            el("td", { class: "muted" }, fmtInt(p.MP)),
            el("td", { class: "big" }, fmt(p.FP, 2)),
            el("td", { class: "muted" }, fmt(p.FPPG, 1)),
            el("td", { class: "muted" }, p.TD ? String(p.TD) : "—"),
            el("td", null, activePill(p.active)),
            el("td", { class: "bar-cell left" },
              `${pct.toFixed(1)}%`,
              el("div", { class: "bar-track" },
                el("div", { class: "bar-fill", style: `width:${pct.toFixed(1)}%` })
              ),
            ),
          );
        })),
      ),
    ),
  );
}
