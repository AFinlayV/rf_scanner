# RF Scanner

**Class:** standing rig — a tool used at every wireless coordination gig,
not a single-serving build. Maintained, versioned, not frozen.
**Riders:** `_roadcase/riders/rf-coordination.md` (not written yet — harvest
after the web UI has run a real show).
**Doctrine:** `_roadcase/DOCTRINE.md`.

## Start here (written 2026-08-01, end of day)

The browser build in `browser/` is written end to end — all four phases of
`docs/PLAN_browser_serial.md`. **Two gates are open, and both need Alex and
the radio, not more code.**

**1. Hardware-verify the browser build.** Nothing in `browser/` has ever
talked to the real RF Explorer; it has only been driven against a fake
serial device. Do this before anything else, because a misdecoded protocol
field and a DNS problem look identical once the thing is deployed.

```bash
cd browser && python3 -m http.server 8765
```

`http://localhost:8765/spike.html` → Connect → **Run one pass** over
470–476 MHz, chunk 6, 5 iter. Compare against a **fresh** Python run over
the same span — not the numbers written down anywhere, the RF environment
drifts 4 dB in an hour. Then `index.html` for the real UI. Chrome or Edge;
localhost is a secure context so Web Serial works without TLS.

What would falsify the port: a config reply that fails to parse (the step
field is 7 *or* 8 characters), bins landing on the wrong frequencies, or
chunks timing out — the settle and timeout constants in `sweep.js` were
copied from `scanner.py` and never tested against hardware that answers 40×
slower than the fake.

**2. Then the deploy.** Blocked on a DNS record Alex is creating:
`rf.alexthe5th.com A -> 144.202.111.184`. Once it resolves, the steps are in
`browser/deploy/nginx-rf.conf`'s header. `nginx -t` covers showrunner too —
if it fails, do not reload.

**Open decision, unrelated to the above:** whether to merge
`claude/vibrant-solomon-ba6505`. See "Known state" at the bottom.

## Shape

```
scanner.py stats.py export.py bands.py constants.py   the engine — no UI imports
ui.py live_mode.py rf_scanner.py                      Tk desktop frontend
webapp.py web/                                        server-side web frontend
browser/                                              the engine again, in JS
tests/                                                48 passing, engine-level
```

**The engine imports no tkinter and no matplotlib, and that must stay true.**
It is what lets three frontends exist without duplicating logic. Anything
that would put a widget import into
`scanner.py`/`stats.py`/`export.py`/`bands.py` is wrong — push it into a
frontend instead.

## Frontends

The Tk app (`rf_scanner.py`) has macOS focus and scroll bugs: the window
needs a titlebar click before it accepts input, panes don't scroll. That is
why the web UI exists.

**Both work. Do not delete `ui.py` until the web UI has survived a real
gig** — the desktop app is the fallback if something fails at a show.

A **third** frontend lives in `browser/`: the page opens the radio itself
over Web Serial, so the engine runs in JavaScript and the server holds no
state. It is feature-complete — engine, statistics, CSV export, band
multiselect, plot, multi-pass loop and empty-pass auto-recovery — and the
whole app has been driven end to end against a fake serial device that
speaks the real wire protocol.

**It is still not gig-ready, because none of it has ever talked to the
actual radio.** A fake that implements the protocol correctly cannot catch a
place where the protocol was decoded wrong, and answers in 25 ms where the
radio takes ~1s per chunk, so every settle and timeout constant is untested
in anger. **Read `docs/PLAN_browser_serial.md` before touching `browser/`**;
it records the wire protocol, the phase gates, and what is verified versus
merely written. Nothing in `browser/` may change the Python engine's
behaviour.

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

## The radio stops answering after an interrupted scan — auto-recovery VERIFIED

**Reproduced and the recovery verified on hardware 2026-08-01.**

Symptom: stop a scan mid-chunk, leave it idle a few minutes, start another
— every chunk times out, the pass returns zero data, and the UI shows an
empty graph. It looks like a broken display; it is a radio that has stopped
answering.

Recovery, measured (web UI, WSUB1G, 470–542 MHz, 6 MHz chunks):

```
15:52:47  Pass 1 returned NO DATA — every chunk timed out.
15:52:47  Reopening the port (the only reliable recovery) and retrying…
15:52:49  Reconnect succeeded — Connected: eModel.MODEL_WSUB1G
15:52:59  Pass 2 1069 data points.
```

Dead to producing data in ~12s, unattended. `scan_worker` calls
`RFExplorerScanner.reconnect()` after any pass that returns nothing, retries
once, and gives up with an actionable status if the retry is also empty.

A reconnect resets `pass_num` to 0 so the retry runs as **pass 1** again.
That matters: `pass_number == 1` is the only branch that re-enables the
on-device average calculator, so without the reset the retry silently ran
on a differently configured radio than the scan started on. Confirmed by
comparing two runs — without the reset the log goes straight from
`Reconnect succeeded` to `Pass 2`; with it, `Reconnect succeeded` →
`Average calculator mode enabled.` → `Pass 1`.

What is known about the cause:
- A full `ClosePort` + `ConnectPort` + handshake revives it every time.
- **Flushing the serial buffer does not.** Measured twice, independently:
  after four idle minutes exactly **53 stale bytes** were waiting — the
  same number both runs. That is a single fixed truncated message, not an
  overflow, so the "buffer fills up" theory is wrong. `reset_stream()` is
  kept as cheap hygiene and as the diagnostic that produced the number.
- Idle time is a necessary ingredient. Stop-then-immediately-restart does
  not trigger it; ~4 and ~11 minute gaps do. Threshold unmeasured.
- Still unknown: whether a *clean* pass boundary also poisons it, and
  whether the Tk app behaves the same. It shares the engine, so probably —
  but the Tk app has no auto-recovery, so there the cure is still a manual
  Disconnect/Connect.

## Known state

- Two tests fail and have since the baseline commit: output bins land 50 kHz
  apart where the tests want 25 kHz (grid-snapping artifact). Exports remain
  WWB-valid — WWB needs *at least* 25 kHz. Unresolved on purpose.

  **There is an unmerged branch that resolves this** —
  `claude/vibrant-solomon-ba6505`, one commit, written 2026-08-01 16:05,
  before the browser work started. It concludes the *tests* were wrong: the
  gaps are not two native bins colliding in one grid slot but native bins
  being coarser than the grid, because `OVERLAP_MHZ` pads every chunk 1 MHz
  per side (a 2 MHz chunk is swept as 4 MHz over 112 points = 36 kHz/bin).
  Filling those slots would mean interpolating amplitudes the radio never
  measured, which on a coordination export reads as a clear channel. It
  relaxes both assertions to `0.025 <= gap <= 0.050` and adds the lower
  bound WWB actually requires, which nothing tested before.

  **Decide whether to merge it before doing more test work.** It touches
  `scanner.py`, both test files, and this file, so it will conflict with the
  browser-era edits here. Read its commit message first — the analysis is
  the valuable part, whatever is decided about the diff.

  It is already checked out as a worktree at
  `.claude/worktrees/vibrant-solomon-ba6505/`, so it can be read and its
  tests run without touching `main`. That copy predates the browser work, so
  its `README.md` and `AGENTS.md` are the older versions — don't read them
  as current.
