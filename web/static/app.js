// app.js — RF Scanner web frontend.
// Server pushes "state" over SSE; we refetch /api/state and re-render.
// The plot is drawn from /api/spectrum, refetched when a pass completes.

const $ = (id) => document.getElementById(id);
let STATE = null;
let BANDS = [];
let selectedBands = [];
let lastPassDrawn = -1;

// ───────────────────────────────────────────────────────────── live wiring ──

const es = new EventSource("/events");
es.addEventListener("state", refreshState);
es.onopen = () => { setDot(true); refreshState(); };
es.onerror = () => setDot(false);

function setDot(ok) {
  const d = $("live-dot");
  d.className = ok ? "ok" : "down";
  if (!ok) $("conn-state").textContent = "reconnecting…";
}

let adopted = false;

async function refreshState() {
  const r = await fetch("/api/state");
  if (!r.ok) return;
  STATE = await r.json();
  // First load of a page (a reload, or opening it on a second device)
  // adopts whatever the server is actually set up to scan, so a phone
  // shows the same bands as the laptop that started the scan.
  if (!adopted) {
    adopted = true;
    adoptServerSelection();
  }
  render();
  if (STATE.spectrum_dirty || STATE.pass_count !== lastPassDrawn) {
    lastPassDrawn = STATE.pass_count;
    drawSpectrum();
  }
}

// ───────────────────────────────────────────────────────────────── rendering ──

function render() {
  const s = STATE;
  $("conn-state").textContent = s.connected ? `connected · ${s.port}` : "no device";
  $("connect").hidden = s.connected;
  $("disconnect").hidden = !s.connected;
  $("port").disabled = s.connected || s.scanning;

  $("start-scan").disabled = !s.connected || s.scanning;
  $("start-scan").hidden = s.scanning;
  $("stop-scan").hidden = !s.scanning;

  $("progress-bar").style.width = (s.scanning ? s.progress : 0) + "%";
  $("status").textContent = s.status || "—";
  $("noise").textContent = s.noise_floor != null && s.pass_count > 0
    ? `Est. noise floor ${s.noise_floor} dBm · ${s.pass_count} pass`
      + `${s.pass_count === 1 ? "" : "es"} · ${s.bin_count} bins`
    : "";

  $("log").textContent = (s.log || []).join("\n");
  $("log").scrollTop = $("log").scrollHeight;

  // Keep the empty-plot message honest as state changes, not only when
  // the plot redraws.
  if (!s.bin_count) $("plot-note").textContent = emptyMessage();

  updateEstimate();
}

// The empty plot must never contradict the device panel — telling someone
// to "connect the RF Explorer" while it says Connected is how a working
// app looks broken.
function emptyMessage() {
  const s = STATE;
  if (!s) return "Loading…";
  if (!s.connected) return "No data yet — connect the RF Explorer to begin.";
  if (s.scanning && !s.pass_count) return "Scanning — first pass in progress…";
  if (s.scanning) return "Scanning…";
  return "Connected. Choose bands or a range, then press Start Scan.";
}

function adoptServerSelection() {
  const s = STATE;
  if (!s) return;
  if (s.chunk) $("chunk").value = s.chunk;
  if (s.iterations) $("iterations").value = s.iterations;
  if (s.ranges && s.ranges.length) {
    $("start").value = s.ranges[0][0];
    $("end").value = s.ranges[s.ranges.length - 1][1];
  }
  selectedBands = (s.selected_bands || []).filter(
    (l) => BANDS.some((b) => b.label === l));
  if (selectedBands.length) {
    const names = selectedBands.map(
      (l) => BANDS.find((b) => b.label === l).name).join(", ");
    $("band-summary").textContent =
      `Bands: ${names} → ${spanText(s.ranges)} MHz (${s.total_mhz} MHz)`;
    $("band-summary").hidden = false;
  }
}

