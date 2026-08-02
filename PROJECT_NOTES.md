# RF Scanner — Project Notes

> **This is a historical log, not the current state of the project.** It is
> kept because the hardware findings, the design rationale and the band
> reference below are still worth having — but sections are dated from
> 2026-03 onward and were written when this was a Tk-only app.
>
> **Start with `AGENTS.md`** for what the project is now and what not to
> build, then `docs/` for the design decisions behind anything in flight.
> Where this file and those disagree, they win.
>
> Chiefly out of date here: this is no longer "a macOS desktop tool" — there
> are three frontends (Tk, the Flask web UI, and the Web Serial browser
> build), and the engine deliberately imports no tkinter and no matplotlib
> so they can share it.

## What this project is

A macOS desktop tool (Python + tkinter) that:
1. Connects to a **Seeed RF Explorer sub-GHz** spectrum analyzer over USB
2. Runs a **precision scan** of the UHF/sub-GHz band with the lowest achievable noise floor
3. Exports a **Shure Wireless Workbench (WWB) compatible CSV** for frequency coordination
4. Optionally opens a **Live Mode** window with a real-time spectrum line + scrolling waterfall display

The user is a live sound engineer doing RF coordination (wireless mics, IEMs).

---

## Files

*See the README for the current tree — this section listed only the Tk app
and went stale as the other frontends landed. What matters structurally:*

```
scanner.py stats.py export.py bands.py constants.py   engine (no UI imports)
rf_scanner.py ui.py live_mode.py                      Tk frontend
webapp.py web/                                        Flask web frontend
browser/                                              Web Serial frontend (JS)
tests/                                                50 tests
```

`bands.py` owns every spectrum definition and took the frequency presets
that this document elsewhere still attributes to `constants.py`.

Run with:  `python3 rf_scanner.py`  or double-click `run.command`
Web UI:    `python3 webapp.py`  →  http://localhost:8080
Tests:   `python3 -m pytest tests/ -v`  (50 tests, ~23s — 48 pass, 2 have
failed since the first commit; see AGENTS.md "Known state")

---

## Hardware

| Item | Detail |
|---|---|
| Device | Seeed RF Explorer WSUB1G or WSUB1G Plus |
| USB chip | Silicon Labs CP2102 → VCP driver required |
| macOS port | `/dev/cu.usbserial-XXXX` (newer driver) or `/dev/cu.SLAB_USBtoUART` (older driver) |
| Baud rate | **500 000 bps** — must be set on device: Menu → Config → USB Baud → 500K |
| Driver | Silicon Labs CP210x v6.0.2 from silabs.com → approve in System Settings → General → Login Items & Extensions → Driver Extensions |

The WSUB1G Plus has an LNA that can push the noise floor to ~-125 dBm.  The original WSUB1G goes to ~-110 dBm.

---

## Key design decisions

### Continuous multi-pass scanning with statistical aggregation (v2.0)
Instead of a single scan pass, the scanner runs continuously — each pass
sweeps the full range and feeds data into a `ScanAccumulator`.  Each frequency
bin accumulates a distribution of amplitude readings across passes.  At export
time, the user selects a percentile (default P20) which determines how
conservative the output is.  Lower percentile = more conservative = only
reports channels that are consistently quiet.

### Lowest noise floor via narrow chunks
Wide sweeps → device auto-selects wide RBW → high noise floor.
Sweeping in **2 MHz chunks** forces a ~18 kHz RBW → noise floor drops ~30 dB.
Each chunk runs with on-device **Average calculator mode** + N iterations
(default 5/chunk) for within-pass noise reduction.  Multi-pass statistical
aggregation adds the temporal dimension on top.

### 25 kHz grid alignment
Wireless mic/IEM channels step in 25 kHz increments.  Before building the chunk list, start/end/chunk are snapped to the 25 kHz grid (floor/ceil respectively).  The final output is resampled to 25 kHz bins.  This ensures every output data point corresponds to a real device channel slot, and chunk seams never split a channel.

