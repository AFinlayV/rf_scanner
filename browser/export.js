// export.js — WWB CSV export, filename generation, and browser download.
//
// Port of export.py, read 2026-08-01. save_wwb_csv there opens a path and
// writes lines to it; a browser page has no filesystem to open a path on,
// so that one Python function becomes two here:
//   formatWWBCSV(data)        — build the exact same line-for-line text
//   downloadCSV(name, data)   — new: the client-side "save" (Blob + <a>)
//
// Format is unchanged — headerless, two columns:
//   470.000,-109.0
//   470.025,-107.5
// Import in WWB: Scan Data -> Add Scan From File.
//
// stats.py and export.py never import each other in the Python engine;
// this file mirrors that (no import of stats.js) — see the rounding note
// below for what that costs.

// ── rounding ───────────────────────────────────────────────────────────
// Same helper and same rationale as stats.js's roundHalfEven: Python's
// round() and its f"{x:.Nf}" formatting both round HALF TO EVEN on the
// exact binary value, and the obvious JS shortcuts (Math.round after
// scaling, toFixed, or a tie test with an epsilon) each get ordinary values
// wrong. The full explanation lives in stats.js — read it there before
// touching this.
//
// Duplicated rather than imported from stats.js on purpose: export.py and
// stats.py are independent modules in the Python original, and this file
// keeps that boundary instead of wiring the two ports together for one
// small function. If you fix a bug here, fix it there too.
function roundHalfEven(x, ndigits = 1) {
  if (!Number.isFinite(x) || Math.abs(x) >= 1e21) return x;
  const neg = x < 0;
  const s = Math.abs(x).toFixed(20);
  const dot = s.indexOf('.');
  const digits = s.slice(0, dot) + s.slice(dot + 1);
  const cut = dot + ndigits;
  let n = BigInt(digits.slice(0, cut));
  const rest = digits.slice(cut);
  if (rest.charCodeAt(0) - 48 > 5) n += 1n;
  else if (rest.charCodeAt(0) - 48 === 5) {
    if (/[1-9]/.test(rest.slice(1))) n += 1n;      // strictly past the tie
    else if (n % 2n === 1n) n += 1n;               // exact tie -> to even
  }
  const out = Number(n) / 10 ** ndigits;
  return neg ? -out : out;
}

/** Round to `ndigits` decimals via roundHalfEven, then zero-pad for display. */
function fixed(x, ndigits) {
  return roundHalfEven(x, ndigits).toFixed(ndigits);
}

// ── WWB CSV ───────────────────────────────────────────────────────────────

/**
 * Build WWB-compatible CSV text for a list of [freqMHz, ampDBM] points:
 * headerless, `{freq:.3f},{amp:.1f}\n` per line — byte-for-byte what
 * save_wwb_csv wrote to disk in the Python original. Returns "" for no
 * data, matching save_wwb_csv creating an empty file for an empty list.
 */
export function formatWWBCSV(data) {
  let out = '';
  for (const [freq, amp] of data) {
    out += `${fixed(freq, 3)},${fixed(amp, 1)}\n`;
  }
  return out;
}

// ── filename ──────────────────────────────────────────────────────────────

/**
 * Generate a descriptive timestamped filename, e.g.
 *   rf_scan_500-650MHz_3passes_P20_20260322_103045.csv
 * Timestamp is LOCAL time — matches datetime.datetime.now() in the Python
 * original, not utcnow().
 *
 * `now` is an injected Date, defaulting to the real clock. default_filename
 * in export.py has no such hook (it always calls datetime.now() directly);
 * this is the one deliberate addition in this file, needed so a test can
 * pin the clock and diff this function's output against Python's for a
 * fixed instant. Never pass `now` from production call sites.
 */
export function defaultFilename({
  prefix = 'rf_scan',
  startMHz = null,
  endMHz = null,
  passes = null,
  percentile = null,
  label = null,
  now = new Date(),
} = {}) {
  const ts = timestamp(now);
  const parts = [prefix];
  if (startMHz !== null && endMHz !== null) {
    parts.push(`${fixed(startMHz, 0)}-${fixed(endMHz, 0)}MHz`);
  }
  if (passes !== null) {
    parts.push(`${passes}pass${passes !== 1 ? 'es' : ''}`);
  }
  const tag = label !== null ? label : (percentile !== null ? `P${percentile}` : null);
  if (tag !== null) {
    parts.push(tag);
  }
  parts.push(ts);
  return parts.join('_') + '.csv';
}

/** strftime("%Y%m%d_%H%M%S") on LOCAL time fields (not UTC). */
function timestamp(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
         `_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

// ── browser download ────────────────────────────────────────────────────
// New in this port: export.py's callers write to a path on the operator's
// machine because the Python process and the radio are the same machine.
// In the browser build there is no server to POST a file to (see
// docs/PLAN_browser_serial.md — "the server holds no state at all") — the
// download has to happen entirely client-side.

/**
 * Build the CSV for `data` and trigger a client-side download named
 * `filename`. Returns the number of points written, mirroring
 * save_wwb_csv's return value in the Python original.
 */
export function downloadCSV(filename, data) {
  const blob = new Blob([formatWWBCSV(data)], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  return data.length;
}
