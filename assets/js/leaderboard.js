import {
  $, el, loadJSON, renderHeader, renderUpdated, makeSortable,
  medalClass, fmt, fmtInt, fmtPct,
} from "./common.js";

renderHeader("leaderboard");
renderUpdated();

try {
  const [lb, players] = await Promise.all([
    loadJSON("leaderboard.json"),
    loadJSON("players.json"),
  ]);
  const rows = lb.leaderboard;
  const topFP = rows[0]?.FP || 1;

  // Summary cards
  const topScorer = [...players.players].sort((a, b) => b.FP - a.FP)[0];
  const summary = $("#summary");
  summary.append(
    statCard("Leader", rows[0]?.owner || "—", `${fmt(rows[0]?.FP || 0, 2)} FP`),
    statCard("Top Player", topScorer ? topScorer.name : "—",
      topScorer ? `${fmt(topScorer.FP, 2)} FP · ${topScorer.team || "—"}` : ""),
  );

  // Table rows
  const table = $("#leaderboard-table");
  const buildRow = (r) => {
    const tr = el("tr", { class: "leader-row", onclick: () => { location.href = `team.html?owner=${encodeURIComponent(r.owner)}`; } });
    tr.style.cursor = "pointer";
    tr.append(
      el("td", { class: `left ${medalClass(r.rank)}` }, String(r.rank)),
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
    `<tr><td colspan="8"><div class="error-box">Couldn't load league data. Make sure the updater has run at least once.</div></td></tr>`;
}

function statCard(label, value, sub) {
  return el("div", { class: "stat-card" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    sub ? el("div", { class: "sub" }, sub) : null,
  );
}