### Coordination software agnostic export
The app should not be coupled to any single coordination platform.  Current export is a headerless two-column CSV (`freq,amp_dBm`) which is compatible with:
- **Shure WWB** — freq in MHz, ≥ 25 kHz step.  Import: Scan Data → Add Scan From File.
- **Soundbase** — same CSV, auto-detects MHz or Hz.  Also merges multi-file uploads.
- **Sennheiser WSM** — needs a header row + different column layout (future).

When more platforms are added, export should be handled through a pluggable exporter pattern (e.g. `exporters/wwb.py`, `exporters/soundbase.py`, `exporters/wsm.py`) rather than hardcoding format logic into the UI.  The `ScanAccumulator` already outputs a generic `list[(freq, amp)]` — each exporter just formats that differently.

Current format (works for both WWB and Soundbase today):
```
470.000,-109.0
470.025,-107.5
```
Export uses the Nth percentile from the accumulated distribution (not raw average).

### Thread safety
All serial I/O is in daemon threads.  UI updates go through a `queue.Queue` drained by `root.after(50, _pump_queue)` on the main thread.  Never call tkinter widgets from the scan threads.

### Live Mode mutual exclusion
Opening the Live Mode window disables the Scan button (both fight for the same serial device).  Buttons re-enable when the Live window closes.

---

## Class / module structure

```
constants.py               # APP_TITLE, APP_VERSION, BAUD_RATE, FREQ_PRESETS, WWB_MIN_STEP_MHZ

scanner.py
  list_serial_ports()      # SLAB ports first
  RFExplorerScanner        # serial comms + single-pass scan logic
    .connect()             # returns (bool, message); logs device diagnostics
    .disconnect()
    .rfe                   # property — access underlying RFE communicator
    .scan_pass(start, end, chunk, iters, stop_event, queue, pass_number)
        → list[(freq_mhz, amp_dbm)]   # one full pass, resampled + smoothed to 25 kHz
    ._scan_chunk(...)      # one chunk, N averaged sweeps, edge-trimmed
    ._resample(points, step)  # bin to 25 kHz grid, linear-power averaging
    ._smooth(points, window)  # moving-average in linear power domain

stats.py
  BinStats                 # dataclass — per-bin statistics
  ScanAccumulator          # multi-pass statistical aggregation
    .add_pass(data)        # feed one pass of (freq, amp) data
    .pass_count / .bin_count / .frequencies
    .export_percentile(N)  # → list[(freq, amp)] using Nth percentile
    .export_max()          # max-hold (worst case)
    .export_min()          # min-hold (best case)
    .get_bin_stats(freq)   # full stats for one bin
    .clear()

export.py
  save_wwb_csv(path, data) # write headerless CSV
  default_filename()       # timestamped filename

ui.py
  RFScannerApp             # main window — continuous scan + percentile export
    ._build_ui()           # connection, params, controls, results (w/ percentile), plot, log
    ._build_plot()         # embedded matplotlib spectrum plot (dark theme)
    ._style_ax()           # apply dark theme to axes (DRY helper)
    ._auto_ylim(*lists)    # set Y axis from amplitude data with padding
    ._update_plot_chunk()  # add chunk data to live plot (3 layers)
    ._update_plot_pass_complete()  # update max hold + redraw after pass
    ._clear_plot()         # full reset (incl. max hold) for new scan
    ._clear_plot_scan_data()  # clear in-progress chunks between passes
    ._start_scan()         # launches background thread with infinite pass loop
    ._stop_scan()          # sets stop_event, thread exits after current chunk
    ._save_csv()           # export using accumulator.export_percentile(pct_var)
    ._open_live_mode()     # lazy-imports LiveModeWindow
    ._pump_queue()         # 50ms drain — handles pass_complete, chunk_vis, etc.
    ._update_noise_floor() # estimated noise floor label (RBW + pass count)

live_mode.py
  LiveModeWindow(Toplevel) # floating spectrum + waterfall window
    ._build_controls()     # dark toolbar: start/stop, range, dBm scale, peak hold
    ._build_plots()        # matplotlib Figure embedded via FigureCanvasTkAgg
    ._live_loop()          # background thread: raw sweeps, no averaging
    ._handle_sweep(msg)    # updates spectrum line + waterfall imshow
    ._pump_queue()         # 33ms drain loop (~30 fps)
```

