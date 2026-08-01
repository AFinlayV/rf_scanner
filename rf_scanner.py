#!/usr/bin/env python3
"""
RF Scanner — entry point.

A macOS tool for RF spectrum scanning using a Seeed RF Explorer
sub-GHz analyzer. Exports scan data for use in coordination software
like Shure Wireless Workbench (WWB).

Usage:
    python3 rf_scanner.py                      # launch GUI
    python3 rf_scanner.py --start 470 --end 650  # CLI scan with defaults
    python3 rf_scanner.py --help               # show all CLI options

See PROJECT_NOTES.md for architecture and feature documentation.

v2.0 — Continuous multi-pass scanning with statistical aggregation.
       Export uses configurable percentile (default P20) across all passes
       for an honest picture of the RF environment.
"""

import argparse
import sys


def main_gui():
    """Launch the GUI."""
    import tkinter as tk

    root = tk.Tk()

    # Workaround: Python 3.10's Tk on macOS 15 can crash in IconServices
    # when macOS tries to render the Python.app bundle icon (corrupted PNG
    # in the .icns file). Set a blank icon to prevent the system from
    # attempting to load the default app icon.
    try:
        blank = tk.PhotoImage(width=1, height=1)
        root.iconphoto(True, blank)
    except Exception:
        pass

    # On macOS, Tkinter loses the macOS application-active state after each
    # button click, so subsequent clicks need a title-bar click to re-activate.
    # Fix: re-activate via NSApplication after every ButtonRelease.
    # Three-tier fallback: PyObjC → temporary topmost → focus_force.
    try:
        from AppKit import NSApplication as _NSApp
        _ns_app = _NSApp.sharedApplication()
        def _macos_activate():
            _ns_app.activateIgnoringOtherApps_(True)
            root.lift()
    except Exception:
        # PyObjC unavailable — use temporary topmost flag.
        # Briefly floating the window to the top forces macOS to treat it
        # as the active window; we drop the flag after 200 ms so it doesn't
        # permanently float above other apps.
        def _macos_activate():
            try:
                root.attributes('-topmost', True)
                root.lift()
                root.focus_force()
                root.after(200, lambda: root.attributes('-topmost', False))
            except Exception:
                root.focus_force()

    root.lift()
    root.after(100, _macos_activate)
    root.bind_all("<ButtonRelease-1>", lambda e: _macos_activate())

    from ui import RFScannerApp
    app = RFScannerApp(root)  # noqa: F841
    root.mainloop()


def main_cli(args):
    """Run a headless scan from the command line."""
    import threading
    import queue
    import time
    import os

    from scanner import RFExplorerScanner, MISSING_DEPS, list_serial_ports
    from stats import ScanAccumulator
    from export import save_wwb_csv, default_filename

    if MISSING_DEPS:
        print(f"Missing packages: {', '.join(MISSING_DEPS)}")
        print(f"Run:  pip3 install {' '.join(MISSING_DEPS)}")
        sys.exit(1)

    # Auto-detect port if not specified
    port = args.port
    if not port:
        ports = list_serial_ports()
        if not ports:
            print("No serial ports found. Is the RF Explorer connected?")
            sys.exit(1)
        port = ports[0]
        print(f"Auto-detected port: {port}")

    # Connect
    print(f"Connecting to {port}...")
    scanner = RFExplorerScanner(port, args.baud)
    ok, msg = scanner.connect()
    if not ok:
        print(f"Connection failed: {msg}")
        sys.exit(1)
    print(msg)

    # Scan
    accumulator = ScanAccumulator()
    stop_event = threading.Event()
    msg_q = queue.Queue()

    print(f"Scanning {args.start}-{args.end} MHz | "
          f"{args.chunk} MHz chunks | {args.iterations} iter/chunk | "
          f"{args.passes} pass{'es' if args.passes != 1 else ''}")
    print()

    try:
        for pass_num in range(1, args.passes + 1):
            t0 = time.time()
            data = scanner.scan_pass(
                args.start, args.end, args.chunk, args.iterations,
                stop_event, msg_q, pass_num
            )
            elapsed = time.time() - t0

            if data:
                accumulator.add_pass(data)
                print(f"  Pass {pass_num}/{args.passes}: "
                      f"{len(data)} points in {elapsed:.1f}s")
            else:
                print(f"  Pass {pass_num}/{args.passes}: no data")

            # Drain the message queue (print logs if verbose)
            while not msg_q.empty():
                m = msg_q.get_nowait()
                if args.verbose and m.get('type') == 'log':
                    print(f"    {m['text']}")

    except KeyboardInterrupt:
        print("\nScan interrupted.")
        stop_event.set()

    # Disconnect
    scanner.disconnect()

    if accumulator.pass_count == 0:
        print("No scan data collected.")
        sys.exit(1)

    # Export
    if args.percentile is not None:
        export_data = accumulator.export_percentile(args.percentile)
        label = f"P{args.percentile}"
    else:
        export_data = accumulator.export_max()
        label = "MAX"

    out_dir = args.output_dir or os.path.expanduser("~/Desktop")
    filename = default_filename(
        start_mhz=args.start,
        end_mhz=args.end,
        passes=accumulator.pass_count,
        label=label,
    )
    path = os.path.join(out_dir, filename)

    n = save_wwb_csv(path, export_data)
    print(f"\nSaved {n} points ({label} across "
          f"{accumulator.pass_count} passes)")
    print(f"File: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="RF Scanner — spectrum scanning with Seeed RF Explorer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python3 rf_scanner.py                           # launch GUI
  python3 rf_scanner.py --start 470 --end 650     # quick CLI scan
  python3 rf_scanner.py -s 500 -e 600 -p 5 -P 10 # 5 passes, P10 export
  python3 rf_scanner.py -s 470 -e 900 -p 10 -v   # full band, verbose
""",
    )
    parser.add_argument("--gui", action="store_true", default=False,
                        help="force GUI mode (default if no scan args given)")
    parser.add_argument("-s", "--start", type=float, default=None,
                        help="start frequency in MHz (default: 470)")
    parser.add_argument("-e", "--end", type=float, default=None,
                        help="end frequency in MHz (default: 900)")
    parser.add_argument("-c", "--chunk", type=float, default=6.0,
                        help="chunk size in MHz (default: 6.0)")
    parser.add_argument("-i", "--iterations", type=int, default=1,
                        help="iterations per chunk (default: 1)")
    parser.add_argument("-p", "--passes", type=int, default=3,
                        help="number of scan passes (default: 3)")
    parser.add_argument("-P", "--percentile", type=int, default=None,
                        help="export percentile 1-99 (default: max-hold)")
    parser.add_argument("--max-hold", action="store_true", default=True,
                        help="export max-hold across passes (default)")
    parser.add_argument("--port", type=str, default=None,
                        help="serial port (auto-detected if omitted)")
    parser.add_argument("--baud", type=int, default=500000,
                        help="baud rate (default: 500000)")
    parser.add_argument("-o", "--output-dir", type=str, default=None,
                        help="output directory (default: ~/Desktop)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print detailed scan logs")

    args = parser.parse_args()

    # If no scan parameters given, launch GUI
    if args.gui or (args.start is None and args.end is None):
        main_gui()
    else:
        # Fill in defaults for partial CLI args
        if args.start is None:
            args.start = 470.0
        if args.end is None:
            args.end = 900.0
        main_cli(args)


if __name__ == "__main__":
    main()
