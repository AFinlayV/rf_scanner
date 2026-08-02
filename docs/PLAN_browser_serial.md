# Plan — the browser owns the radio (Web Serial)

*Written 2026-08-01. Decision made with Alex the same day. Read this before
touching `browser/`.*

## What changes and why

Today the Python process owns the RF Explorer, so scanning only works on a
machine that has this repo, Python, and the deps installed. Alex wants to
walk up to **any** computer, plug in the radio, open a URL, and scan.

The only architecture that delivers that is **Web Serial** — a browser API
that opens a USB serial device directly, with the user picking the port from
a native dialog. Verified available 2026-08-01.

```
any Chrome machine  →  rf.alexthe5th.com  (VPS serves static files)
                    →  page opens the RF Explorer plugged into THAT machine
```

**The server holds no state at all.** No scan session, no accumulator, no
serial port, nothing to wedge. That deletes the entire class of bug this
repo spent 2026-08-01 fixing, makes the VPS a plain static host, and lets
two people use it at once without colliding.

### Stack escalation, recorded per DOCTRINE rule 5

This moves the scan engine from Python into browser JavaScript. The boring
stack cannot do this: the radio is USB serial on the *viewer's* machine, and
only a browser API can reach it. Still no framework and no build step —
plain ES modules, same as `web/static/app.js`.

## The protocol (decoded from the RFExplorer pip package, 2026-08-01)

Ground truth is the reference implementation on disk, not the web docs.

| Piece | Format |
|---|---|
| Command framing | `"#" + chr(len(cmd) + 2) + cmd`, written as bytes |
| Request config + start feed | `C0` |
| Set sweep range | `C2-F:{startKHz:07d},{endKHz:07d},{topDBM},{bottomDBM}` |
| Averaging calculator | `C+\x02` (eCalculator.AVG = 2) |
| Config reply | `#C2-F:` line, ≥60 chars — startKHz at 6 (7 ch), stepHz at 14 (7 **or 8** ch, comma-delimited), then top/bottom (4 ch each), then data points (4 ch) |
| Sweep frame | `$S` + length byte + N amplitude bytes + `\r\n` |
| Amplitude | `dBm = -byteValue / 2.0` |
| Frequency of bin i | `startMHz + i × stepMHz` |

Two traps: the step field is 7 **or** 8 characters (branch on where the
comma lands), and the sweep length byte is stripped by the reference
implementation's read thread before parsing — the wire has it, the parsed
line does not.

## Phases

Each is one commit. Do not start the next until the previous is verified
with the radio attached.

- **P0 — protocol spike.** One HTML file: open port, `C0`, parse `#C2-F:`,
  set a small range, read one `$S`, print points. **Proves the whole risk.**
  Verify: printed frequencies and amplitudes match what `webapp.py` reports
  for the same range.
- **P1 — the sweep engine.** Port `scanner.py`'s chunk loop: per-chunk
  config, LO settle, flush phase, collect N sweeps, edge trim, overlap
  padding, 25 kHz grid snap, dithering on passes 2+. This is the RF
  expertise; port it faithfully rather than reinventing.
  **Written (`browser/sweep.js`), parity-verified offline, awaiting the
  radio** — see "P1 status" below.
- **P2 — stats + export.** Port `stats.py` percentile accumulation and
  `export.py` CSV. Download client-side via a Blob.
  **Done** (`browser/stats.js`, `browser/export.js`) — see "P2 status".
- **P3 — the UI.** Reuse `web/templates/index.html`, `style.css`, and the
  canvas plot from `app.js` — they are already built and proven. Swap the
  server API calls for direct engine calls. Port `bands.py` to a JS table.
  **Band table done** (`browser/bands.js`); the UI itself is not started.
- **P4 — serve it.** Static files on the VPS + nginx + certbot. **HTTPS is
  mandatory** — Web Serial refuses to run outside a secure context.

## P1 status — parity proven on the bench, not yet on the radio

`browser/sweep.js` is a line-for-line port of `scan_pass` / `_scan_chunk`.
It was checked against the Python by running **both engines over the same
fake radio** — a deterministic synthetic spectrum, integer-derived so JS and
Python see bit-identical amplitude bytes. Five cases: one contiguous span,
the repack-split J8 (554–608 + 614–616), off-grid input, a span narrower
than one chunk, and three disjoint spans.

```
                        bins   max |Δfreq|   max |Δamp|   log text
  contiguous 470-476      92      0.0e+00      0.0e+00    identical
  repack split J8        865      0.0e+00      0.0e+00    identical
  off-grid input         127      0.0e+00      0.0e+00    identical
  span under one chunk    67      0.0e+00      0.0e+00    identical
  three spans            348      0.0e+00      0.0e+00    identical
```

Bit-identical, not merely close. Twelve dithered passes over the split band
also held the invariants that matter at a show: **zero bins in the 608–614
gap**, zero bins outside the requested spans, every bin on the 25 kHz grid.

Two deviations from the Python, both forced by the wire and both documented
at the top of `sweep.js`: a raw `$S` frame carries no start frequency, so the
`#C2-F:` config echo is what separates one chunk's sweeps from the last's;
and bin frequencies come from that echo rather than from the requested span
over the point count. They agree whenever the radio tunes where it was asked.

