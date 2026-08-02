// app.js — RF Scanner, browser build. This page owns the radio.
//
// Adapted from web/static/app.js, which talks to webapp.py over SSE and
// /api/*. Here there is no server and no server state: the engine runs in
// this tab, so every fetch becomes a direct call. What is kept verbatim is
// the part that was already proven at the bench — paint(), the band picker,
// and the pass-time estimate.
//
// The multi-pass loop below is a port of webapp.py's scan_worker, including
// its empty-pass recovery. Read that function before changing this one.

import { RFExplorer } from './rfe.js';
import { scanPass } from './sweep.js';
import { ScanAccumulator } from './stats.js';
import { downloadCSV, defaultFilename } from './export.js';
import { BANDS, BY_LABEL, FREQ_PRESETS, merge, totalWidth } from './bands.js';

const $ = (id) => document.getElementById(id);

// ─────────────────────────────────────────────────────────────────── state ──
// One operator, one radio, one scan — so this is plain module state, the
// same call AGENTS.md records for webapp.py's single global Session.

const rfe = new RFExplorer(writeLog);
const acc = new ScanAccumulator();

let controller = null;          // AbortController for the running scan
let connected = false;
let scanning = false;
let progress = 0;
let status = "—";
let logLines = [];
let selectedBands = [];
let scanRanges = [];            // what the current/last scan actually covered
let scanChunk = 6;              // ditto — the noise-floor model needs it

// ───────────────────────────────────────────────────────────────────── log ──

function writeLog(text) {
  const t = new Date().toTimeString().slice(0, 8);
  logLines.push(`${t}  ${text}`);
  // A long gig would otherwise grow this without bound.
  if (logLines.length > 500) logLines = logLines.slice(-400);
  const el = $("log");
  el.textContent = logLines.join("\n");
  el.scrollTop = el.scrollHeight;
}

// ─────────────────────────────────────────────────────────────── rendering ──

function render() {
  $("conn-state").textContent = connected ? "radio connected" : "no radio";
  $("live-dot").className = connected ? "ok" : "down";
  $("connect").hidden = connected;
  $("disconnect").hidden = !connected;
  $("disconnect").disabled = scanning;

  $("start-scan").disabled = !connected || scanning;
  $("start-scan").hidden = scanning;
  $("stop-scan").hidden = !scanning;

  $("progress-bar").style.width = (scanning ? progress : 0) + "%";
  $("status").textContent = status || "—";
  $("noise").textContent = acc.passCount > 0
    ? `Est. noise floor ${noiseFloor()} dBm · ${acc.passCount} pass`
      + `${acc.passCount === 1 ? "" : "es"} · ${acc.binCount} bins`
    : "";

  // Keep the empty-plot message honest as state changes, not only when the
  // plot redraws.
  if (!acc.binCount) $("plot-note").textContent = emptyMessage();

  updateEstimate();
}

/** Same model the Tk and web UIs show: base + RBW gain + averaging gain. */
function noiseFloor() {
  const rbwKHz = (scanChunk * 1000) / 112;
  const rbwGain = 10 * Math.log10(200 / Math.max(rbwKHz, 0.1));
  const avgGain = 10 * Math.log10(Math.max(acc.passCount, 1));
  return Math.round((-110 - rbwGain - avgGain) * 10) / 10;
}

// The empty plot must never contradict the device panel — telling someone to
// "connect the RF Explorer" while it says connected is how a working app
// looks broken.
function emptyMessage() {
  if (!connected) return "No data yet — connect the RF Explorer to begin.";
  if (scanning && !acc.passCount) return "Scanning — first pass in progress…";
  if (scanning) return "Scanning…";
  return "Connected. Choose bands or a range, then press Start Scan.";
}

// ────────────────────────────────────────────────────────────────── ranges ──

