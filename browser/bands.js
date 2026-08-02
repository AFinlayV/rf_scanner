// bands.js — manufacturer wireless band designations, as scannable
// frequency ranges.
//
// Faithful port of bands.py for P3 of docs/PLAN_browser_serial.md — every
// band edge, label, and family name below is transcribed verbatim from the
// Python source, not re-derived from general RF knowledge. If something
// here looks wrong, it was wrong in bands.py too: fix it there (a one-line
// edit to BANDS) and re-port, don't patch the number here.
//
// WHY A BAND IS A *LIST* OF RANGES
// --------------------------------
// After the US 600 MHz repack (completed 2020) the FCC auctioned 617–652 and
// 663–698 MHz to mobile carriers. Wireless mics kept 470–608 MHz plus the
// 2 MHz duplex gap at 614–616. So a band like Shure J8 — originally a clean
// 554–626 MHz — is now literally two disjoint pieces: 554–608 and 614–616.
// That is why a band's `ranges` is a list, and why the scanner takes a list
// of spans rather than one start/end pair.
//
// ACCURACY
// --------
// Compiled 2026-08-01 from Shure and Sennheiser published figures (see
// SOURCES at the bottom). Shure's master band chart PDF was not machine-
// readable, so this covers the families that could be confirmed from primary
// or multiple independent sources — it is NOT every band Shure has shipped.
// Band letters mean different ranges on different product lines (Shure H5 on
// SLX is not the same spectrum as H50 on ULX-D), which is why `family` is
// part of every entry's identity.
//
// Verify against your own gear before coordinating a show. Adding or
// correcting an entry is a one-line edit to BANDS below.

// US wireless-mic spectrum after the 600 MHz repack: the UHF TV band, plus
// the duplex gap. Everything above 616 MHz is mobile carrier spectrum now.
export const US_USABLE = [
  Object.freeze([470.0, 608.0]),
  Object.freeze([614.0, 616.0]),
];

// Each entry in BANDS has this shape — a plain frozen object, not a class
// (nothing here needs methods). label/spanText/usLegal are derived once at
// table-build time below instead of recomputed on every access like
// bands.py's @property does; harmless, since the table never changes after
// load.
//   name      manufacturer designation, e.g. "H5"
//   maker     "Shure" | "Sennheiser"
//   family    product line the name belongs to. Band letters mean
//             different ranges on different product lines — Shure H5 on
//             SLX is not the same spectrum as H50 on ULX-D — so family is
//             part of a band's identity, same as name.
//   ranges    [[startMHz, endMHz], ...] MHz spans, ascending. Always a
//             LIST: the US 600 MHz repack split some bands in two (e.g.
//             Shure J8 = 554–608 plus the 614–616 duplex gap). Never
//             flatten a two-range band into one span.
//   note      "" when there's nothing special to say
//   label     `${maker} ${family} ${name}` — unique key, used by BY_LABEL
//   spanText  human-readable span, e.g. "554–608, 614–616 MHz"
//   usLegal   "full" | "partial" | "none" — how much of ranges survives
//             the US repack (ranges intersected against US_USABLE)

// Mirrors Python's f"{x:g}" (general format, default 6 significant digits,
// trailing zeros stripped) — bands.py uses this to build span_text. Every
// edge in this table happens to be a whole number, so it reduces to the
// bare integer, but keep the real formatter rather than assume that stays
// true after the next edit to BANDS.
function formatG(x, precision = 6) {
  if (x === 0) return '0';
  const s = x.toPrecision(precision);
  if (s.includes('e')) {
    let [mantissa, exp] = s.split('e');
    if (mantissa.includes('.')) mantissa = mantissa.replace(/0+$/, '').replace(/\.$/, '');
    const expNum = parseInt(exp, 10);
    const sign = expNum < 0 ? '-' : '+';
    return `${mantissa}e${sign}${String(Math.abs(expNum)).padStart(2, '0')}`;
  }
  if (s.includes('.')) return s.replace(/0+$/, '').replace(/\.$/, '');
  return s;
}

function band(name, maker, family, ranges, note = '') {
  const frozenRanges = Object.freeze(ranges.map(([a, b]) => Object.freeze([a, b])));
  const width = frozenRanges.reduce((sum, [a, b]) => sum + (b - a), 0);
  const kept = intersect(frozenRanges, US_USABLE);
  let usLegal;
  if (kept.length === 0) {
    usLegal = 'none';
  } else {
    const keptWidth = kept.reduce((sum, [a, b]) => sum + (b - a), 0);
    usLegal = Math.abs(keptWidth - width) < 0.001 ? 'full' : 'partial';
  }
  return Object.freeze({
    name,
    maker,
    family,
    ranges: frozenRanges,
    note,
    label: `${maker} ${family} ${name}`,
    spanText: frozenRanges.map(([a, b]) => `${formatG(a)}–${formatG(b)}`).join(', ') + ' MHz',
    usLegal,
  });
}

// ─────────────────────────────────────────────────────────────── range math ──

/** Sort and union overlapping/touching spans. Picking G57 and H5 (which
 * overlap heavily) must scan the union once, not the same MHz twice. */
export function merge(ranges) {
  if (!ranges || ranges.length === 0) return [];
  const sorted = ranges
    .map(([a, b]) => [Number(a), Number(b)])
    .sort((x, y) => (x[0] - y[0]) || (x[1] - y[1]));
  const out = [];
  for (const [start, end] of sorted) {
    if (out.length && start <= out[out.length - 1][1]) {
      if (end > out[out.length - 1][1]) {
        out[out.length - 1] = [out[out.length - 1][0], end];
      }
    } else {
      out.push([start, end]);
    }
  }
  return out;
}