---

## Hardware session notes (2026-03-21)

Hardware has been connected and tested successfully. Key findings:

### macOS port naming
The Silicon Labs CP210x driver on newer macOS versions uses `/dev/cu.usbserial-XXXX` instead of the older `/dev/cu.SLAB_USBtoUART`. The `list_serial_ports()` function now matches both patterns. The library's `GetConnectedPorts()` only accepts `SLAB_USB` in the port name — we work around this by injecting the port directly into `rfe.m_arrValidCP2102Ports`.

### RFExplorer Python library v1.33.2106.3 API notes
Several API names differ from what was assumed during development:
- `PortConnected` (not `IsPortConnected`) — property
- `MainBoardModel` (not `MainBoard`) — property
- `eCalculator.AVG` (not `.Average`) — value=2
- No `SetCalculator()` method — use `SendCommand("C+" + chr(mode_value))`
- `AutoConfigure` must be `True` for the library to request device config after connection (so `MainBoardModel` gets populated)
- `RFECommunicator()` constructor starts a `ReceiveSerialThread` immediately

### Python 3.10 + macOS 15 crash bugs
Two distinct crash vectors:
1. **Color emoji in Tk labels**: Python 3.10's bundled Tk crashes in `CopyEmojiImage` → `CTFontDrawGlyphsWithAdvancesInternal` when rendering color emoji (📺, 💾) or emoji-adjacent Unicode (▶ U+25B6, ⬛ U+2B1B) in button labels. Fixed by using plain text labels.
2. **App bundle icon**: macOS 15's `IconServices` crashes loading Python.app's `.icns` file (corrupted PNG). Fixed by setting a blank 1×1 `PhotoImage` as the window icon before any widgets are created.

### Remaining debug points
1. **`UpdateDeviceConfig` span limits**: WSUB1G (not Plus) max span ~100 MHz. Live Mode's 430 MHz span (470-900) may be rejected.
2. **Settle time**: Currently 0.35s. If stale sweeps slip through, increase to 0.5s.
3. **`SweepData.CleanAll()`**: Works on v1.33 — confirmed functional.
4. **`TotalDataPoints` / `StartFrequencyMHZ`**: Both are properties (not methods) on v1.33. The `callable()` guard handles this correctly.
5. **Calculator mode echo parse warning**: `ProcessReceivedString: invalid literal for int() with base 10: '\x02'` — harmless. The library echoes back our `"C+\x02"` average-mode command and tries to parse the `\x02` byte as a decimal integer. The command works, just the echo parse fails. Fix: suppress by intercepting the echo before it hits the library's parser, or silence the warning output.

---

## Session notes (2026-03-22) — Visualization, data quality, amplitude investigation

### Live spectrum plot — completed
Wired up the embedded matplotlib spectrum plot with live updating during scans:
- `chunk_vis` messages from scanner thread update the plot in real time as each chunk completes
- Three-layer rendering: dim previous pass (background), orange max hold line, bright cyan current pass
- `_style_ax()` helper consolidates dark theme styling
- `_auto_ylim()` sets Y axis from data range with padding
- Plot clears on new scan start, chunk data clears between passes, max hold persists across passes
- Window minsize increased to 660x880 to accommodate the plot

### Data quality improvements — completed
Several issues were identified and fixed to improve scan data quality:

1. **Edge trimming increased** (5 → 10 bins): The IF filter rolloff at the edges of each sweep causes amplitude errors. Trimming 10 bins from each side (was 5) removes more of the distorted data at chunk boundaries.

