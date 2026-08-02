// sweep.js — one full scan pass, in the browser.
//
// Port of scanner.py's scan_pass() and _scan_chunk(). The RF behaviour here
// is hard-won and was tuned against real hardware: the LO settle, the flush
// phase, the ±15% chunk dither, the 1 MHz overlap padding, the 10-bin edge
// trim, and linear-power-domain averaging everywhere. It is ported line for
// line rather than reinvented. If something looks arbitrary, it is load-
// bearing — see the comments in scanner.py before changing it.
//
// Two deliberate differences from the Python, both forced by the wire:
//
//   1. Staleness. A parsed sweep from the pip package carries its own start
//      frequency, so scanner.py can spot a sweep left over from the previous
//      chunk by comparing it (|Δ| > 1 MHz means stale). A raw $S frame has no
//      such field — it is just amplitudes. What separates the old range from
//      the new one on the wire is the #C2-F: config the device echoes when it
//      retunes, so that echo is the barrier: everything after it belongs to
//      this chunk.
//
//   2. Bin frequencies come from that echoed config (startMHz + i·stepMHz)
//      rather than from the requested span divided by the point count. When
//      the radio tunes exactly where it was asked the two agree — verified on
//      470–476 MHz in P0. When it snaps the range to its own grid, the echo
//      is the truth and the requested span is not.
//
// Rounding: Python's round() is half-to-even, JS's is half-up. The values
// rounded here are averages of linear power, so an exact tie at 0.1 dB or at
// the 4th decimal of a megahertz essentially never happens. Not worth an
// emulation layer; noted so the next person doesn't wonder.

const STEP = 0.025;          // WWB_MIN_STEP_MHZ — WWB needs >= 25 kHz spacing
const DITHER = 0.15;         // ±15% chunk-width jitter on passes 2+
const OVERLAP_MHZ = 1.0;     // padding so edge-trimmed bins are covered
const EDGE_TRIM = 10;        // bins dropped per sweep end (IF filter rolloff)
const SETTLE_FLUSH = 2;      // valid sweeps discarded after each retune
const NATIVE_POINTS = 112;   // device sweep size, used to auto-scale smoothing

const sleep = ms => new Promise(r => setTimeout(r, ms));
const roundTo = (x, d) => { const p = 10 ** d; return Math.round(x * p) / p; };
const dbmOf = byte => -byte / 2.0;

/** Average dBm values in the linear power domain.
 *
 * Averaging dBm directly biases noise low by ~2.5 dB (Jensen's inequality),
 * which at a show reads as a quieter band than you actually have.
 */
function avgDBM(values, divisor = values.length) {
  let mw = 0;
  for (const v of values) mw += 10 ** (v / 10);
  return 10 * Math.log10(Math.max(mw / divisor, 1e-20));
}

// ── one full pass ──────────────────────────────────────────────────────

/**
 * Sweep the requested spectrum in chunkMHz-wide slices.
 *
 * `ranges` is a list of [start, end] MHz spans — pass several to scan
 * disjoint spectrum in one pass (band multiselect, or a single band the
 * 600 MHz repack split in two). It supersedes startMHz/endMHz, which remain
 * for callers scanning one contiguous span.
 *
 * Returns a sorted array of [freqMHz, ampDBM] at >= 25 kHz resolution.
 * Partial data is returned if `signal` aborts mid-pass.
 */