function currentRanges() {
  if (selectedBands.length) {
    return merge(selectedBands.flatMap((l) => (BY_LABEL[l] || { ranges: [] }).ranges));
  }
  const a = parseFloat($("start").value), b = parseFloat($("end").value);
  return (isFinite(a) && isFinite(b) && b > a) ? [[a, b]] : [];
}

const spanText = (spans) =>
  spans.map(([a, b]) => `${+a.toFixed(3)}–${+b.toFixed(3)}`).join(", ");
const trim = (n) => n.toFixed(3).replace(/\.?0+$/, "");

function updateEstimate() {
  const spans = currentRanges();
  const chunk = parseFloat($("chunk").value);
  const iters = parseInt($("iterations").value, 10);
  if (!spans.length || !(chunk > 0) || !(iters > 0)) {
    $("estimate").textContent = "";
    return;
  }
  // Chunks never straddle a span boundary, so count per span.
  const n = spans.reduce((t, [a, b]) => t + Math.ceil((b - a) / chunk), 0);
  const secs = n * (0.6 + iters * 0.3);
  const t = secs < 60 ? `~${secs.toFixed(0)}s` : `~${(secs / 60).toFixed(1)}min`;
  $("estimate").textContent =
    `${spanText(spans)} MHz · ${trim(totalWidth(spans))} MHz `
    + `· ≈ ${n} chunks · ${t}/pass · continuous until stopped`;
}

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

// ────────────────────────────────────────────────────────────────── device ──

$("connect").onclick = async () => {
  $("connect").disabled = true;
  try {
    await rfe.connect();
    connected = true;
    status = "Connected. Choose bands or a range, then press Start Scan.";
  } catch (e) {
    // Dismissing the native port picker throws NotFoundError. That is a
    // person changing their mind, not a fault worth an alert box.
    if (e.name !== "NotFoundError") alert("Connect failed: " + e.message);
    writeLog("connect failed: " + e.message);
  }
  $("connect").disabled = false;
  render();
};

$("disconnect").onclick = async () => {
  controller?.abort();
  await rfe.disconnect();
  connected = false;
  status = "Disconnected.";
  render();
};

// ──────────────────────────────────────────────────────────────────── scan ──

$("start-scan").onclick = () => {
  const ranges = currentRanges();
  if (!ranges.length) {
    alert("Choose bands, or a Start/End range with End above Start.");
    return;
  }
  const chunk = parseFloat($("chunk").value);
  const iterations = parseInt($("iterations").value, 10);
  if (!(chunk > 0) || !(iterations > 0)) {
    alert("Chunk size and iterations must both be greater than zero.");
    return;
  }

  acc.clear();
  scanRanges = ranges;
  scanChunk = chunk;
  scanning = true;
  progress = 0;
  status = "Starting…";
  controller = new AbortController();
  render();
  drawSpectrum();
  scanLoop(ranges, chunk, iterations);      // deliberately not awaited
};

$("stop-scan").onclick = () => {
  controller?.abort();
  status = "Stopping after this chunk…";
  render();
};

function onEngineMessage(m) {
  if (m.type === "log") {
    writeLog(m.text);
  } else if (m.type === "progress") {
    // Touch only the two nodes that change — a full render per chunk would
    // fight the band modal and the inputs while someone is using them.
    progress = m.value;
    status = m.text;
    $("progress-bar").style.width = progress + "%";
    $("status").textContent = status;
  }
}

/**
 * The continuous multi-pass loop — a port of webapp.py's scan_worker.
 *
 * The empty-pass branch is the important part. A pass that returns nothing
 * means every chunk timed out, which is what a radio does after a scan is
 * stopped mid-chunk and then left idle for a few minutes. Recovery is a
 * reconnect, not a flush (measured; see AGENTS.md), and it is worth doing
 * unattended because the alternative is an empty graph and a cheerful status.
 */