2. **Overlap increased** (0.1 → 0.25 MHz): Each chunk is padded by 0.25 MHz on each side (was 0.1) so that after edge trimming, there's still overlap between adjacent chunks for smooth blending.

3. **3-point moving average smoothing**: New `_smooth()` static method applies a symmetric moving-average filter in linear power domain. Reduces noise spikiness without smearing narrow signals. Edge points keep original values.

4. **Chunk boundary dithering** (spatial dithering for RF): On passes 2+, chunk width is randomized ±15% so that IF filter rolloff artifacts land at different frequencies each pass and average out across the accumulator. Pass 1 stays fixed so the time estimate is accurate. User recognized this as analogous to audio dithering — "to avoid quantization error in the effects of the antialiasing filter."

### Amplitude offset investigation — UNRESOLVED
Device display reads ~-98 to -100 dBm in quiet spectrum, but software outputs ~-110 to -112 dBm (~10-12 dB gap).

**Root cause analysis so far:**
- The RF Explorer Python library silently applies `fOffset_dB + nInputStageOffset` to ALL amplitude readings at storage time in `ProcessReceivedString` (in `ReceiveSerialThread.py`)
- `GetAmplitude_DBM()` returns pre-adjusted values — the offset is already baked in
- Input stage offset array in `RFExplorer.py`: `[0.0, 30.0, -25.0, 60.0]` → Direct=0, Attenuator=+30, LNA=-25
- `fOffset_dB` is parsed from device config string `#C2-F:` at position 169 in `RFEConfiguration.py`
- Diagnostic logging added at connect time reports: `InputStage`, `OffsetDB`, `CfgOffset`, `CalcMode`
- First-chunk diagnostic logs raw min/max/sample values
- **Observed values (2026-03-24)**: `InputStage=eInputStage.Direct | OffsetDB=0.0 | CfgOffset=0.0 | CalcMode=eCalculator.NORMAL` — all zero offsets, so the library isn't adding any offset. The discrepancy source remains unknown.
- **Re-confirmed (2026-08-01)**, same WSUB1G on `/dev/cu.usbserial-210`, identical reading: `InputStage=eInputStage.Direct | OffsetDB=0 | CfgOffset=0 | CalcMode=eCalculator.NORMAL`. Five months apart, so this is a stable property of the unit and not a transient. **Stop re-measuring it** — the library offset is not the cause and a third reading will not say otherwise. If this is picked up again, the untested suspects are the antenna/front-end path and what the device's own display does to the number before showing it, neither of which is visible from the serial stream.

**LNA investigation:**
- Attempted sending `a2` (LNA enable) via serial — caused device timeouts, had to power cycle
- User suspects the internal LNA may not exist on their specific WSUB1G Plus unit
- If library thought LNA was active but hardware didn't have it, readings would be 25 dB too low — but diagnostics show InputStage=Direct, so this isn't the cause
- RF Explorer menu structure for input stage: Attenuator Menu → Input → Direct/Attenuator/LNA (not a separate menu as initially guessed)
- Serial commands: `a0`=Direct, `a1`=Attenuator, `a2`=LNA

**CSV data analysis (470-500 MHz, 5 passes, 1 MHz chunks, 10 iterations):**
- Clear noise floor at -112.3 to -112.8 dBm in 476-490 MHz quiet region
- Floor rises to ~-111.4 dBm in 492-500 MHz range (~1.1 dB step at 491 MHz)
- Step identified as likely device hardware characteristic (internal calibration table boundary)
- Baofeng test transmissions clearly visible at 483.7, 484.5, 490.35 MHz

### Port selection note
The macOS serial port list may include `/dev/cu.debug-console` — this is a macOS system debug port, NOT the RF Explorer. Selecting it will show `MODEL_NONE` and all chunks will timeout. The RF Explorer will appear as `/dev/cu.usbserial-XXXX` or `/dev/cu.SLAB_USBtoUART`.

