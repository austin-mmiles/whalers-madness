import {
  $, el, loadJSON, renderHeader, renderUpdated, makeSortable,
  fmt, fmtInt, fmtPct, teamBadge, posBadge, activePill,
} from "./common.js";

renderHeader("players");
renderUpdated();

const params = new URLSearchParams(location.search);
const name = params.get("name") || "";

try {
  const data = await loadJSON("players.json");
  const player = data.players.find((p) => p.name === name);
  if (!player) {
    $("#player-header").append(
      el("h1", { class: "page-title" }, "Player not found"),
      el("p", { class: "page-subtitle" }, el("a", { href: "players.html" }, "← Back to all players")),
    );
    $("#player-gamelog tbody").innerHTML = "";
  } else {
    renderHeader2(player);
    renderSummary(player);
    renderGameLog(player);
  }
} catch (e) {
  console.error(e);
  $("#player-header").append(
    el("div", { class: "error-box" }, "Couldn't load player data."),
  );
}

function renderHeader2(p) {
  const host = $("#player-header");
  host.append(
    el("p", { class: "page-subtitle" },
      el("a", { href: "players.html" }, "← All players"),
    ),
    el("h1", { class: "page-title", style: "display:flex;align-items:center;gap:10px;flex-wrap:wrap" },
      p.name,
      p.pos ? posBadge(p.pos) : null,
      teamBadge(p.team),
      activePill(p.active),
    ),
    el("p", { class: "page-subtitle" },
      p.ownedBy.length
        ? el("span", null,
            "Owned by ",
            p.ownedBy.map((o, i) => [
              i > 0 ? ", " : "",
              el("a", { href: `team.html?owner=${encodeURIComponent(o)}` }, o),
            ]).flat(),
          )
        : el("span", { class: "muted" }, "Undrafted"),
    ),
  );
}

function renderSummary(p) {
  const host = $("#player-summary");
  host.append(
    card("Fantasy Points", fmt(p.FP, 2), `${fmt(p.FPPG, 2)} per game`),
    card("Games", fmtInt(p.G), `${fmt(p.MPPG, 1)} MPG`),
    card("Triple-Doubles", fmtInt(p.TD), p.TD ? `+${fmt(p.TD * 16.75, 1)} FP` : "none"),
    card("Salary", p.salary ? `$${fmt(p.salary, 2)}M` : "—",
      p.salary ? `${fmt(p.FP / p.salary, 2)} FP per $M` : ""),
  );
}

function card(label, value, sub) {
  return el("div", { class: "stat-card" },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    sub ? el("div", { class: "sub" }, sub) : null,
  );
}

function renderGameLog(p) {
  const log = p.gameLog || [];
  const table = $("#player-gamelog");
  if (!log.length) {
    table.tBodies[0].innerHTML = `<tr><td colspan="11" class="muted">No games played yet.</td></tr>`;
    return;
  }
  const build = (g) => {
    const tr = el("tr");
    tr.append(
      el("td", { class: "left" }, g.date),
      el("td", { class: "left" },
        el("span", { class: "muted" }, g.home ? "vs" : "@"),
        " ",
        teamBadge(g.opp),
      ),
      el("td", null, fmt(g.MP, 1)),
      el("td", null, fmtInt(g.PTS)),
      el("td", null, fmtInt(g.TRB)),
      el("td", null, fmtInt(g.AST)),
      el("td", { class: "hide-sm muted" }, fmtInt(g.STL)),
      el("td", { class: "hide-sm muted" }, fmtInt(g.BLK)),
      el("td", { class: "hide-sm muted" }, fmtInt(g.TOV)),
      el("td", { class: "hide-sm" }, g.TD ? "✓" : el("span", { class: "muted" }, "—")),
      el("td", { class: "gamelog-bigfp" }, fmt(g.FP, 2)),
    );
    return tr;
  };
  makeSortable(table, log, build, "date", "desc");
}
