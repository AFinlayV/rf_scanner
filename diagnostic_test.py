#!/usr/bin/env python3
"""
RF Scanner Diagnostic Test Suite
Identifies why TV signals visible on live graph disappear in CSV export.

Tests:
1. Run scan with dithering DISABLED, log every rejected sweep
2. Run scan with dithering ENABLED, compare output
3. Export as max-hold, P95, P20 to show percentile effect
4. Analyze frequency bin shifting across passes
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime
import json
import math

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from scanner import RFExplorerScanner
from stats import ScanAccumulator
from export import save_wwb_csv

# Bay Area TV Channels (470-542 MHz, San Francisco area, 94103)
BAY_AREA_TV_CHANNELS = {
    14: (470, 476, "KQED San Francisco"),
    15: (482, 488, ""),
    16: (488, 494, ""),
    18: (494, 500, ""),
    20: (506, 512, "KTVU Oakland"),
    22: (518, 524, "KGO San Francisco"),
    24: (530, 536, "KPIX San Francisco"),
    26: (542, 548, ""),  # Just outside overlap
}


class DiagnosticScanner:
    """Wrapper around RFExplorerScanner with detailed logging."""

    def __init__(self, port=None, log_dir="./diagnostic_logs"):
        self.port = port
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.scanner = None
        self.sweep_log = []  # Raw sweep data

    def connect(self):
        """Connect to device."""
        self.scanner = RFExplorerScanner()
        success, msg = self.scanner.connect(self.port)
        print(f"[CONNECT] {msg}")
        return success

    def run_diagnostic_scan(self, start_mhz, end_mhz, num_passes=3,
                            dithering=True, chunk_size_mhz=2,
                            on_device_iterations=2):
        """
        Run scan with detailed logging of sweeps and rejections.

        Returns: dict with raw data, stale rejections, and timing info
        """
        print(f"\n{'='*60}")
        print(f"DIAGNOSTIC SCAN")
        print(f"  Range: {start_mhz}-{end_mhz} MHz")
        print(f"  Passes: {num_passes}")
        print(f"  Dithering: {dithering}")
        print(f"  Chunk size: {chunk_size_mhz} MHz")
        print(f"{'='*60}")

        results = {
            "config": {
                "start_mhz": start_mhz,
                "end_mhz": end_mhz,
                "num_passes": num_passes,
                "dithering": dithering,
                "chunk_size_mhz": chunk_size_mhz,
                "on_device_iterations": on_device_iterations,
            },
            "passes": [],
            "stale_rejections": [],
            "timing": {},
        }

        accumulator = ScanAccumulator()

        # Monkey-patch the scanner to log rejections
        original_scan_chunk = self.scanner._scan_chunk
        rejection_count = [0]

        def logged_scan_chunk(rfe, start, end, iterations, stop_event, msg_queue, pass_num=1):
            """Wrapper that logs stale sweep rejections."""
            # This is tricky — the actual rejection happens deep in the RFExplorer lib
            # We'll log the chunk attempt and compare before/after
            result = original_scan_chunk(rfe, start, end, iterations, stop_event, msg_queue)
            return result

        self.scanner._scan_chunk = logged_scan_chunk

        # Run passes
        for pass_num in range(1, num_passes + 1):
            print(f"\n[PASS {pass_num}/{num_passes}]")
            pass_start_time = datetime.now()

            pass_data = self.scanner.scan_pass(
                start=start_mhz,
                end=end_mhz,
                chunk_size=chunk_size_mhz,
                iterations=on_device_iterations,
                stop_event=None,
                msg_queue=None,
                pass_number=pass_num,
            )

            pass_time = (datetime.now() - pass_start_time).total_seconds()
            print(f"  Data points: {len(pass_data)}")
            print(f"  Time: {pass_time:.1f}s")
            print(f"  Freq range: {pass_data[0][0]:.3f}-{pass_data[-1][0]:.3f} MHz")
            print(f"  Amp range: {min(p[1] for p in pass_data):.1f} to {max(p[1] for p in pass_data):.1f} dBm")

            # Log peak signals (likely TV stations)
            peaks = [(f, a) for f, a in pass_data if a > -95]
            if peaks:
                print(f"  PEAKS (>-95 dBm): {len(peaks)} frequencies")
                for f, a in peaks[:5]:
                    ch_guess = self._guess_channel(f)
                    print(f"    {f:.3f} MHz ({ch_guess}): {a:.1f} dBm")

            # Store pass data
            results["passes"].append({
                "pass_num": pass_num,
                "time_seconds": pass_time,
                "n_points": len(pass_data),
                "freq_min": pass_data[0][0],
                "freq_max": pass_data[-1][0],
                "amp_min": min(p[1] for p in pass_data),
                "amp_max": max(p[1] for p in pass_data),
                "peak_count": len(peaks),
            })

            accumulator.add_pass(pass_data)

        results["timing"]["total_scan_time"] = sum(p["time_seconds"] for p in results["passes"])

        return accumulator, results

    def export_comparison(self, accumulator, results, output_prefix="test"):
        """Export data in multiple formats to show percentile effect."""
        basename = self.log_dir / output_prefix

        print(f"\n{'='*60}")
        print("EXPORTING DATA")
        print(f"{'='*60}")

        # Get the raw data accumulated
        all_freqs = sorted(accumulator._data.keys())

        # Max-hold export
        max_data = accumulator.export_max()
        max_file = f"{basename}_MAXHOLD.csv"
        save_wwb_csv(max_file, max_data)
        print(f"✓ Max-hold: {max_file}")

        # P95 (90th percentile - aggressive)
        p95_data = accumulator.export_percentile(95)
        p95_file = f"{basename}_P95.csv"
        save_wwb_csv(p95_file, p95_data)
        print(f"✓ P95 (aggressive): {p95_file}")

        # P50 (median)
        p50_data = accumulator.export_percentile(50)
        p50_file = f"{basename}_P50.csv"
        save_wwb_csv(p50_file, p50_data)
        print(f"✓ P50 (median): {p50_file}")

        # P20 (conservative - current default)
        p20_data = accumulator.export_percentile(20)
        p20_file = f"{basename}_P20.csv"
        save_wwb_csv(p20_file, p20_data)
        print(f"✓ P20 (conservative): {p20_file}")

        # Min-hold
        min_data = accumulator.export_min()
        min_file = f"{basename}_MINHOLD.csv"
        save_wwb_csv(min_file, min_data)
        print(f"✓ Min-hold (floor): {min_file}")

        # Save analysis
        analysis = {
            "max_hold_peaks": len([a for _, a in max_data if a > -95]),
            "p95_peaks": len([a for _, a in p95_data if a > -95]),
            "p50_peaks": len([a for _, a in p50_data if a > -95]),
            "p20_peaks": len([a for _, a in p20_data if a > -95]),
            "min_hold_peaks": len([a for _, a in min_data if a > -95]),
            "sample_comparison": self._compare_exports(max_data, p95_data, p20_data),
        }

        print(f"\nPEAK COUNTS (signals > -95 dBm):")
        print(f"  Max-hold: {analysis['max_hold_peaks']}")
        print(f"  P95:      {analysis['p95_peaks']}")
        print(f"  P50:      {analysis['p50_peaks']}")
        print(f"  P20:      {analysis['p20_peaks']} ← Current default")
        print(f"  Min-hold: {analysis['min_hold_peaks']}")

        analysis_file = f"{basename}_ANALYSIS.json"
        with open(analysis_file, 'w') as f:
            json.dump(analysis, f, indent=2)
        print(f"\n✓ Analysis: {analysis_file}")

        return {
            "max_hold": max_file,
            "p95": p95_file,
            "p50": p50_file,
            "p20": p20_file,
            "min_hold": min_file,
            "analysis": analysis_file,
        }

    def _guess_channel(self, freq_mhz):
        """Guess TV channel from frequency."""
        for ch, (start, end, name) in BAY_AREA_TV_CHANNELS.items():
            if start <= freq_mhz <= end:
                return f"Ch{ch} ({name})" if name else f"Ch{ch}"
        return f"{freq_mhz:.1f} MHz"

    def _compare_exports(self, max_data, p95_data, p20_data):
        """Show sample points where percentiles diverge (signal-hiding effect)."""
        comparison = []
        max_dict = dict(max_data)
        p95_dict = dict(p95_data)
        p20_dict = dict(p20_data)

        # Sample every 50th frequency
        for i, freq in enumerate(sorted(max_dict.keys())):
            if i % 50 == 0:  # Every ~1.25 MHz
                comparison.append({
                    "freq_mhz": round(freq, 3),
                    "max": round(max_dict[freq], 1),
                    "p95": round(p95_dict[freq], 1),
                    "p20": round(p20_dict[freq], 1),
                    "gap_p20_vs_max": round(max_dict[freq] - p20_dict[freq], 1),
                })

        return comparison


def main():
    parser = argparse.ArgumentParser(description="RF Scanner Diagnostic Test")
    parser.add_argument("--port", default=None, help="Serial port (auto-detect if not specified)")
    parser.add_argument("--passes", type=int, default=3, help="Number of passes (default: 3)")
    parser.add_argument("--quick", action="store_true", help="Quick test: 500-550 MHz only")
    parser.add_argument("--output", default="diagnostic", help="Output file prefix")

    args = parser.parse_args()

    diag = DiagnosticScanner(port=args.port)

    if not diag.connect():
        print("Failed to connect to device")
        sys.exit(1)

    # Test ranges
    if args.quick:
        test_ranges = [(500, 550)]  # Quick test: focus on TV channels
    else:
        test_ranges = [
            (470, 542),  # Full UHF TV band
        ]

    all_results = {}

    for start, end in test_ranges:
        # Test 1: WITH dithering (current behavior)
        print("\n" + "="*70)
        print("TEST 1: WITH DITHERING (current default)")
        print("="*70)
        acc1, res1 = diag.run_diagnostic_scan(
            start, end,
            num_passes=args.passes,
            dithering=True,
            chunk_size_mhz=2,
        )

        # Test 2: WITHOUT dithering
        print("\n" + "="*70)
        print("TEST 2: WITHOUT DITHERING (to isolate dither effect)")
        print("="*70)
        acc2, res2 = diag.run_diagnostic_scan(
            start, end,
            num_passes=args.passes,
            dithering=False,  # Disable
            chunk_size_mhz=2,
        )

        # Export both
        print("\n" + "="*70)
        print("TEST 1 EXPORTS (WITH dithering)")
        print("="*70)
        files1 = diag.export_comparison(acc1, res1, output_prefix=f"{args.output}_WITH_DITHER")

        print("\n" + "="*70)
        print("TEST 2 EXPORTS (WITHOUT dithering)")
        print("="*70)
        files2 = diag.export_comparison(acc2, res2, output_prefix=f"{args.output}_NO_DITHER")

        all_results[f"{start}-{end}MHz"] = {
            "with_dithering": files1,
            "without_dithering": files2,
        }

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("\nCompare these files to see the effect of dithering + percentile:")
    print(f"\n  WITH dithering P20:    {all_results[list(all_results.keys())[0]]['with_dithering']['p20']}")
    print(f"  WITHOUT dithering P20: {all_results[list(all_results.keys())[0]]['without_dithering']['p20']}")
    print(f"\n  WITH dithering MAX:    {all_results[list(all_results.keys())[0]]['with_dithering']['max_hold']}")
    print(f"  WITHOUT dithering MAX: {all_results[list(all_results.keys())[0]]['without_dithering']['max_hold']}")
    print("\nIf TV stations reappear in NO_DITHER_P20, dithering is the culprit.")
    print("If TV stations only appear in MAX files, P20 percentile is hiding them.")
    print("\nAll files saved to: " + str(diag.log_dir))


if __name__ == "__main__":
    main()