---

## Possible next features (user has mentioned these)

- ~~**Multi-pass statistical aggregation**~~ — **IMPLEMENTED in v2.0**.  Continuous scanning with `ScanAccumulator`, configurable percentile export (default P20), min/max/std available in `stats.py`.
- ~~**Estimated noise floor display**~~ — **IMPLEMENTED**.  Shows estimated noise floor in Results section based on chunk size (RBW) and pass count.
- ~~**Live spectrum visualization**~~ — **IMPLEMENTED**.  Embedded matplotlib plot with real-time chunk updates, max hold, previous pass overlay, auto-scaling Y axis.
- **RF Explorer device settings in GUI**: Input stage selector (Direct/Attenuator/LNA), amplitude offset control, calculator mode display. Currently these must be set on the device manually.
- **Max hold reset button**: Currently max hold only clears when starting a new scan.
- **Chunk dithering toggle**: Currently always on for passes 2+. Should be a checkbox.
- **Smoothing control**: Currently fixed at 3-point window. Could be adjustable or toggleable.
- **Known interference source tagging**: ability to mark frequency ranges as known sources (TV broadcast channels, airport/AWOS, public safety, DECT, etc.) with a label.  Tagged ranges are visually flagged in the spectrum view and annotated in exports — helps distinguish "this is a TV tower, ignore it" from "this is unknown RF we need to work around."
- **Frequency markers / threshold line** on the live spectrum
- **Auto-save** scan on completion
- **Zoom** in live mode to a sub-band
- **GPS / location metadata**: integrate macOS location services to attach GPS coordinates to each scan.  Store lat/lon with the RF data so scans are geographically tagged.
- **RF gear library + band-selective sweep**: maintain a database of manufacturers (Shure, Sennheiser, Wisycom, etc.), models, and their tuning bands/frequency ranges.  User selects one or more specific models/bands; the app builds a sweep list covering only those frequency ranges (skipping everything else) and runs a continuous scan — useful at festivals where you only care about the spectrum your gear actually operates in.
- **Scan database with show metadata**: persist all scans (RF data + metadata) to a local database.  Metadata fields: venue name, show/event name, vendor/client, GPS coordinates, zip code, date/time, gear selection.  Enables historical comparison across shows and venues.
- **Intermodulation (IM3) calculator**: given a planned frequency list, compute third-order intermod products and flag conflicts against scan data.  Essential for multi-system coordination.
- **FCC database lookup by zip/GPS**: pull licensed TV stations, Part 74 wireless mic licenses, and other allocations for the current location.  Auto-populates known interference source tags.  **Must support offline use** — internet is unreliable on site.  Pre-download strategy: user enters zip code or drops a pin at home/office before the gig; app fetches and caches all relevant FCC records locally (SQLite).  On site, all lookups hit the local cache.  Cache should be human-readable enough to manually inspect/update if needed.  FCC data sources: ULS (Universal Licensing System) bulk data downloads + CDBS TV station database — both are public, updated weekly, and can be filtered to a radius around a location.
- **Live interference alerts**: during a show, continuously scan and notify (visual + audio) if a new signal appears above a configurable threshold.  Catches walk-in interference mid-performance.
- **Frequency assignment tracker**: simple table mapping frequency → transmitter → performer/channel.  Stored with the show in the database.
- **Preset / template system**: save and load common rig configurations (e.g. "4 handhelds + 2 IEM packs") to quickly pre-populate the gear selector.
- **Pluggable exporters**: refactor export into a plugin pattern (`exporters/wwb.py`, `exporters/soundbase.py`, `exporters/wsm.py`, etc.) so the user picks their coordination software from a dropdown and the app writes the correct format.  Sennheiser WSM uses a different CSV layout with headers; other platforms may need XML or JSON.  The `ScanAccumulator` already outputs generic data — each exporter is just a formatter.

