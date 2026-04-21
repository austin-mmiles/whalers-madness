import {
  $, $$, el, loadJSON, renderHeader, renderUpdated, fmt,
} from "./common.js";

renderHeader("history");
renderUpdated();

const COLORS = [
  "#5eb8ff","#ff7eb6","#2ecc71","#f5c443","#b594ff","#ff8a4c","#64d8c6",
  "#ff5a5f","#9ccc65","#f48fb1","#7c9cff","#ffab40","#80cbc4","#c39bd3",
];

try {
  const hist = await loadJSON("history.json");
  if (!hist.days || hist.days.length === 0) {
    throw new Error("No history captured yet — come back after the next update.");
  }
  const lb = (await loadJSON("leaderboard.json")).leaderboard;
  const owners = lb.map((r) => r.owner);

  // Build per-owner series, forward-filling missing days with previous value
  const dates = hist.days.map((d) => d.date);
  const series = {};
  owners.forEach((o, i) => {
    series[o] = { color: COLORS[i % COLORS.length], on: true, values: [] };
  });
  let last = Object.fromEntries(owners.map((o) => [o, 0]));
  for (const d of hist.days) {
    for (const o of owners) {
      if (d.totals[o] !== undefined) last[o] = d.totals[o];
      series[o].values.push(last[o]);
    }
  }

  const state = { series, dates };
  drawChart(state);
  buildLegend(state);

  window.addEventListener("resize", () => drawChart(state));
} catch (e) {
  console.error(e);
  $("#chart").innerHTML = `<div class="error-box">${e.message}</div>`;
}

function drawChart(state) {
  const wrap = $("#chart");
  wrap.innerHTML = "";
  const W = Math.max(600, wrap.clientWidth - 32);
  const H = 420;
  const P = { l: 48, r: 16, t: 16, b: 36 };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const active = Object.entries(state.series).filter(([, v]) => v.on);
  const maxY = Math.max(
    1,
    ...active.flatMap(([, v]) => v.values),
  );
  const niceMax = niceCeil(maxY);
  const xAt = (i) => P.l + (i / Math.max(1, state.dates.length - 1)) * (W - P.l - P.r);
  const yAt = (v) => P.t + (1 - v / niceMax) * (H - P.t - P.b);

  // Grid
  const grid = document.createElementNS("http://www.w3.org/2000/svg", "g");
  grid.setAttribute("stroke", "#242a3a");
  grid.setAttribute("stroke-width", "1");
  const steps = 5;
  for (let s = 0; s <= steps; s++) {
    const y = P.t + (s / steps) * (H - P.t - P.b);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", P.l); line.setAttribute("x2", W - P.r);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    grid.appendChild(line);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", P.l - 6); t.setAttribute("y", y + 4);
    t.setAttribute("fill", "#8c93a4");
    t.setAttribute("font-size", "11");
    t.setAttribute("text-anchor", "end");
    t.textContent = Math.round(niceMax * (1 - s / steps));
    grid.appendChild(t);
  }
  svg.appendChild(grid);

  // X axis labels (a few evenly-spaced dates)
  const labelCount = Math.min(state.dates.length, 7);
  for (let i = 0; i < labelCount; i++) {
    const idx = Math.round(i * (state.dates.length - 1) / Math.max(1, labelCount - 1));
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", xAt(idx)); t.setAttribute("y", H - 12);
    t.setAttribute("fill", "#8c93a4");
    t.setAttribute("font-size", "11");
    t.setAttribute("text-anchor", "middle");
    t.textContent = fmtShortDate(state.dates[idx]);
    svg.appendChild(t);
  }

  // Lines
  for (const [owner, v] of active) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const d = v.values.map((val, i) =>
      `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(val).toFixed(1)}`
    ).join(" ");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", v.color);
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    svg.appendChild(path);

    // End-point dot + owner label
    const last = v.values.length - 1;
    const cx = xAt(last), cy = yAt(v.values[last]);
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy);
    dot.setAttribute("r", "3"); dot.setAttribute("fill", v.color);
    svg.appendChild(dot);
  }
  wrap.appendChild(svg);
}

function buildLegend(state) {
  const legend = $("#legend");
  legend.innerHTML = "";
  for (const [owner, v] of Object.entries(state.series)) {
    const entry = el("span", { class: `entry on` },
      el("span", { class: "swatch", style: `background:${v.color}` }),
      owner,
    );
    entry.addEventListener("click", () => {
      v.on = !v.on;
      entry.classList.toggle("on", v.on);
      entry.classList.toggle("off", !v.on);
      drawChart(state);
    });
    legend.appendChild(entry);
  }
}

function niceCeil(n) {
  if (n <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(n)));
  const r = n / exp;
  if (r <= 1) return 1 * exp;
  if (r <= 2) return 2 * exp;
  if (r <= 5) return 5 * exp;
  return 10 * exp;
}

function fmtShortDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