export async function scanPass(rfe, {
  startMHz, endMHz, chunkMHz, iterations,
  ranges = null,
  passNumber = 1,
  smoothWindow = 3,
  signal = null,
  onMessage = () => {},
} = {}) {
  const stopped = () => !!signal?.aborted;
  const say = onMessage;

  if (passNumber === 1) {
    // Clear anything a previously-interrupted scan left mid-message, or
    // every chunk below times out. See RFExplorer.resetStream().
    const stale = rfe.resetStream();
    if (stale > 0) {
      say({ type: 'log', text:
        `Flushed ${stale} stale bytes from the serial buffer before starting.` });
    }
    try {
      await rfe.setAverageCalculator();
      say({ type: 'log', text: 'Average calculator mode enabled.' });
    } catch {
      say({ type: 'log', text:
        'Note: could not set Average mode (firmware may not support it).' });
    }
  }

  // One contiguous span is just a list of length 1, so everything below has
  // a single code path.
  const spansIn = (ranges?.length ? ranges : [[startMHz, endMHz]])
    .map(([a, b]) => [a, b]);

  // ── Snap to the 25 kHz grid ──────────────────────────────────────────
  const chunkAligned = roundTo(Math.max(STEP, Math.round(chunkMHz / STEP) * STEP), 3);
  const spans = spansIn.map(([a, b]) => [
    roundTo(Math.floor(a / STEP) * STEP, 3),
    roundTo(Math.ceil(b / STEP) * STEP, 3),
  ]);

  const snapped = chunkAligned !== chunkMHz ||
    spans.some(([a, b], i) => a !== spansIn[i][0] || b !== spansIn[i][1]);
  if (passNumber === 1 && snapped) {
    const fmt = ss => ss.map(([a, b]) => `${a}–${b}`).join(', ');
    say({ type: 'log', text:
      `Snapped to 25 kHz grid: ${fmt(spans)} MHz, chunk ${chunkAligned} MHz  ` +
      `(was ${fmt(spansIn)}, chunk ${chunkMHz})` });
  }
  chunkMHz = chunkAligned;

  // ── Build the chunk list, with dithered boundaries ───────────────────
  // Randomising chunk width ±15% each pass means IF filter rolloff artifacts
  // land at different frequencies, so they average out across passes.
  // Chunks never straddle a span boundary — a gap between spans is spectrum
  // we were told not to scan, not spectrum to sweep through. Each chunk
  // carries its span's hard edges so overlap padding can't spill past them.
  const chunks = [];
  for (const [spanStart, spanEnd] of spans) {
    let f = spanStart;
    while (f < spanEnd - STEP / 2) {
      let width = chunkMHz;
      if (passNumber > 1) {                       // pass 1 undithered: the
        const jitter = chunkMHz * (Math.random() * 2 * DITHER - DITHER);
        width = roundTo(Math.max(STEP * 4, chunkMHz + jitter), 3);
      }                                           // time estimate stays honest
      const cEnd = roundTo(Math.min(f + width, spanEnd), 3);
      chunks.push([roundTo(f, 3), cEnd, spanStart, spanEnd]);
      f = cEnd;
    }
  }
  const total = chunks.length;

  const spanText = spans.map(([a, b]) => `${a.toFixed(3)}–${b.toFixed(3)}`).join(', ');
  say({ type: 'log', text:
    `Pass ${passNumber}: ${spanText} MHz | ${total} chunks × ~${chunkMHz} MHz | ` +
    `${iterations} iter/chunk${passNumber > 1 ? ' (dithered)' : ''}` });

  // ── Sweep them ───────────────────────────────────────────────────────
  const accum = new Map();                        // freq -> [amp, ...]

  for (let idx = 0; idx < total; idx++) {
    const [cStart, cEnd, spanLo, spanHi] = chunks[idx];
    if (stopped()) {
      say({ type: 'log', text:
        `Pass ${passNumber} interrupted at chunk ${idx + 1}/${total}.` });
      break;
    }

    say({
      type: 'progress',
      value: Math.floor(100 * idx / total),
      text: `Pass ${passNumber} | Chunk ${idx + 1}/${total}: ` +
            `${cStart.toFixed(1)}–${cEnd.toFixed(1)} MHz`,
    });

    const paddedStart = roundTo(Math.max(spanLo, cStart - OVERLAP_MHZ), 3);
    const paddedEnd   = roundTo(Math.min(spanHi, cEnd   + OVERLAP_MHZ), 3);

    const points = await scanChunk(rfe, paddedStart, paddedEnd, iterations, stopped, say);
    if (!points) continue;

    if (passNumber === 1 && idx === 0) {
      const amps = points.map(p => p[1]);
      const sample = points.slice(0, 5)
        .map(([f, a]) => `('${f.toFixed(3)}', '${a.toFixed(1)}')`).join(', ');
      say({ type: 'log', text:
        `DIAG first chunk raw: min=${Math.min(...amps).toFixed(1)} ` +
        `max=${Math.max(...amps).toFixed(1)} dBm | samples: [${sample}]` });
    }

    for (const [freq, amp] of points) {
      const key = roundTo(freq, 3);
      const bin = accum.get(key);
      if (bin) bin.push(amp); else accum.set(key, [amp]);
    }

    say({ type: 'chunk_vis', data: points, chunk_idx: idx, total_chunks: total });
  }

  if (accum.size === 0) return [];

  // ── Reduce ───────────────────────────────────────────────────────────
  // Average within-chunk overlaps, then smooth at the device's native
  // resolution before resampling onto the 25 kHz grid.
  let raw = [...accum.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([freq, vals]) => [freq, roundTo(avgDBM(vals), 1)]);

  // A window of 3 means "the default" — auto-scale it to hold the smoothing
  // bandwidth near 160 kHz however wide the chunks are.
  if (smoothWindow === 3) {
    const binKHz = (chunkMHz * 1000) / NATIVE_POINTS;
    smoothWindow = Math.max(3, Math.round(160 / binKHz));
    if (smoothWindow % 2 === 0) smoothWindow += 1;      // keep it symmetric
  }
  raw = smooth(raw, smoothWindow);

  const result = resample(raw, STEP);
  say({ type: 'log', text:
    `Pass ${passNumber} ${stopped() ? 'partial — ' : ''}${result.length} data points.` });
  return result;
}

