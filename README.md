# RF Scanner

A macOS desktop tool for RF spectrum scanning with the Seeed RF Explorer. Built for live sound engineers doing wireless frequency coordination.

Scans the UHF/sub-GHz band in narrow chunks for maximum sensitivity, accumulates multi-pass statistics, and exports CSV files compatible with Shure Wireless Workbench (WWB) and Soundbase.

## Features

- **Precision scanning** — sweeps in narrow chunks (default 6 MHz) to force low RBW and maximize noise floor depth
- **Multi-pass statistical aggregation** — runs continuously, accumulating amplitude distributions per frequency bin; export at any percentile (P20 default = conservative)
- **Live spectrum plot** — embedded matplotlib visualization with real-time chunk updates, max hold overlay, and previous pass comparison
- **Chunk boundary dithering** — randomizes chunk edges on passes 2+ so IF filter artifacts average out across passes (spatial dithering for RF)
- **Bandwidth-constant smoothing** — moving average in the linear power domain, with the window auto-scaled to hold ~160 kHz of smoothing however wide the chunks are (3 points at the default 6 MHz chunk, wider at smaller ones)
- **Manufacturer band multiselect** — pick Shure/Sennheiser bands by name (G57, H5, J8, A1, G…) and scan exactly that spectrum; overlapping bands merge so nothing is swept twice
- **Disjoint scanning** — scans a list of spans, not one range. Post-repack bands really are discontiguous (Shure J8 = 554–608 **+** 614–616 MHz), and the 608–614 carrier gap is never swept
- **25 kHz grid alignment** — all output snapped to wireless mic channel spacing
- **Live Mode** — separate real-time spectrum + waterfall display for monitoring
- **WWB/Soundbase CSV export** — headerless two-column format (freq MHz, amp dBm)

## Frontends

**Web UI (preferred)** — `python3 webapp.py`, then open
`http://localhost:8080`. Works from a phone, tablet or another laptop on the
LAN or over Tailscale, which is the point: you can drive a scan from across
the room. Same engine, same band picker, live plot, CSV download.

**Desktop UI** — `python3 rf_scanner.py`. The original Tk app. Still fully
working and still the home of Live Mode; kept as the fallback.

**Browser-owns-the-radio (in progress, not usable yet)** — `browser/`, a
port of the engine into JavaScript so a Chrome page can open the RF Explorer
directly over Web Serial, with no Python on the scanning machine. The engine,
statistics, CSV export and band table are ported and checked against the
Python; there is no UI yet and none of it has been driven against the
hardware. See `docs/PLAN_browser_serial.md`.

> The RF Explorer is a USB device, so **whichever UI you use, the process
> must run on the machine the radio is plugged into.** The web UI just moves
> the *screen*, not the radio — a remote server cannot scan.

### Running the web UI

```bash
python3 webapp.py                       # http://localhost:8080
PORT=9000 python3 webapp.py             # different port
PASSCODE=hunter2 python3 webapp.py      # shared passcode, for anything exposed
```

Find your address for other devices with `ipconfig getifaddr en0`, or use the
Mac's Tailscale name. Set a `PASSCODE` for anything reachable beyond a
trusted tailnet.

## Requirements

- macOS (tested on macOS 15 / Sequoia)
- Python 3.10+
- Seeed RF Explorer (WSUB1G or WSUB1G Plus) with USB cable
- [Silicon Labs CP210x VCP driver](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers)

## Installation

```bash
pip3 install pyserial RFExplorer numpy matplotlib
```

Or double-click `run.command` which auto-installs dependencies.

After installing the Silicon Labs driver, approve it in:
System Settings > General > Login Items & Extensions > Driver Extensions

## Usage

```bash
python3 rf_scanner.py
```

