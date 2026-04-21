import {
  $, $$, el, loadJSON, renderHeader, renderUpdated, makeSortable,
  fmt, fmtInt, fmtPct, teamBadge, activePill,
} from "./common.js";

renderHeader("players");
renderUpdated();

try {
  const data = await loadJSON("players.json");
  const players = data.players.map((p, i) => ({ ...p, rank: i + 1 }));

  const table = $("#players-table");
  let filters = { q: "", status: "all", pos: "all" };

  const buildRow = (p) => el("tr", null,
    el("td", { class: "left muted" }, String(p.rank)),
    el("td", { class: "left" },
      el("span", { class: "player-name" }, p.name),
      p.ownedBy.length
        ? el("div", { class: "muted", style: "font-size:11px;margin-top:2px" },
            "Owned by: ", p.ownedBy.map((o, i) => [
              i > 0 ? ", " : "",
              el("a", { href: `team.html?owner=${encodeURIComponent(o)}` }, o),
            ]).flat(),
          )
        : null,
    ),
    el("td", null, teamBadge(p.team)),
    el("td", { class: "muted" }, fmtInt(p.G)),
    el("td", { class: "hide-sm muted" }, p.G ? fmt(p.MPPG, 1) : "—"),
    el("td", { class: "big" }, fmt(p.FP, 2)),
    el("td", null, fmt(p.FPPG, 1)),
    el("td", { class: "hide-sm muted" }, fmtInt(p.PTS)),
    el("td", { class: "hide-sm muted" }, fmtInt(p.TRB)),
    el("td", { class: "hide-sm muted" }, fmtInt(p.AST)),
    el("td", { class: "hide-sm muted" }, fmtInt(p.STL)),
    el("td", { class: "hide-sm muted" }, fmtInt(p.BLK)),
    el("td", { class: "hide-sm muted" }, fmtInt(p.TOV)),
    el("td", { class: "hide-sm" }, p.TD ? String(p.TD) : el("span", { class: "muted" }, "0")),
    el("td", null, fmtPct(p.ownership, 0)),
    el("td", null, activePill(p.active)),
  );

  const applyFilters = (rows) => rows.filter((p) => {
    if (filters.status === "active" && !p.active) return false;
    if (filters.status === "eliminated" && p.active) return false;
    if (filters.pos !== "all") {
      const owned = p.ownedBy.length > 0;
      // Use slot prefix on any drafted slot — but players.json doesn't keep slot.
      // Fall back to inferring from name presence in rosters isn't free here, so
      // instead we filter by the "pos" returned from BR totals — G/F/C initial.
      // Our schema doesn't carry that; for now filter by team/name search only.
      // (Position filter still useful if the upstream adds a `pos` field.)
      if (!p.pos || p.pos[0] !== filters.pos) return false;
    }
    if (filters.q) {
      const q = filters.q.toLowerCase();
      if (!p.name.toLowerCase().includes(q) && !(p.team || "").toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const sortable = makeSortable(table, applyFilters(players), buildRow, "FP", "desc");

  $("#search").addEventListener("input", (e) => {
    filters.q = e.target.value.trim();
    sortable.setRows(applyFilters(players));
  });
  $$("#status-filter button").forEach((b) => b.addEventListener("click", () => {
    $$("#status-filter button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    filters.status = b.dataset.status;
    sortable.setRows(applyFilters(players));
  }));
  $$("#position-filter button").forEach((b) => b.addEventListener("click", () => {
    $$("#position-filter button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    filters.pos = b.dataset.pos;
    sortable.setRows(applyFilters(players));
  }));
} catch (e) {
  console.error(e);
  $("#players-table tbody").innerHTML =
    `<tr><td colspan="16"><div class="error-box">Couldn't load player data.</div></td></tr>`;
}
