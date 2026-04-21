import {
  $, el, loadJSON, renderHeader, renderUpdated, fmt, fmtPct,
} from "./common.js";

renderHeader("teams");
renderUpdated();

try {
  const lb = (await loadJSON("leaderboard.json")).leaderboard;
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