async function scanLoop(ranges, chunk, iterations) {
  let passNum = 0;
  let emptyPasses = 0;
  // Set when the loop stops for a reason the operator needs to keep reading.
  // A flag rather than sniffing the status text: "No data — reopening the
  // port and retrying…" is transient, and stopping while it showed would
  // otherwise leave it on screen as if it were the outcome.
  let fatal = false;

  try {
    while (!controller.signal.aborted) {
      passNum += 1;
      const data = await scanPass(rfe, {
        startMHz: ranges[0][0], endMHz: ranges[ranges.length - 1][1],
        chunkMHz: chunk, iterations, ranges,
        passNumber: passNum,
        signal: controller.signal,
        onMessage: onEngineMessage,
      });

      if (data.length) {
        emptyPasses = 0;
        acc.addPass(data);
        progress = 100;
        status = `Pass ${passNum} complete · ${acc.binCount} bins`;
        drawSpectrum();
      } else if (controller.signal.aborted) {
        break;                       // stopped before anything came back
      } else {
        emptyPasses += 1;
        writeLog(`Pass ${passNum} returned NO DATA — every chunk timed out.`);
        if (emptyPasses > 1) {
          writeLog("Still no data after a reconnect — stopping.");
          status = "No data even after reconnecting. Check the USB cable, "
                 + "and the device's Config > USB Baud = 500K.";
          fatal = true;
          break;
        }
        status = "No data — reopening the port and retrying…";
        render();
        writeLog("Reopening the port (the only reliable recovery) and retrying…");
        let ok = true;
        try {
          await rfe.reconnect();
        } catch (e) {
          ok = false;
          writeLog("Reconnect FAILED — " + e.message);
        }
        if (ok) {
          writeLog("Reconnect succeeded.");
          connected = true;
          // A reconnect is a fresh device session, so the next pass must be
          // pass 1 again — that is the only branch which re-enables the
          // on-device average calculator. Without this reset the retry runs
          // on a differently configured radio than the scan started on.
          passNum = 0;
        } else {
          connected = false;
          status = "Radio not answering and reconnect failed — check USB and power.";
          fatal = true;
          break;
        }
      }
      render();
    }
  } catch (e) {
    writeLog("Scan error: " + e.message);
    status = "Error: " + e.message;
    fatal = true;
  } finally {
    scanning = false;
    controller = null;
    if (!fatal) {
      status = `Stopped after ${acc.passCount} pass`
             + `${acc.passCount === 1 ? "" : "es"}`;
    }
    render();
  }
}

// ───────────────────────────────────────────────────────────── band picker ──

function loadPresets() {
  $("preset").innerHTML = `<option value="">Preset…</option>`
    + Object.entries(FREQ_PRESETS)
        .filter(([, v]) => v !== null)          // "Custom" has no range
        .map(([k, v]) => `<option value="${v[0]},${v[1]}">${k}</option>`)
        .join("");
}

$("preset").onchange = (e) => {
  if (!e.target.value) return;
  const [a, b] = e.target.value.split(",");
  selectedBands = [];
  $("band-summary").hidden = true;
  $("start").value = a;
  $("end").value = b;
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
  const shown = BANDS.filter((b) => !usOnly || b.usLegal !== "none");
  let html = "", group = null;
  for (const b of shown) {
    const g = `${b.maker} — ${b.family}`;
    if (g !== group) { html += `<h3>${g}</h3>`; group = g; }
    const warn = b.usLegal === "partial" ? ` <em>⚠ partly outside US spectrum</em>`
      : b.usLegal === "none" ? ` <em>⚠ not usable in the US</em>` : "";
    const checked = selectedBands.includes(b.label) ? "checked" : "";
    html += `<label class="band"><input type="checkbox" value="${b.label}" ${checked}>
      <span><b>${b.name}</b> ${b.spanText}${warn}
      ${b.note ? `<br><small>${b.note}</small>` : ""}</span></label>`;
  }
  $("band-list").innerHTML = html;
  $("band-list").querySelectorAll("input").forEach(
    (i) => i.addEventListener("change", updateModalSummary));
  updateModalSummary();
}