---

## Dependencies

| Package | Purpose | Required? |
|---|---|---|
| `pyserial` | Serial port I/O | Yes |
| `RFExplorer` | Official RF Explorer Python library (wraps pyserial) | Yes |
| `numpy` | Statistical aggregation engine + Live Mode | Yes |
| `matplotlib` | Spectrum + waterfall plots (Live Mode only) | Live Mode only |

Install: `pip3 install pyserial RFExplorer numpy matplotlib`

---

## Testing

### Run tests

```bash
cd rf_coordinator
python3 -m pytest tests/ -v          # all 50 tests
python3 -m pytest tests/test_stats.py -v    # just stats (16 tests)
python3 -m pytest tests/test_scanner.py -v  # just scanner (15 tests)
```

### Mock hardware

Tests use a mock RF Explorer device (`tests/mock_rfexplorer.py`) that simulates:
- Realistic noise floor (~-110 dBm, 2 dB sigma, deterministic seed)
- Configurable interference sources (center freq, bandwidth, power, presence probability)
- Stale sweep rejection (sweeps at wrong frequency after config change)
- Timeout simulation (non-responsive chunks)
- Both property and method forms of `TotalDataPoints` / `StartFrequencyMHZ`

The mock is injected via `sys.modules` patching so `scanner.py` imports it as if it were the real `RFExplorer` library.  `time.sleep` is patched to no-op for settle times (keeps the suite at ~26s instead of ~20min).

### Test coverage summary

| Suite | Tests | Validates |
|---|---|---|
| `test_stats` | 16 | add_pass, partial passes, percentile export (P20/P50/P90), min/max, BinStats, clear, cache invalidation |
| `test_export` | 8 | CSV format, freq/amp precision, 25 kHz step compliance, empty data, filenames |
| `test_scanner` | 15 | connect/disconnect, chunk scan, stale rejection, timeout, config error, grid alignment, resampling, calculator mode |
| `test_integration` | 7 | 3-pass continuous loop, mid-pass stop, full scan-to-CSV pipeline, interference detection, narrow/wide range, callable guard |

---

## Gear Selector — Frequency Band Reference

For the future gear selector UI: user picks manufacturer → product family → band,
and the app builds a sweep covering only those frequency ranges. All data below is
for **US-legal (FCC Part 74 / Part 15)** operation only.

**Important**: The 600 MHz incentive auction reallocated 617–652 MHz and 663–698 MHz
to mobile carriers. Operation of wireless audio equipment in these ranges is
**prohibited** in the US as of July 13, 2020. Some older band designations (e.g.,
Shure J50) that included these ranges are discontinued or replaced with
auction-compliant versions (J50A). The gear selector should flag or exclude
any band that overlaps the 617–698 MHz range.

### Shure

#### Axient Digital (AD series — flagship digital wireless)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G53  | 470.000–510.000 | |
| G57  | 470.000–616.000 | Wide tuning |
| H54  | 518.000–578.000 | Popular US band |
| K54  | 602.000–614.000 | Narrow; below auction block |
| X55  | 941.500–960.000 | 900 MHz band |

#### ULX-D (digital wireless, mid-tier)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| V50  | 174.000–216.000 | VHF |
| G50  | 470.000–534.000 | |
| H50  | 534.000–598.000 | |
| J50A | 572.000–616.000 | Auction-compliant replacement for J50 |
| L50A | 632.000–696.000 | Post-auction compliant |
| X52  | 902.000–928.000 | 900 MHz ISM |

#### QLX-D (digital wireless, entry-pro)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G50  | 470.000–534.000 | |
| H50  | 534.000–598.000 | |
| J50A | 572.000–616.000 | |

#### SLX-D (digital wireless, entry-level pro)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G58  | 470.000–514.000 | |
| H55  | 514.000–558.000 | |
| J52  | 558.000–602.000 | |

#### SLX-D+ (newer entry-level digital)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G57  | 470.000–616.000 | Wide tuning |