/** The parts of `ranges` that fall inside `mask`. */
export function intersect(ranges, mask) {
  const out = [];
  for (const [a, b] of merge(ranges)) {
    for (const [mA, mB] of mask) {
      const lo = Math.max(a, mA);
      const hi = Math.min(b, mB);
      if (hi - lo > 0.0001) out.push([lo, hi]);
    }
  }
  return merge(out);
}

export function totalWidth(ranges) {
  return merge(ranges).reduce((sum, [a, b]) => sum + (b - a), 0);
}

// ──────────────────────────────────────────────────────────────── the table ──
// Ranges are the manufacturer's published tuning range, trimmed where the
// repack removed spectrum (originals noted).

export const BANDS = [
  // ── Shure ULX-D / QLX-D ──────────────────────────────────────────────
  band('G50', 'Shure', 'ULX-D/QLX-D', [[470.0, 534.0]]),
  band('H50', 'Shure', 'ULX-D/QLX-D', [[534.0, 598.0]]),
  band('J50', 'Shure', 'ULX-D/QLX-D', [[572.0, 608.0], [614.0, 616.0]],
       'orig. 572–636; repack trimmed'),
  band('L50', 'Shure', 'ULX-D/QLX-D', [[632.0, 698.0]],
       '600 MHz band — not usable in the US post-repack'),

  // ── Shure Axient Digital ─────────────────────────────────────────────
  band('G57', 'Shure', 'Axient Digital', [[470.0, 608.0], [614.0, 616.0]],
       'orig. 470–616; repack trimmed'),
  band('G56', 'Shure', 'Axient Digital', [[470.0, 608.0], [614.0, 616.0]],
       'orig. 470–636; repack trimmed'),
  band('K53', 'Shure', 'Axient Digital', [[606.0, 698.0]],
       '600 MHz band — not usable in the US post-repack'),

  // ── Shure UHF-R / legacy wideband ────────────────────────────────────
  band('J8', 'Shure', 'UHF-R', [[554.0, 608.0], [614.0, 616.0]],
       'orig. 554–626; J8A is the post-repack version'),

  // ── Shure SLX (analog) ───────────────────────────────────────────────
  band('G4E', 'Shure', 'SLX', [[470.0, 494.0]]),
  band('G5E', 'Shure', 'SLX', [[494.0, 518.0]]),
  band('H5', 'Shure', 'SLX', [[518.0, 542.0]]),
  band('J3', 'Shure', 'SLX', [[572.0, 596.0]]),
  band('L4', 'Shure', 'SLX', [[638.0, 662.0]],
       '600 MHz band — not usable in the US post-repack'),
  band('P4', 'Shure', 'SLX', [[702.0, 726.0]],
       '700 MHz — illegal for wireless mics in the US since 2010'),
  band('R5', 'Shure', 'SLX', [[800.0, 820.0]], 'not US wireless-mic spectrum'),
  band('S6', 'Shure', 'SLX', [[838.0, 865.0]], 'not US wireless-mic spectrum'),

  // ── Shure BLX ────────────────────────────────────────────────────────
  band('J10', 'Shure', 'BLX', [[584.0, 608.0]], 'orig. 584–608'),

  // ── Sennheiser evolution wireless G3/G4 ──────────────────────────────
  band('A1', 'Sennheiser', 'ew G3/G4', [[470.0, 516.0]]),
  band('A', 'Sennheiser', 'ew G3/G4', [[516.0, 558.0]]),
  band('AS', 'Sennheiser', 'ew G4', [[520.0, 558.0]]),
  band('G', 'Sennheiser', 'ew G3/G4', [[566.0, 608.0]]),
  band('GB', 'Sennheiser', 'ew G3/G4', [[606.0, 648.0]],
       '600 MHz band — not usable in the US post-repack'),
  band('B', 'Sennheiser', 'ew G3/G4', [[626.0, 668.0]],
       '600 MHz band — not usable in the US post-repack'),
  band('C', 'Sennheiser', 'ew G3/G4', [[734.0, 776.0]], 'not US spectrum'),
  band('D', 'Sennheiser', 'ew G3/G4', [[780.0, 822.0]], 'not US spectrum'),
  band('E', 'Sennheiser', 'ew G3/G4', [[823.0, 865.0]], 'not US spectrum'),
];

export const BY_LABEL = {};
for (const b of BANDS) BY_LABEL[b.label] = b;

/** Bands with any spectrum still usable in the US. */
export function usBands() {
  return BANDS.filter((b) => b.usLegal !== 'none');
}

/** Selected band labels -> merged scannable spans. */
export function rangesFor(labels) {
  const picked = [];
  for (const lab of labels) {
    const b = BY_LABEL[lab];
    if (b) picked.push(...b.ranges);
  }
  return merge(picked);
}

// Legacy single-span quick picks, kept for the plain preset dropdown.
export const FREQ_PRESETS = {
  'Sub-GHz  470–900 MHz': Object.freeze([470.0, 900.0]),
  'US mic   470–608 MHz': Object.freeze([470.0, 608.0]),
  'UHF      470–698 MHz': Object.freeze([470.0, 698.0]),
  'UHF      470–608 MHz': Object.freeze([470.0, 608.0]),
  'VHF      174–216 MHz': Object.freeze([174.0, 216.0]),
  'Custom': null,
};

// SOURCES (fetched 2026-08-01)
//   Shure G50/H50/J50/L50 ..... shure.com "ULX-D Digital Wireless in the H50 Band: FAQs"
//   Shure G56/G57/K53/J8 ...... Shure US frequency-band listings
//   Shure SLX bands ........... Shure SLX frequency compatibility chart + retailer specs
//   Sennheiser G3/G4 bands .... published G3/G4 band tables
//   US repack spectrum ........ FCC 600 MHz transition: mics keep 470–608 + 614–616
