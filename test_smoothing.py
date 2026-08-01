#!/usr/bin/env python3
"""
Smoothing window optimisation test.

Runs a real scan collecting raw (window=1) data across multiple passes,
then applies every test window size to the same raw data and reports:
  - Mean adjacent amplitude diff  (lower = smoother)
  - Noise floor stddev            (lower = steadier noise floor)
  - TV station peak levels        (should stay high — signal preservation)
  - Detection rate                (% of known stations above threshold)

Usage:
  python3 test_smoothing.py
  python3 test_smoothing.py --start 554 --end 600 --passes 5
"""

import argparse
import math
import statistics
import threading
import queue
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scanner import RFExplorerScanner, list_serial_ports, RFExplorerScanner

# SF Bay Area UHF TV stations (post-repack)
SF_STATIONS = [
    ('KBCW',      28, 554.0, 560.0),
    ('KPIX',      29, 560.0, 566.0),
    ('KQED',      30, 566.0, 572.0),
    ('KTVU',      31, 572.0, 578.0),
    ('KCNS',      32, 578.0, 584.0),
    ('KDTV/KTSF', 20, 506.0, 512.0),
    ('KCNZ',      21, 512.0, 518.0),
    ('KFSF',      34, 590.0, 596.0),
]

DETECT_THRESHOLD = -95.0   # dBm — anything above this counts as detected
NOISE_BAND = (520.0, 540.0)  # a mostly-quiet region for noise floor stats
TEST_WINDOWS = [1, 3, 5, 7, 9, 11, 15]


def metrics(data, scan_start, scan_end):
    """Compute smoothness and signal metrics for a (freq, amp) dataset."""
    if not data:
        return {}

    amps = [a for _, a in data]
    diffs = [abs(data[i+1][1] - data[i][1]) for i in range(len(data) - 1)]

    # Noise floor stats in quiet band
    noise_bins = [a for f, a in data if NOISE_BAND[0] <= f <= NOISE_BAND[1]]
    noise_std = statistics.stdev(noise_bins) if len(noise_bins) > 2 else float('nan')

    # Station detection
    stations_in_range = [(n, ch, lo, hi) for n, ch, lo, hi in SF_STATIONS
                         if lo >= scan_start and hi <= scan_end]
    detected = 0
    station_peaks = {}
    for name, ch, lo, hi in stations_in_range:
        bins = [a for f, a in data if lo <= f <= hi]
        if bins:
            peak = max(bins)
            station_peaks[name] = peak
            if peak > DETECT_THRESHOLD:
                detected += 1

    detect_rate = detected / len(stations_in_range) if stations_in_range else float('nan')

    return {
        'n_points':    len(data),
        'mean_diff':   sum(diffs) / len(diffs) if diffs else 0,
        'max_diff':    max(diffs) if diffs else 0,
        'noise_std':   noise_std,
        'detect_rate': detect_rate,
        'station_peaks': station_peaks,
        'stations_checked': len(stations_in_range),
    }


def main():
    parser = argparse.ArgumentParser(description="Smoothing window optimisation test")
    parser.add_argument('--port',   default=None)
    parser.add_argument('--start',  type=float, default=506.0)
    parser.add_argument('--end',    type=float, default=600.0)
    parser.add_argument('--chunk',  type=float, default=6.0)
    parser.add_argument('--passes', type=int,   default=3)
    parser.add_argument('--iters',  type=int,   default=1)
    args = parser.parse_args()

    # Port detection
    port = args.port
    if not port:
        ports = list_serial_ports()
        if not ports:
            print("No serial ports found.")
            sys.exit(1)
        port = ports[0]
    print(f"Port: {port}")

    # Connect
    scanner = RFExplorerScanner(port)
    ok, msg = scanner.connect()
    if not ok:
        print(f"Connection failed: {msg}")
        sys.exit(1)
    print(f"Connected: {msg.splitlines()[0]}\n")

    # Collect raw passes (window=1 = no smoothing)
    stop = threading.Event()
    raw_passes: list[list[tuple[float, float]]] = []

    print(f"Scanning {args.start}–{args.end} MHz | {args.chunk} MHz chunks | "
          f"{args.iters} iter/chunk | {args.passes} passes | NO smoothing (raw)")
    print()

    for p in range(1, args.passes + 1):
        mq = queue.Queue()
        data = scanner.scan_pass(
            args.start, args.end, args.chunk, args.iters,
            stop, mq, pass_number=p, smooth_window=1,
        )
        # Drain log messages
        while not mq.empty():
            m = mq.get_nowait()

        if data:
            raw_passes.append(data)
            amps = [a for _, a in data]
            print(f"  Pass {p}: {len(data)} pts | "
                  f"{min(amps):.1f}–{max(amps):.1f} dBm")
        else:
            print(f"  Pass {p}: no data")

    scanner.disconnect()

    if not raw_passes:
        print("No data collected.")
        sys.exit(1)

    print()

    # Build cumulative max-hold per additional pass
    # so we can test window × pass_count combos on the same raw data
    # Max-hold per pass count
    pass_maxhold: list[list[tuple[float, float]]] = []
    hold: dict[float, float] = {}
    for p_data in raw_passes:
        for f, a in p_data:
            if f not in hold or a > hold[f]:
                hold[f] = a
        pass_maxhold.append(sorted(hold.items()))

    # For each pass count and window size, apply smoothing and measure
    print("=" * 78)
    print(f"{'Passes':>6}  {'Window':>6}  {'Pts':>5}  {'MeanDiff':>8}  "
          f"{'MaxDiff':>7}  {'NoiseSt':>7}  {'Detect':>6}")
    print("-" * 78)

    best_score = float('inf')
    best_config = None

    for n_passes, maxhold_data in enumerate(pass_maxhold, start=1):
        for window in TEST_WINDOWS:
            smoothed = RFExplorerScanner._smooth(maxhold_data, window=window)
            m = metrics(smoothed, args.start, args.end)
            if not m:
                continue

            score = m['mean_diff'] + m['noise_std']  # combined smoothness score
            if m['detect_rate'] == 1.0 and score < best_score:
                best_score = score
                best_config = (n_passes, window, m)

            detect_str = (f"{m['detect_rate']*100:.0f}%"
                          if not math.isnan(m['detect_rate']) else "n/a")
            noise_str  = (f"{m['noise_std']:.2f}"
                          if not math.isnan(m['noise_std']) else "n/a")
            print(f"{n_passes:>6}  {window:>6}  {m['n_points']:>5}  "
                  f"{m['mean_diff']:>8.2f}  {m['max_diff']:>7.1f}  "
                  f"{noise_str:>7}  {detect_str:>6}")

    print("=" * 78)

    if best_config:
        n, w, m = best_config
        print(f"\nBest config (smoothest with 100% detection): "
              f"{n} pass{'es' if n!=1 else ''}, window={w}")
        print(f"  Mean adj diff: {m['mean_diff']:.2f} dB  |  "
              f"Noise stddev: {m['noise_std']:.2f} dB")
        print(f"  Station peaks:")
        for name, peak in sorted(m['station_peaks'].items(), key=lambda x: -x[1]):
            flag = " ✓" if peak > DETECT_THRESHOLD else " ✗"
            print(f"    {name:<12} {peak:.1f} dBm{flag}")
    else:
        print("\nNo config achieved 100% station detection in scan range.")
        print("Check antenna connection or widen scan range.")


if __name__ == '__main__':
    main()
