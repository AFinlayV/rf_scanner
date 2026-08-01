# RF Scanner

**Class:** standing rig — a tool used at every wireless coordination gig,
not a single-serving build. Maintained, versioned, not frozen.
**Riders:** `_roadcase/riders/rf-coordination.md` (not written yet — harvest
after the web UI has run a real show).
**Doctrine:** `_roadcase/DOCTRINE.md`.

## Shape

```
scanner.py stats.py export.py bands.py constants.py   the engine — no UI imports
ui.py live_mode.py rf_scanner.py                      Tk desktop frontend
webapp.py web/                                        browser frontend
tests/                                                48 passing, engine-level
```

**The engine imports no tkinter and no matplotlib, and that must stay true.**
It is what lets two frontends exist without duplicating logic. Anything that
would put a widget import into `scanner.py`/`stats.py`/`export.py`/`bands.py`
is wrong — push it into a frontend instead.

## Frontends

The Tk app (`rf_scanner.py`) has macOS focus and scroll bugs: the window
needs a titlebar click before it accepts input, panes don't scroll. That is
why the web UI exists.

**Both work. Do not delete `ui.py` until the web UI has survived a real
gig** — the desktop app is the fallback if something fails at a show.

**v1 acceptance (2026-08-01): done when Alex can run a band-multiselect scan
and get a WWB CSV from a browser — including a phone — without touching the
Tk window, at a real gig.** Until that has happened, the web UI is unproven
no matter how good the mock tests look.

Progress against it: **bench-verified on real hardware 2026-08-01** — WSUB1G
on `/dev/cu.usbserial-210`, disjoint H5 + J10 scan, 1890 bins exported, zero
bins in the skipped 542–584 MHz gap, every bin on the 25 kHz grid, strongest
return 585.8 MHz at −81.1 dBm (Bay Area UHF broadcast). Still not gig-proven:
no phone-at-a-show run, and Alex has not driven a coordination job on it.

## Hardware reality

The RF Explorer is a **USB serial device**. Whatever runs the scan must be
physically attached to it. The web UI is served from that same machine (the
Mac, or a box at the gig) and reached over LAN/Tailscale. **The VPS can
never run a scan** — it has no radio. Don't design toward hosting it there.

## Do not build

- **No second frontend framework.** The web UI is vanilla JS + a canvas.
  No React, no build step. It is ~450 lines; keep it that size.
- **No Live Mode / waterfall in the web UI** without a decision written
  here first. Deliberately left in the Tk app for v1.
- **No multi-user or multi-device session model.** One operator, one radio,
  one scan. The single global `Session` in `webapp.py` is correct.
- **No band data invented from memory.** Every entry in `bands.py` traces to
  a published source (see its SOURCES block). Wrong band edges mean scanning
  the wrong spectrum at a show. Ask Alex or cite a source.
- No hosting the scan on the VPS (see above).

## Band data

Band letters mean different spectrum on different product lines (SLX `H5` ≠
ULX-D `H50`), so `family` is part of every band's identity. A band is a
**list** of ranges, because the US 600 MHz repack split some of them
(Shure J8 = 554–608 **+** 614–616). `scan_pass(..., ranges=[...])` takes
that list; chunks never straddle a span boundary and overlap padding never
spills past one.

## Open: the radio stops answering after an interrupted scan

**Reproduced 2026-08-01, cause not fully understood, fix UNTESTED.**

Symptom: stop a scan mid-chunk, leave it idle a few minutes, start another
— every chunk times out, the pass returns zero data, and the UI shows an
empty graph. Looks like a broken display; it is a radio that has stopped
answering.

What is actually known:
- Reconnecting (`ClosePort` + `ConnectPort` + handshake) revives it every
  time, immediately.
- **Flushing the serial buffer does not fix it.** Measured: after four idle
  minutes only **53 stale bytes** were waiting — nowhere near an overflow —
  and every chunk still timed out. The "buffer fills up" theory is wrong.
  `reset_stream()` is kept as cheap hygiene and as the diagnostic that
  produced that number, not as a fix.
- Short gaps between stop and restart do NOT trigger it. Idle time is a
  necessary ingredient; the threshold is unmeasured (seen at ~4 and ~11
  minutes, not seen at seconds).
- Untested: whether a *clean* pass boundary (rather than a mid-chunk stop)
  also poisons it, and whether the Tk app shows the same behaviour. It
  shares the engine, so it probably does.

Current mitigation, **written but never run**: on a pass that returns no
data, `scan_worker` calls `RFExplorerScanner.reconnect()` and retries once,
then gives up with an actionable status. Nobody has watched this fire.
**Verify it before trusting it at a show** — reproduce with the recipe
above and confirm the log shows "Reopening the port… Reconnect succeeded"
followed by real data.

## Known state

- All 50 tests pass. The two long-standing failures (bins 50 kHz apart where
  the tests wanted 25) were the tests being wrong, fixed 2026-08-01.

## The 25 kHz grid is sparse on a single pass, on purpose

Do not "fix" this, and do not interpolate to fill it.

The radio returns ~112 points per sweep no matter how wide the sweep is.
`OVERLAP_MHZ = 1.0` pads a 2 MHz chunk out to a 4 MHz sweep, so native bins
land ~36 kHz apart — **coarser than the 25 kHz grid**. Slots no bin landed
in stay empty. Filling them would mean writing an amplitude the radio never
measured, and on a coordination export an invented number reads as a clear
channel. WWB's rule is *at least* 25 kHz between points, so 50 kHz is valid;
the 2026-08-01 hardware run exported 1890 bins this way.

Multi-pass accumulation fills the grid honestly — dithered chunk widths put
the native bins on different frequencies each pass. Measured: three passes
of 470–474 leave zero gaps above 25 kHz in 60/60 trials. A show export is
always multi-pass, so the holes are a single-pass artifact operators never
see.

**Untested idea, needs a radio:** `OVERLAP_MHZ` only has to exceed the
`EDGE_TRIM = 10` bins cut from each sweep end. For a 2 MHz chunk that is
~0.22 MHz, not 1.0 — the flat 1.0 was calibrated for a wider chunk (see its
"~58 kHz/bin" comment). Dropping it to ~0.3 would make native bins ~23 kHz,
finer than the grid, and fill it with *real* samples. Not done: the trim
fraction depends on the point count the device actually returns, which
varies by model, so too small an overlap reopens real chunk-seam gaps. Bench
it against the WSUB1G before believing it.
