import {
  $, el, loadJSON, renderHeader, renderUpdated, fmt, fmtPct, teamBadge,
} from "./common.js";

renderHeader("teams");
renderUpdated();

try {
  const [lbData, seriesData] = await Promise.all([
    loadJSON("leaderboard.json"),
    loadJSON("series.json").catch(() => ({ series: [] })),
  ]);
  const lb = lbData.leaderboard;
  renderSeries(seriesData.series || []);

  const grid = $("#team-grid");
  grid.innerHTML = "";
  for (const r of lb) {
    const card = el("a", {
        class: "team-card",
        href: `team.html?owner=${encodeURIComponent(r.owner)}`,
        style: "text-decoration:none;color:inherit",
      },
      el("div", { class: "head" },
        el("span", { class: "owner" }, r.owner),
        el("span", { class: "rank-pill" }, `#${r.rank}`),
      ),
      el("div", { class: "fp" }, fmt(r.FP, 1)),
      el("div", { class: "meta" },
        el("span", null, `${r.playersLeft}/10 alive`),
        el("span", null, `${r.G} games`),
        el("span", null, `${r.MP} min`),
      ),
      el("div", { class: "meta" },
        el("span", null, `$ left: ${fmtPct(r.salaryLeftPct, 0)}`),
      ),
    );
    grid.append(card);
  }
} catch (e) {
  console.error(e);
  $("#team-grid").innerHTML =
    `<div class="error-box">Couldn't load team data.</div>`;
}

function renderSeries(series) {
  const host = $("#series-strip");
  if (!host) return;
  if (!series.length) {
    host.append(el("div", { class: "muted" }, "No series data yet."));
    return;
  }
  const order = { "Eastern Conference First Round": 0, "Western Conference First Round": 1 };
  const sorted = [...series].sort((a, b) => {
    const ao = order[a.round] ?? 99;
    const bo = order[b.round] ?? 99;
    return ao - bo;
  });
  for (const s of sorted) {
    const leaderWins = s.wins[s.teams.indexOf(s.leader)];
    const trailerWins = s.wins[s.teams.indexOf(s.trailer)];
    const classes = ["series-card"];
    if (s.over) classes.push("over");
    if (!s.over && leaderWins >= 3) classes.push("risk");
    host.append(
      el("div", { class: classes.join(" ") },
        el("div", { class: "round-label" }, s.round),
        el("div", { class: "matchup" },
          el("span", { class: "leader" }, teamBadge(s.leader), " ", String(leaderWins)),
          el("span", { class: "trailer" }, String(trailerWins), " ", teamBadge(s.trailer)),
        ),
        s.over
          ? el("div", { class: "muted", style: "font-size:11px" }, `${s.winner} advances`)
          : el("div", { class: "muted", style: "font-size:11px" },
              leaderWins + trailerWins === 0 ? "Series tied 0-0" : `${s.leader} leads ${leaderWins}-${trailerWins}`),
      ),
    );
  }
}
