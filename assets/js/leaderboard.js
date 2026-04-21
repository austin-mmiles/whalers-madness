import {
  $, el, loadJSON, renderHeader, renderUpdated, makeSortable,
  medalClass, fmt, fmtInt, fmtPct, movementBadge, teamBadge,
} from "./common.js";

renderHeader("leaderboard");
renderUpdated();

try {
  const [lb, players, today, series] = await Promise.all([
    loadJSON("leaderboard.json"),
    loadJSON("players.json"),
    loadJSON("today.json").catch(() => ({ games: [] })),
    loadJSON("series.json").catch(() => ({ series: [] })),
  ]);
  const rows = lb.leaderboard;
  const topFP = rows[0]?.FP || 1;

  const topScorer = [...players.players].sort((a, b) => b.FP - a.FP)[0];
  const summary = $("#summary");
  summary.append(
    statCard("Leader", rows[0]?.owner || "—", `${fmt(rows[0]?.FP || 0, 2)} FP`),
    statCard("Top Player", topScorer ? topScorer.name : "—",
      topScorer ? `${fmt(topScorer.FP, 2)} FP · ${topScorer.team || "—"}` : ""),
  );

  renderTodayStrip(today, series);
  renderInsights(players.players, rows);

  const table = $("#leaderboard-table");
  const buildRow = (r) => {
    const tr = el("tr", { class: "leader-row", onclick: () => { location.href = `team.html?owner=${encodeURIComponent(r.owner)}`; } });
    tr.style.cursor = "pointer";
    tr.append(
      el("td", { class: `left ${medalClass(r.rank)}` }, String(r.rank)),
      el("td", { class: "left hide-sm" }, movementBadge(r.rankDelta)),
      el("td", { class: "left" }, el("a", { href: `team.html?owner=${encodeURIComponent(r.owner)}`, class: "owner-name" }, r.owner)),
      el("td", { class: "big" }, fmt(r.FP, 2)),
      el("td", { class: "hide-sm muted" }, fmtInt(r.G)),
      el("td", { class: "hide-sm muted" }, fmtInt(r.MP)),
      el("td", null, `${r.playersLeft}/10`),
      el("td", { class: "hide-sm" }, fmtPct(r.salaryLeftPct, 0)),
      el("td", { class: "bar-cell hide-sm left" },
        fmt(r.FP, 0),
        el("div", { class: "bar-track" },
          el("div", { class: "bar-fill", style: `width:${(r.FP / topFP * 100).toFixed(1)}%` })
        ),
      ),
    );
    return tr;
  };
  makeSortable(table, rows, buildRow, "FP", "desc");
} catch (err) {
  console.error(err);
  $("#leaderboard-table tbody").innerHTML =
    `<tr><td colspan="9"><div class="error-box">Couldn't load league data. Make sure the updater has run at least once.</div></td></tr>`;
}

function statCard(label, value, sub) {
  return el("div", { class: "stat-card" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    sub ? el("div", { class: "sub" }, sub) : null,
  );
}

function renderTodayStrip(today, series) {
  const host = $("#today");
  if (!host) return;
  const games = (today && today.games) || [];
  const seriesByTeam = new Map();
  for (const s of (series.series || [])) {
    seriesByTeam.set(s.teams[0], s);
    seriesByTeam.set(s.teams[1], s);
  }
  if (!games.length) {
    host.append(
      el("div", { class: "today-empty" },
        el("span", { class: "today-label" }, "Today"),
        el("span", null, "No games scheduled."),
      ),
    );
    return;
  }
  const label = el("div", { class: "today-label" }, `Today · ${games.length} game${games.length > 1 ? "s" : ""}`);
  const strip = el("div", { class: "today-games" });
  for (const g of games) {
    const [a, b] = g.teams;
    const info = seriesByTeam.get(a) || seriesByTeam.get(b);
    const scoreNode = g.scores
      ? el("span", { class: "today-score" }, `${g.scores[0]}–${g.scores[1]}`)
      : el("span", { class: "today-status" }, g.status === "scheduled" ? "Scheduled" : "Live");
    const statusPill = el("span", { class: `today-pill ${g.status}` },
      g.status === "final" ? "Final" : g.status === "live" ? "Live" : "Soon");
    const cell = el("div", { class: "today-game" },
      el("div", { class: "today-matchup" }, teamBadge(a), el("span", { class: "muted" }, "vs"), teamBadge(b)),
      scoreNode,
      statusPill,
      info ? el("div", { class: "today-series" }, `${info.leader} ${info.wins[info.teams.indexOf(info.leader)]}–${info.wins[info.teams.indexOf(info.trailer)]}`) : null,
    );
    strip.append(cell);
  }
  host.append(label, strip);
}

function renderInsights(players, leaderboard) {
  const host = $("#insights");
  if (!host) return;

  // Most/least owned among drafted players
  const owned = [...players].filter((p) => p.ownedBy && p.ownedBy.length > 0);
  const mostOwned = [...owned].sort((a, b) => (b.ownedBy?.length || 0) - (a.ownedBy?.length || 0) || b.FP - a.FP)[0];
  const soloGems = [...owned]
    .filter((p) => p.ownedBy.length === 1 && p.FP > 0)
    .sort((a, b) => b.FP - a.FP)[0];

  // Best FP/$ efficiency (among players with salary)
  const withSalary = owned.filter((p) => p.salary && p.FP > 0);
  const bestValue = withSalary.sort((a, b) => b.FPperDollar - a.FPperDollar)[0];

  // Tightest race: top 3 scoreboard gap
  const top3 = leaderboard.slice(0, 3);
  const raceSub = top3.length >= 2
    ? `${fmt(top3[0].FP - top3[1].FP, 1)} FP gap to 2nd`
    : "";

  const cards = [];
  if (mostOwned) {
    cards.push(card("Most Owned", mostOwned.name,
      `${mostOwned.ownedBy.length} owner${mostOwned.ownedBy.length > 1 ? "s" : ""} · ${fmt(mostOwned.FP, 0)} FP`));
  }
  if (soloGems) {
    cards.push(card("Best Solo Pick", soloGems.name,
      `${soloGems.ownedBy[0]} · ${fmt(soloGems.FP, 0)} FP`));
  }
  if (bestValue) {
    cards.push(card("Best Value", bestValue.name,
      `${fmt(bestValue.FP, 0)} FP @ $${fmt(bestValue.salary, 2)}M`));
  }
  if (top3[0]) {
    cards.push(card("Top of the Pack", top3[0].owner, raceSub || "—"));
  }
  host.append(
    el("h2", { class: "section-title" }, "Insights"),
    el("div", { class: "stat-row" }, ...cards),
  );
}

function card(label, value, sub) {
  return el("div", { class: "stat-card insight-card" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    sub ? el("div", { class: "sub" }, sub) : null,
  );
}