function currentRanges() {
  if (selectedBands.length) {
    return mergeSpans(selectedBands.flatMap(
      (l) => (BANDS.find((b) => b.label === l) || { spans: [] }).spans));
  }
  const a = parseFloat($("start").value), b = parseFloat($("end").value);
  return (isFinite(a) && isFinite(b) && b > a) ? [[a, b]] : [];
}

function mergeSpans(spans) {
  const out = [];
  for (const [a, b] of [...spans].sort((x, y) => x[0] - y[0])) {
    if (out.length && a <= out[out.length - 1][1]) {
      out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
    } else out.push([a, b]);
  }
  return out;
}

const width = (spans) => spans.reduce((t, [a, b]) => t + (b - a), 0);
const spanText = (spans) => spans.map(([a, b]) => `${+a.toFixed(3)}–${+b.toFixed(3)}`).join(", ");

function updateEstimate() {
  const spans = currentRanges();
  const chunk = parseFloat($("chunk").value);
  const iters = parseInt($("iterations").value, 10);
  if (!spans.length || !(chunk > 0) || !(iters > 0)) { $("estimate").textContent = ""; return; }
  // Chunks never straddle a span boundary, so count per span.
  const n = spans.reduce((t, [a, b]) => t + Math.ceil((b - a) / chunk), 0);
  const secs = n * (0.6 + iters * 0.3);
  const t = secs < 60 ? `~${secs.toFixed(0)}s` : `~${(secs / 60).toFixed(1)}min`;
  $("estimate").textContent =
    `${spanText(spans)} MHz · ${width(spans).toFixed(3).replace(/\.?0+$/, "")} MHz `
    + `· ≈ ${n} chunks · ${t}/pass · continuous until stopped`;
}

// ────────────────────────────────────────────────────────────────── device ──

async function loadPorts() {
  const r = await fetch("/api/ports");
  const { ports } = await r.json();
  const sel = $("port");
  const keep = sel.value;
  sel.innerHTML = ports.length
    ? ports.map((p) => `<option>${p}</option>`).join("")
    : `<option value="">no serial ports found</option>`;
  if (ports.includes(keep)) sel.value = keep;
}

$("refresh-ports").onclick = loadPorts;

$("connect").onclick = async () => {
  const port = $("port").value;
  if (!port) return;
  $("connect").disabled = true;
  const r = await fetch("/api/connect", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ port }),
  });
  const j = await r.json();
  $("connect").disabled = false;
  if (!r.ok) alert(j.message || j.error || "Connect failed");
  refreshState();
};

$("disconnect").onclick = async () => {
  await fetch("/api/disconnect", { method: "POST" });
  refreshState();
};

// ──────────────────────────────────────────────────────────────────── scan ──

