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
- **P2 — stats + export.** Port `stats.py` percentile accumulation and
  `export.py` CSV. Download client-side via a Blob.
- **P3 — the UI.** Reuse `web/templates/index.html`, `style.css`, and the
  canvas plot from `app.js` — they are already built and proven. Swap the
  server API calls for direct engine calls. Port `bands.py` to a JS table.
- **P4 — serve it.** Static files on the VPS + nginx + certbot. **HTTPS is
  mandatory** — Web Serial refuses to run outside a secure context.

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
