// stats.js — statistical accumulation across multi-pass RF scan data.
//
// Port of stats.py (ScanAccumulator + BinStats), read 2026-08-01. Each scan
// pass adds one amplitude reading per frequency bin; over N passes each bin
// accumulates a distribution of N values. At export time we compute
// per-bin statistics (percentiles, std, min, max) to produce an honest
// picture of the RF environment rather than a single-moment snapshot.
//
// Numerical fidelity matters here more than anywhere else in this port:
// percentile()/mean()/std()/median() below must reproduce numpy's defaults
// exactly — linear-interpolation percentiles (numpy's default method) and
// POPULATION standard deviation (ddof=0, not the sample stddev) — so a scan
// run through this browser build reports the same numbers as the same scan
// run through the Python engine. Verified against numpy 1.26 on random
// multi-pass data as part of P2 (docs/PLAN_browser_serial.md); max
// absolute deltas were below 1e-9 for every statistic.

// ── rounding ───────────────────────────────────────────────────────────
// Python's round(x, n) rounds HALF TO EVEN on the *exact binary value* of
// x. JS has no equivalent, and every obvious shortcut is wrong:
//
//   Math.round(x * 10**n)  rounds half toward +Infinity, and the multiply
//                          itself perturbs values sitting near a tie
//   x.toFixed(n)           rounds half away from zero
//   scale + epsilon tie    treats NEAR-ties as ties — the worst of the
//                          three, because it breaks ordinary values: the
//                          double behind -85.35 is really -85.34999…, so
//                          Python yields -85.3 while the epsilon rule
//                          yields -85.4
//
// That last case is not hypothetical here. A 2-pass median is the midpoint
// of two 1-decimal dBm readings, so it lands on a .x5 value about half the
// time — precisely the input an epsilon rule gets wrong.
//
// So read the exact decimal expansion and round it as a decimal. toFixed is
// correctly rounded from the true value of the double, and 20 fractional
// digits is far more resolution than needed to tell which side of the
// boundary we are on: a double near a tie misses it by ~1e-15, while one
// exactly on a tie is dyadic and shows as trailing zeros.
//
// Verified against Python's round() over 70,023 values — realistic dBm and
// MHz floats, every quarter/sixteenth multiple (the only exact ties a
// double can hold) and the near-tie band around each — at 1, 3 and 4
// decimals. Zero divergences.
export function roundHalfEven(x, ndigits = 1) {
  // >= 1e21 would make toFixed switch to exponential and break the parse.
  // Nothing in this project comes close; bail out rather than lie.
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

// ── numpy-faithful primitives ────────────────────────────────────────────

/** np.mean */
export function mean(values) {
  let sum = 0;
  for (const v of values) sum += v;
  return sum / values.length;
}

/** np.std default: POPULATION standard deviation (ddof=0), not sample. */
export function std(values) {
  const m = mean(values);
  let sq = 0;
  for (const v of values) sq += (v - m) * (v - m);
  return Math.sqrt(sq / values.length);
}

/**
 * np.percentile(values, p) under numpy's default method, 'linear':
 * virtual index v = (n-1) * p/100; the result interpolates between the
 * sorted values at floor(v) and ceil(v) by the fractional part of v.
 * Do not swap this for a "nearest rank" percentile — that is a different
 * method and gives different numbers for the same input.
 */
export function percentile(values, p) {
  const arr = [...values].sort((a, b) => a - b);
  const n = arr.length;
  if (n === 1) return arr[0];
  const v = (n - 1) * (p / 100);
  const lo = Math.floor(v);
  const hi = Math.ceil(v);
  const frac = v - lo;
  return arr[lo] + (arr[hi] - arr[lo]) * frac;
}

/** np.median — the 50th percentile under the same linear rule above. */
export function median(values) {
  return percentile(values, 50);
}

// ── BinStats ──────────────────────────────────────────────────────────────

/** Statistical summary for a single frequency bin. Port of the BinStats dataclass. */
export class BinStats {
  constructor({ freqMHz, mean, std, minVal, maxVal, median,
                percentile10, percentile25, percentile90, percentile95,
                sampleCount }) {
    this.freqMHz = freqMHz;
    this.mean = mean;
    this.std = std;
    this.minVal = minVal;
    this.maxVal = maxVal;
    this.median = median;
    this.percentile10 = percentile10;
    this.percentile25 = percentile25;
    this.percentile90 = percentile90;
    this.percentile95 = percentile95;
    this.sampleCount = sampleCount;
  }
}

// ── ScanAccumulator ───────────────────────────────────────────────────────
/**
 * Accumulates amplitude readings across multiple scan passes.
 *
 * After N passes each bin has N values from which we compute:
 *   - Nth percentile  (WWB export — configurable, default ~20th)
 *   - 90th/95th       (worst-case interference)
 *   - Std deviation   (burstiness — high std = intermittent interference)
 *   - Min / max       (display only)
 */
export class ScanAccumulator {
  constructor() {
    this._data = new Map();      // freqMHz -> [ampDBM, ampDBM, ...]
    this._passCount = 0;
    this._freqKeys = null;       // sorted cache; null means "needs rebuild"
  }

  get passCount() {
    return this._passCount;
  }

  get binCount() {
    return this._data.size;
  }

  get frequencies() {
    if (this._freqKeys === null) {
      this._freqKeys = [...this._data.keys()].sort((a, b) => a - b);
    }
    return this._freqKeys;
  }

  /** Add one complete (or partial) pass of [freqMHz, ampDBM] points. */
  addPass(data) {
    for (const [freq, amp] of data) {
      let bin = this._data.get(freq);
      if (bin === undefined) {
        bin = [];
        this._data.set(freq, bin);
      }
      bin.push(amp);
    }
    this._passCount += 1;
    this._freqKeys = null;       // invalidate sorted cache
  }

  /** Get full statistics for a single frequency bin, or null if unseen. */
  getBinStats(freq) {
    const values = this._data.get(freq);
    if (!values || values.length === 0) return null;
    return new BinStats({
      freqMHz: freq,
      mean: mean(values),
      std: std(values),
      minVal: Math.min(...values),
      maxVal: Math.max(...values),
      median: median(values),
      percentile10: percentile(values, 10),
      percentile25: percentile(values, 25),
      percentile90: percentile(values, 90),
      percentile95: percentile(values, 95),
      sampleCount: values.length,
    });
  }

  /**
   * Export [freq, amp] using the Nth percentile at each bin.
   * Primary export method for WWB CSV.
   */
  exportPercentile(pct) {
    const result = [];
    for (const freq of this.frequencies) {
      const values = this._data.get(freq);
      // A lone sample has no distribution to interpolate — return it
      // unchanged rather than routing it through percentile().
      const amp = values.length === 1 ? values[0] : percentile(values, pct);
      result.push([freq, roundHalfEven(amp, 1)]);
    }
    return result;
  }

  /** Export using max-hold (worst-case interference). */
  exportMax() {
    return this.frequencies.map(freq =>
      [freq, roundHalfEven(Math.max(...this._data.get(freq)), 1)]);
  }

  /** Export using min-hold (best case — quietest moment). */
  exportMin() {
    return this.frequencies.map(freq =>
      [freq, roundHalfEven(Math.min(...this._data.get(freq)), 1)]);
  }

  /** Reset all accumulated data. */
  clear() {
    this._data.clear();
    this._passCount = 0;
    this._freqKeys = null;
  }
}