**What is NOT verified: the browser talking to the actual radio through this
engine.** `navigator.serial.requestPort()` needs a human at the port picker.
Bench harness for it is `browser/spike.html` → "Run one pass".

Python reference, measured 2026-08-01 17:02 on `/dev/cu.usbserial-210`,
470–476 MHz, chunk 6, 5 iter/chunk:

```
  bins 92 | span 470.550–475.450 MHz | floor -97.8 | peak -85.6 dBm
  strongest: 472.975 (-85.6), 472.925 (-85.9), 473.025 (-86.2) MHz
```

Compare the browser against a **fresh** Python run, not these numbers — the
RF environment drifts, and the P0 run an hour earlier saw a -101.7 floor over
the same span. Run the two back to back.

That same run also closed an open question: the device reports
`OffsetDB=0 | CfgOffset=0`, so the raw `-byte/2` amplitude decode needs no
offset correction. If a future radio reports a non-zero offset, `rfe.js`
would have to parse it out of the config reply's later fields.

Not ported, deliberately: the empty-pass auto-recovery (reconnect + retry)
that `webapp.py`'s `scan_worker` does. It belongs with the multi-pass loop,
which is P3's, not P1's.

## P2 status — done, and the rounding is the whole story

`browser/stats.js` and `browser/export.js` reproduce numpy's defaults:
linear-interpolation percentiles (**not** nearest-rank) and population
standard deviation (ddof=0). Checked against Python over 1/2/3/5/8-pass and
all-identical datasets, 300 bins each: every `getBinStats` figure agrees to
the floating-point noise floor (max |Δ| 1.4e-14, summation-order only), every
`exportPercentile`/`exportMax`/`exportMin` value is exactly equal, and all
six CSVs are byte-identical to `save_wwb_csv`'s.

**The one real bug found and fixed here was rounding.** Python's `round()`
rounds half to even *on the exact binary value*; the JS shortcuts all differ,
and a tie test with an epsilon is the worst of them because it misclassifies
ordinary near-ties. The double behind `-85.35` is really `-85.34999…`, so
Python gives `-85.3` and an epsilon rule gives `-85.4`.

That is not a theoretical case: **the median of an even number of passes is
the midpoint of two 1-decimal readings, so it lands on a `.x5` value about
half the time.** Measured on a 2-pass P50 export, the epsilon version got
**67 of 300 bins wrong** by 0.1 dB, in both directions. Odd pass counts were
unaffected — a percentile of an odd count lands on a real sample.

`roundHalfEven` now reads the exact decimal expansion via `toFixed(20)` and
rounds it as a decimal. Verified against Python's `round()` over 70,023
values at 1, 3 and 4 decimals — realistic dBm and MHz floats, every exact tie
a double can represent, and the near-tie band around each — zero divergences.
It is duplicated in both files on purpose (rule 6); fix one, fix the other.

## Band table status — transcribed, verified equal

`browser/bands.js` is a mechanical transcription of `bands.py`: 26 bands,
17 US-legal, `US_USABLE`, `BY_LABEL`, all 6 `FREQ_PRESETS`, and
`merge`/`intersect`/`totalWidth`/`usBands`/`rangesFor`. Dumped both tables to
JSON and compared: identical, including the four repack-split multi-range
bands (J50, G57, G56, J8) and the preset and band display order the UI reads.
No band edge was retyped from memory; the SOURCES block came across.

One thing to look at when you next touch `bands.py` — **it is a source issue,
not a port issue, and both files behave the same today.** Five 600 MHz bands
carry the note "not usable in the US post-repack", but K53 (606–698) and GB
(606–648) both compute `usLegal = "partial"`, because 606–608 survived the
repack and intersects `US_USABLE`. The computed field is right and the note
is a generic label; `bands.py` already carries an inline caveat about this
for GB but not for K53.

## Acceptance

**Done when Alex opens `rf.alexthe5th.com` on a Chrome machine that has
never been set up, plugs in the RF Explorer, scans a band selection, and
downloads a CSV that Wireless Workbench imports — with no Python on that
machine.**

Bench runs do not count for P4; the point is a machine that was never
prepared.

## Constraints to state plainly

- **Chrome or Edge only.** Safari has no Web Serial, so no iPhone/iPad as
  the scanning machine. Firefox neither.
- **HTTPS required**, except on localhost.
- **One permission click per browser** — a native port picker, remembered
  afterwards. Not a passcode.
- Unauthenticated and public is Alex's explicit decision (2026-08-01). It is
  far less alarming here than with `webapp.py`, because there is no server
  state and no server-side radio: a stranger loading the page gets a page
  that can talk to no hardware.

## Do not

- Do not delete `webapp.py` or `ui.py`. Three frontends is fine while this
  is unproven; the acceptance test above is the gate.
- Do not reimplement the RF logic from first principles. `scanner.py` is
  hard-won — the settle/flush phases, the ±15% dither, the 1 MHz overlap,
  the edge trim. Port it.
- Do not add a build step, a framework, or npm.