#### BLX (analog wireless, budget)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| H9   | 512.000–542.000 | |
| H10  | 542.000–572.000 | |
| H11  | 572.000–596.000 | |
| J11  | 596.000–616.000 | |
| K14  | 614.000–638.000 | Partly in auction block — check compliance |

#### PSM 1000 (in-ear monitors, flagship)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G10  | 470.000–542.000 | |
| H22  | 518.000–602.000 | |
| J8A  | 554.000–616.000 | Auction-compliant |
| X1   | 944.000–952.000 | 900 MHz |

#### PSM 900 (in-ear monitors, mid-tier)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G6   | 470.000–506.000 | |
| G7   | 506.000–542.000 | |
| K1   | 596.000–632.000 | Overlaps auction block — check compliance |
| X1   | 944.000–952.000 | 900 MHz |

#### PSM 300 (in-ear monitors, entry-level)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| G20  | 488.000–512.000 | |
| H20  | 518.000–542.000 | |
| J13  | 566.000–590.000 | |
| K12  | 614.000–638.000 | Partly in auction block — check compliance |

### Sennheiser

#### ew 100 G4 (analog wireless, workhorse)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| A1   | 470.000–516.000 | |
| A    | 516.000–558.000 | |
| AS   | 520.000–558.000 | |
| G    | 558.000–608.000 | |

#### ew 300/500 G4 (analog wireless, pro)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| Aw+  | 470.000–558.000 | Wide tuning |
| AS   | 520.000–558.000 | |
| Gw1  | 558.000–608.000 | |

#### ew IEM G4 (in-ear monitors, analog)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| A1   | 470.000–516.000 | |
| A    | 516.000–558.000 | |
| AS   | 520.000–558.000 | |
| G    | 558.000–608.000 | |

#### EW-D (digital wireless, mid-tier)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| Q1-6 | 470.200–526.000 | |
| R1-6 | 520.200–576.000 | |
| R4-9 | 552.000–607.800 | |

#### EW-DX (digital wireless, flagship)
| Band  | Range (MHz) | Notes |
|-------|-------------|-------|
| Q1-9  | 470.200–607.800 | Wide tuning |
| R1-9  | 520.200–607.800 | Wide tuning |

#### Digital 6000 (premium digital wireless)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| A1-A4 | 470.000–558.000 | Exact sub-band splits vary |

#### Digital 9000 (flagship digital wireless)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| A1-A4 | 470.000–558.000 | Exact sub-band splits vary |

#### XSW 1 / XSW 2 (entry-level wireless)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| A    | 548.000–572.000 | |

#### XSW IEM (entry-level in-ear monitors)
| Band | Range (MHz) | Notes |
|------|-------------|-------|
| A    | 476.000–500.000 | |
| B    | 572.000–596.000 | |

#### Spectera (next-gen IP-native wireless — announced 2025)
| Band  | Range (MHz) | Notes |
|-------|-------------|-------|
| UHF   | 470.000–694.000 | Wide tuning; respects auction block internally |
| 1G4   | 1350.000–1400.000 | 1.4 GHz band (outside RF Explorer sub-GHz range) |

### Implementation notes for gear selector

- Data structure: nested dict `GEAR_BANDS[manufacturer][family][band] = (start_mhz, end_mhz)`
- UI flow: Manufacturer dropdown → Family dropdown (filtered) → Band checkboxes (multi-select)
- Multi-band selection: union of all selected ranges → build chunk list covering only those ranges
- Auction block warning: if any selected band overlaps 617–698 MHz, show a warning
- 900 MHz / VHF bands: include but note they require different antenna setups
- Sennheiser Spectera 1G4 band (1.35–1.4 GHz): outside RF Explorer sub-GHz range, grey out or hide
- Consider a "Custom range" option for unlisted gear or future products
- Band data should live in `constants.py` as a static dict (no need for a database at this stage)