// ── one chunk ──────────────────────────────────────────────────────────

async function scanChunk(rfe, startMHz, endMHz, iterations, stopped, say) {
  const label = `${startMHz.toFixed(1)}–${endMHz.toFixed(1)} MHz`;

  // Retune. Waiting for the config echo both tells us where the radio
  // actually landed and draws the line under the previous chunk's sweeps.
  let cfg;
  try {
    await rfe.setRange(startMHz, endMHz);
    cfg = await rfe.waitForConfig(4000);
  } catch (e) {
    say({ type: 'log', text: `  Config error ${label}: ${e.message}` });
    return null;
  }

  await sleep(100);                       // LO lock

  // Flush phase — the first sweeps after a retune carry an amplitude bias
  // while the detector/AGC settles, so they are read and thrown away.
  const flushDeadline = Date.now() + 3000;
  for (let i = 0; i < SETTLE_FLUSH && !stopped(); i++) {
    const left = flushDeadline - Date.now();
    if (left <= 0) break;
    try { await rfe.nextSweep(left); } catch { break; }
  }

  // Collection phase.
  const sweeps = [];
  const deadline = Date.now() + Math.max(iterations * 1500 + 2000, 5000);
  let timedOut = false;
  while (sweeps.length < iterations && !stopped()) {
    const left = deadline - Date.now();
    if (left <= 0) { timedOut = true; break; }
    let amps;
    try {
      amps = await rfe.nextSweep(left);
    } catch {
      timedOut = true;
      break;                              // some sweeps may still be in hand
    }
    if (amps.length) sweeps.push(amps);
  }

  if (!sweeps.length) {
    if (timedOut) say({ type: 'log', text: `  Timeout on ${label} — skipping.` });
    return null;
  }

  // Average across sweeps in the linear power domain. The divisor is the
  // sweep count even where a short sweep contributes no term at index i —
  // scanner.py does the same, and ragged sweep lengths do not occur in
  // practice on this hardware.
  const nSweeps = sweeps.length;
  const nPoints = sweeps[nSweeps - 1].length;
  const averaged = [];
  for (let i = 0; i < nPoints; i++) {
    const vals = [];
    for (const s of sweeps) if (i < s.length) vals.push(dbmOf(s[i]));
    averaged.push(avgDBM(vals, nSweeps));
  }

  // Trim the edge bins — IF filter rolloff degrades amplitude accuracy at
  // each sweep end, which shows up as ripple at every chunk boundary.
  // OVERLAP_MHZ above is what makes the trimmed spectrum reappear.
  let first = 0, kept = averaged;
  if (nPoints > 2 * EDGE_TRIM + 10) {
    kept = averaged.slice(EDGE_TRIM, nPoints - EDGE_TRIM);
    first = EDGE_TRIM;
  }

  return kept.map((amp, i) => [
    roundTo(cfg.startMHz + (first + i) * cfg.stepMHz, 4),
    roundTo(amp, 1),
  ]);
}

// ── post-processing ────────────────────────────────────────────────────

/** Moving average in the linear power domain, symmetric window, edges kept.
 *  Takes the noise spikiness down without smearing narrow signals. */
export function smooth(points, window = 3) {
  if (points.length < window || window < 2) return points;
  const half = window >> 1;
  return points.map(([freq, amp], i) => {
    if (i < half || i >= points.length - half) return [freq, amp];
    const neighbours = points.slice(i - half, i + half + 1).map(p => p[1]);
    return [freq, roundTo(avgDBM(neighbours), 1)];
  });
}

/** Bin onto a stepMHz grid, averaging in the linear power domain.
 *  WWB will not import data spaced tighter than 25 kHz. */
export function resample(points, stepMHz) {
  if (!points.length) return points;
  const bins = new Map();
  for (const [freq, amp] of points) {
    const key = roundTo(Math.round(freq / stepMHz) * stepMHz, 3);
    const bin = bins.get(key);
    if (bin) bin.push(amp); else bins.set(key, [amp]);
  }
  return [...bins.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([freq, amps]) => [freq, roundTo(avgDBM(amps), 1)]);
}