1. Select the RF Explorer port (`/dev/cu.usbserial-XXXX` or `/dev/cu.SLAB_USBtoUART`)
2. Click **Connect**
3. Set the spectrum to scan — either type a Start/End range, pick a preset, or
   click **Bands…** to check off manufacturer bands (defaults to showing only
   bands still usable in the US after the 600 MHz repack). Whichever you touch
   last wins. Then set chunk size and iterations per chunk
4. Click **Start Scan** — runs continuously until you hit **Stop**
5. Adjust export percentile and click **Save CSV**

### Device setup

On the RF Explorer device:
- **Menu > Config > USB Baud > 500K** (required for fast data transfer)
- Input stage: Attenuator Menu > Input (Direct / Attenuator / LNA)

### Scan settings guide

| Setting | Default | Effect |
|---------|---------|--------|
| Chunk size | 6 MHz | Smaller = lower RBW = deeper noise floor, but slower |
| Iterations | 1 | More = smoother within-pass data, but slower per chunk |
| Passes | continuous | More passes = better statistical picture; P20 export gets more conservative |
| Percentile | P20 | Lower = more conservative (only reports consistently quiet channels) |

## File structure

The engine imports no tkinter and no matplotlib — that is what lets several
frontends share it. Keep it that way.

```
rf_scanner/
├── scanner.py          # RFExplorerScanner — serial comms, scan logic, dithering, smoothing
├── stats.py            # ScanAccumulator — multi-pass statistical aggregation
├── export.py           # WWB CSV export with percentile selection
├── bands.py            # manufacturer band table + presets; owns all spectrum definitions
├── constants.py        # app title, baud rate, WWB step size
│
├── rf_scanner.py       # Tk entry point
├── ui.py               # RFScannerApp — main window, spectrum plot, continuous scan
├── live_mode.py        # LiveModeWindow — real-time spectrum + waterfall
│
├── webapp.py           # Flask server for the web UI
├── web/                # its templates + vanilla-JS frontend (no build step)
│
├── browser/            # WIP: the engine in JS, radio opened via Web Serial
│   ├── rfe.js          #   wire protocol
│   ├── sweep.js        #   scan_pass/_scan_chunk port
│   ├── stats.js        #   stats.py port
│   ├── export.js       #   export.py port + client-side download
│   ├── bands.js        #   bands.py transcription
│   └── spike.html      #   bench harness (no UI yet)
│
├── AGENTS.md           # how this project is built and what not to build
├── docs/               # design decisions — read before the code they describe
├── PROJECT_NOTES.md    # detailed dev notes, hardware findings, feature backlog
├── requirements.txt    # dependencies
├── run.command         # macOS double-click launcher
└── tests/              # 50 tests (pytest)
    ├── mock_rfexplorer.py
    ├── conftest.py
    ├── test_stats.py
    ├── test_export.py
    ├── test_scanner.py
    └── test_integration.py
```

## Testing

```bash
python3 -m pytest tests/ -v    # 50 tests
```

Tests use a mock RF Explorer device that simulates realistic noise floors, interference sources, stale sweep rejection, and timeouts — no hardware needed.

**48 pass; 2 fail, and have since the first commit.** Both assert that output
bins land 25 kHz apart where grid-snapping actually produces 50 kHz in
places. Exports stay WWB-valid — WWB requires *at least* 25 kHz — so this is
left unresolved on purpose rather than papered over. See AGENTS.md.

## Known issues

- **Amplitude offset (~10 dB)**: Device display may read ~10 dB higher than software output in quiet spectrum. Still unexplained, but one hypothesis is now ruled out: on 2026-08-01 the WSUB1G reported `OffsetDB=0` and `CfgOffset=0` at connect, so the RF Explorer library is *not* silently applying an offset to readings on this unit. Diagnostic logging remains in place at connect time.
- **macOS port list**: `/dev/cu.debug-console` may appear in the port dropdown — this is a macOS system port, not the RF Explorer. Select the `usbserial` or `SLAB` port instead.
- **Python 3.10 + macOS 15**: Color emoji and certain Unicode characters crash Tk. All UI text uses plain ASCII.