$("start-scan").onclick = async () => {
  const body = {
    bands: selectedBands,
    start: parseFloat($("start").value),
    end: parseFloat($("end").value),
    chunk: parseFloat($("chunk").value),
    iterations: parseInt($("iterations").value, 10),
  };
  const r = await fetch("/api/scan/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) alert((await r.json()).error || "Could not start");
  refreshState();
};

$("stop-scan").onclick = async () => {
  await fetch("/api/scan/stop", { method: "POST" });
  refreshState();
};

for (const id of ["start", "end", "chunk", "iterations"]) {
  $(id).addEventListener("input", () => {
    // Typing a literal range drops the band selection: last edit wins.
    if ((id === "start" || id === "end") && selectedBands.length) {
      selectedBands = [];
      $("band-summary").hidden = true;
    }
    updateEstimate();
  });
}

// ───────────────────────────────────────────────────────────── band picker ──

async function loadBands() {
  const r = await fetch("/api/bands");
  const j = await r.json();
  BANDS = j.bands;
  $("preset").innerHTML = `<option value="">Preset…</option>` + Object.entries(j.presets)
    .map(([k, v]) => `<option value="${v[0]},${v[1]}">${k}</option>`).join("");
}

$("preset").onchange = (e) => {
  if (!e.target.value) return;
  const [a, b] = e.target.value.split(",");
  selectedBands = [];
  $("band-summary").hidden = true;
  $("start").value = a; $("end").value = b;
  updateEstimate();
};

$("pick-bands").onclick = () => { renderBandList(); $("band-modal").hidden = false; };
$("band-close").onclick = () => { $("band-modal").hidden = true; };
$("us-only").onchange = renderBandList;
$("band-clear").onclick = () => {
  $("band-list").querySelectorAll("input").forEach((i) => (i.checked = false));
  updateModalSummary();
};

function renderBandList() {
  const usOnly = $("us-only").checked;
  const shown = BANDS.filter((b) => !usOnly || b.us_legal !== "none");
  let html = "", group = null;
  for (const b of shown) {
    const g = `${b.maker} — ${b.family}`;
    if (g !== group) { html += `<h3>${g}</h3>`; group = g; }
    const warn = b.us_legal === "partial" ? ` <em>⚠ partly outside US spectrum</em>`
      : b.us_legal === "none" ? ` <em>⚠ not usable in the US</em>` : "";
    const checked = selectedBands.includes(b.label) ? "checked" : "";
    html += `<label class="band"><input type="checkbox" value="${b.label}" ${checked}>
      <span><b>${b.name}</b> ${b.span_text}${warn}
      ${b.note ? `<br><small>${b.note}</small>` : ""}</span></label>`;
  }
  $("band-list").innerHTML = html;
  $("band-list").querySelectorAll("input").forEach(
    (i) => i.addEventListener("change", updateModalSummary));
  updateModalSummary();
}

function modalPicked() {
  return [...$("band-list").querySelectorAll("input:checked")].map((i) => i.value);
}

function updateModalSummary() {
  const picked = modalPicked();
  if (!picked.length) {
    $("band-modal-summary").textContent = "Nothing selected — the Start/End range is used.";
    return;
  }
  const spans = mergeSpans(picked.flatMap((l) => BANDS.find((b) => b.label === l).spans));
  const raw = picked.reduce(
    (t, l) => t + width(BANDS.find((b) => b.label === l).spans), 0);
  const saved = raw - width(spans);
  $("band-modal-summary").textContent =
    `${picked.length} band(s) → ${spanText(spans)} MHz = ${width(spans).toFixed(3).replace(/\.?0+$/, "")} MHz`
    + (saved > 0.01 ? `  (overlap merged: ${saved.toFixed(3).replace(/\.?0+$/, "")} MHz saved)` : "");
}

$("band-apply").onclick = () => {
  selectedBands = modalPicked();
  if (selectedBands.length) {
    const spans = mergeSpans(selectedBands.flatMap(
      (l) => BANDS.find((b) => b.label === l).spans));
    const names = selectedBands.map((l) => BANDS.find((b) => b.label === l).name).join(", ");
    $("start").value = spans[0][0];
    $("end").value = spans[spans.length - 1][1];
    $("band-summary").textContent =
      `Bands: ${names} → ${spanText(spans)} MHz (${width(spans).toFixed(3).replace(/\.?0+$/, "")} MHz)`;
    $("band-summary").hidden = false;
    $("preset").value = "";
  } else {
    $("band-summary").hidden = true;
  }
  $("band-modal").hidden = true;
  updateEstimate();
};

// ────────────────────────────────────────────────────────────────── export ──

$("save-desktop").onclick = async () => {
  const r = await fetch("/api/export", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: $("export-mode").value }),
  });
  const j = await r.json();
  if (!r.ok) return alert(j.error);
  refreshState();
};

$("download").onclick = () => {
  window.location = `/api/export/download?mode=${encodeURIComponent($("export-mode").value)}`;
};

$("export-mode").onchange = drawSpectrum;

// ──────────────────────────────────────────────────────────────────── plot ──