const modalPicked = () =>
  [...$("band-list").querySelectorAll("input:checked")].map((i) => i.value);

function updateModalSummary() {
  const picked = modalPicked();
  if (!picked.length) {
    $("band-modal-summary").textContent =
      "Nothing selected — the Start/End range is used.";
    return;
  }
  const spans = merge(picked.flatMap((l) => BY_LABEL[l].ranges));
  const raw = picked.reduce((t, l) => t + totalWidth(BY_LABEL[l].ranges), 0);
  const saved = raw - totalWidth(spans);
  $("band-modal-summary").textContent =
    `${picked.length} band(s) → ${spanText(spans)} MHz = ${trim(totalWidth(spans))} MHz`
    + (saved > 0.01 ? `  (overlap merged: ${trim(saved)} MHz saved)` : "");
}

$("band-apply").onclick = () => {
  selectedBands = modalPicked();
  if (selectedBands.length) {
    const spans = merge(selectedBands.flatMap((l) => BY_LABEL[l].ranges));
    const names = selectedBands.map((l) => BY_LABEL[l].name).join(", ");
    $("start").value = spans[0][0];
    $("end").value = spans[spans.length - 1][1];
    $("band-summary").textContent =
      `Bands: ${names} → ${spanText(spans)} MHz (${trim(totalWidth(spans))} MHz)`;
    $("band-summary").hidden = false;
    $("preset").value = "";
  } else {
    $("band-summary").hidden = true;
  }
  $("band-modal").hidden = true;
  updateEstimate();
};

// ────────────────────────────────────────────────────────────────── export ──

/** (points, label) for the selected export mode — 'MAX' or 'P<n>'. */
function exportData() {
  const mode = $("export-mode").value;
  if (!acc.passCount) return { points: [], label: mode };
  if (mode.toUpperCase() === "MAX") return { points: acc.exportMax(), label: "MAX" };
  const pct = parseInt(mode.replace(/^[Pp]/, ""), 10);
  const n = isFinite(pct) ? pct : 20;
  return { points: acc.exportPercentile(n), label: `P${n}` };
}

$("download").onclick = () => {
  const { points, label } = exportData();
  if (!points.length) { alert("No data yet — run a scan first."); return; }
  const name = defaultFilename({
    startMHz: scanRanges[0][0],
    endMHz: scanRanges[scanRanges.length - 1][1],
    passes: acc.passCount,
    label,
  });
  downloadCSV(name, points);
  writeLog(`Downloaded ${points.length} bins as ${name}`);
};

$("export-mode").onchange = drawSpectrum;

// ──────────────────────────────────────────────────────────────────── plot ──

function drawSpectrum() {
  const { points, label } = exportData();
  paint(points, scanRanges);
  $("plot-note").textContent = points.length
    ? `${points.length} bins · ${label} across ${acc.passCount} `
      + `pass${acc.passCount === 1 ? "" : "es"}`
    : emptyMessage();
}

// Unchanged from web/static/app.js — this drawing code is already proven.
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

// ──────────────────────────────────────────────────────────────────── init ──

window.addEventListener("resize", drawSpectrum);

// Leaving mid-scan without closing the port is what leaves the radio
// streaming into a buffer nobody drains — the state it never recovers from
// on its own.
window.addEventListener("beforeunload", () => {
  controller?.abort();
  if (connected) rfe.disconnect();
});

if (!("serial" in navigator)) {
  const w = $("serial-warning");
  w.textContent = "This browser has no Web Serial, so it cannot open the "
    + "radio. Use Chrome or Edge on a desktop — Safari and Firefox do not "
    + "support it, which rules out iPhone and iPad as the scanning machine.";
  w.style.color = "var(--danger)";
  w.hidden = false;
  $("connect").disabled = true;
}

loadPresets();
render();
drawSpectrum();