async function drawSpectrum() {
  const r = await fetch(`/api/spectrum?mode=${encodeURIComponent($("export-mode").value)}`);
  if (!r.ok) return;
  const { points, mode, spans, passes } = await r.json();
  paint(points, spans);
  $("plot-note").textContent = points.length
    ? `${points.length} bins · ${mode} across ${passes} pass${passes === 1 ? "" : "es"}`
    : emptyMessage();
}

function paint(points, spans) {
  const cv = $("plot");
  const dpr = window.devicePixelRatio || 1;
  const cssW = cv.clientWidth || 1200, cssH = 420;
  cv.width = cssW * dpr; cv.height = cssH * dpr;
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, cssW, cssH);

  const L = 52, R = 12, T = 24, B = 30;   // T leaves room for the unit label
  const w = cssW - L - R, h = cssH - T - B;

  const css = getComputedStyle(document.body);
  const ink = css.getPropertyValue("--dim").trim() || "#8b949e";
  const line = css.getPropertyValue("--accent").trim() || "#e8a33d";

  if (!points.length) {
    g.fillStyle = ink; g.font = "13px system-ui";
    g.fillText("no data", L + 8, T + 20);
    return;
  }

  // X domain from the requested spans (so skipped spectrum stays visible
  // as a gap), Y auto-scaled with headroom.
  const xs = spans && spans.length ? spans : [[points[0][0], points[points.length - 1][0]]];
  const fMin = xs[0][0], fMax = xs[xs.length - 1][1];
  let aMin = Infinity, aMax = -Infinity;
  for (const [, a] of points) { if (a < aMin) aMin = a; if (a > aMax) aMax = a; }
  aMin = Math.floor((aMin - 3) / 10) * 10;
  aMax = Math.ceil((aMax + 3) / 10) * 10;

  const X = (f) => L + ((f - fMin) / (fMax - fMin)) * w;
  const Y = (a) => T + (1 - (a - aMin) / (aMax - aMin)) * h;

  // Shade spectrum that was NOT scanned (the gaps between spans).
  g.fillStyle = "rgba(255,255,255,0.04)";
  for (let i = 0; i < xs.length - 1; i++) {
    const x0 = X(xs[i][1]), x1 = X(xs[i + 1][0]);
    if (x1 - x0 > 0.5) g.fillRect(x0, T, x1 - x0, h);
  }

  // Grid + labels
  g.strokeStyle = "rgba(255,255,255,0.07)"; g.lineWidth = 1;
  g.fillStyle = ink; g.font = "11px system-ui"; g.textBaseline = "middle";
  for (let a = aMin; a <= aMax; a += 10) {
    const y = Y(a);
    g.beginPath(); g.moveTo(L, y); g.lineTo(L + w, y); g.stroke();
    g.textAlign = "right"; g.fillText(`${a}`, L - 6, y);
  }
  g.textAlign = "center"; g.textBaseline = "top";
  const ticks = 8;
  for (let i = 0; i <= ticks; i++) {
    const f = fMin + (i / ticks) * (fMax - fMin);
    const x = X(f);
    g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + h); g.stroke();
    g.fillText(f.toFixed(0), x, T + h + 6);
  }
  // Above the plot area, so it never collides with the top gridline label.
  g.textAlign = "right"; g.textBaseline = "top";
  g.fillText("dBm", L - 6, 4);
  g.textAlign = "left";
  g.fillText("MHz", L + 2, 4);

  // The trace. Break the path across gaps — never draw a line through
  // spectrum we deliberately skipped.
  const GAP = Math.max(0.2, (fMax - fMin) / w * 6);   // ~6px worth of MHz
  g.strokeStyle = line; g.lineWidth = 1.2; g.beginPath();
  let prevF = null;
  for (const [f, a] of points) {
    const x = X(f), y = Y(a);
    if (prevF === null || f - prevF > GAP) g.moveTo(x, y);
    else g.lineTo(x, y);
    prevF = f;
  }
  g.stroke();
}

// ─────────────────────────────────────────────────────────────────── init ──

window.addEventListener("resize", () => drawSpectrum());
loadBands().then(loadPorts).then(refreshState);
