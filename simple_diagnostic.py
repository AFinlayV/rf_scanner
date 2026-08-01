#!/usr/bin/env python3
"""
Simple diagnostic runner - tests scanning with different export modes.
No patching of internals, just runs normal scans and exports multiple ways.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime
import threading
from queue import Queue

sys.path.insert(0, str(Path(__file__).parent))

from scanner import RFExplorerScanner, list_serial_ports
from stats import ScanAccumulator
from export import save_wwb_csv


def run_test(port=None, start_mhz=470, end_mhz=542, num_passes=3,
             chunk_size=2, output_dir="./diagnostic_logs"):
    """Run a diagnostic scan and export multiple formats."""

    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\n{'='*70}")
    print(f"RF SCANNER DIAGNOSTIC TEST")
    print(f"{'='*70}")
    print(f"Time:        {datetime.now().isoformat()}")
    print(f"Range:       {start_mhz}–{end_mhz} MHz")
    print(f"Passes:      {num_passes}")
    print(f"Chunk size:  {chunk_size} MHz")
    print(f"Output dir:  {output_dir}")
    print(f"{'='*70}\n")

    # Auto-detect port if not specified
    if port is None:
        ports = list_serial_ports()
        if not ports:
            print("ERROR: No serial ports found. Is the device connected?")
            return False
        port = ports[0]
        print(f"Auto-detected port: {port}")

    # Connect to device
    try:
        scanner = RFExplorerScanner(port)
    except Exception as e:
        print(f"ERROR connecting to {port}: {e}")
        return False

    success, msg = scanner.connect()
    if not success:
        print(f"ERROR: {msg}")
        return False

    print(f"✓ Connected: {msg}\n")

    # Accumulator for multi-pass stats
    accumulator = ScanAccumulator()

    # Track raw pass data for comparison
    pass_data_list = []

    # Run passes
    for pass_num in range(1, num_passes + 1):
        print(f"[PASS {pass_num}/{num_passes}] ", end="", flush=True)

        stop_event = threading.Event()
        msg_queue = Queue()

        pass_data = scanner.scan_pass(
            start_mhz=start_mhz,
            end_mhz=end_mhz,
            chunk_mhz=chunk_size,
            iterations=2,
            stop_event=stop_event,
            msg_q=msg_queue,
            pass_number=pass_num,
        )

        # Stats
        if pass_data:
            amp_min = min(p[1] for p in pass_data)
            amp_max = max(p[1] for p in pass_data)
            print(f"{len(pass_data)} points | {amp_min:.1f} to {amp_max:.1f} dBm")

            # Check for signals
            peaks = [(f, a) for f, a in pass_data if a > -95]
            if peaks:
                print(f"  → PEAKS DETECTED: {len(peaks)} freq bins > -95 dBm")
                for f, a in sorted(peaks)[:3]:
                    print(f"     {f:.3f} MHz: {a:.1f} dBm")

            pass_data_list.append(pass_data)
            accumulator.add_pass(pass_data)
        else:
            print("ERROR: No data returned")
            return False

    print(f"\n{'='*70}")
    print("EXPORTING DATA")
    print(f"{'='*70}\n")

    # Export in different formats
    exports = {}

    # 1. Max-hold (should show all signals ever detected)
    max_data = accumulator.export_max()
    max_file = output_dir / f"DIAGNOSTIC_{timestamp}_01_MAXHOLD.csv"
    save_wwb_csv(str(max_file), max_data)
    exports['max'] = max_file
    max_peaks = len([a for _, a in max_data if a > -95])
    print(f"✓ Max-hold (peak signal ever seen)")
    print(f"  File: {max_file.name}")
    print(f"  Signals > -95 dBm: {max_peaks}\n")

    # 2. P95 (aggressive detection)
    p95_data = accumulator.export_percentile(95)
    p95_file = output_dir / f"DIAGNOSTIC_{timestamp}_02_P95.csv"
    save_wwb_csv(str(p95_file), p95_data)
    exports['p95'] = p95_file
    p95_peaks = len([a for _, a in p95_data if a > -95])
    print(f"✓ P95 (95th percentile - aggressive)")
    print(f"  File: {p95_file.name}")
    print(f"  Signals > -95 dBm: {p95_peaks}\n")

    # 3. P50 (median)
    p50_data = accumulator.export_percentile(50)
    p50_file = output_dir / f"DIAGNOSTIC_{timestamp}_03_P50.csv"
    save_wwb_csv(str(p50_file), p50_data)
    exports['p50'] = p50_file
    p50_peaks = len([a for _, a in p50_data if a > -95])
    print(f"✓ P50 (median)")
    print(f"  File: {p50_file.name}")
    print(f"  Signals > -95 dBm: {p50_peaks}\n")

    # 4. P20 (conservative - current default)
    p20_data = accumulator.export_percentile(20)
    p20_file = output_dir / f"DIAGNOSTIC_{timestamp}_04_P20_DEFAULT.csv"
    save_wwb_csv(str(p20_file), p20_data)
    exports['p20'] = p20_file
    p20_peaks = len([a for _, a in p20_data if a > -95])
    print(f"✓ P20 (20th percentile - CURRENT DEFAULT)")
    print(f"  File: {p20_file.name}")
    print(f"  Signals > -95 dBm: {p20_peaks}\n")

    # 5. Min-hold (noise floor)
    min_data = accumulator.export_min()
    min_file = output_dir / f"DIAGNOSTIC_{timestamp}_05_MINHOLD.csv"
    save_wwb_csv(str(min_file), min_data)
    exports['min'] = min_file
    print(f"✓ Min-hold (noise floor)")
    print(f"  File: {min_file.name}\n")

    # Analysis
    print(f"{'='*70}")
    print("RESULTS SUMMARY")
    print(f"{'='*70}\n")

    print("Peak Signal Counts (> -95 dBm):")
    print(f"  Max-hold: {max_peaks} ← Most likely to show all signals")
    print(f"  P95:      {p95_peaks}")
    print(f"  P50:      {p50_peaks}")
    print(f"  P20:      {p20_peaks} ← Current default (may hide signals)")
    print(f"  Min-hold: {len([a for _, a in min_data if a > -95])}")

    print(f"\n{'='*70}")
    print("INTERPRETATION")
    print(f"{'='*70}\n")

    if max_peaks > 0 and p20_peaks == 0:
        print("⚠️  FOUND THE BUG!")
        print("   TV stations are detected (Max-hold shows them)")
        print("   But disappear in P20 export (current default)")
        print("   → Solution: Use max-hold or P95 for coordination scans\n")
    elif max_peaks > 0 and p20_peaks > 0:
        print("✓ Signals detected in both max-hold AND P20")
        print("   The export modes are working correctly")
        print("   → May be antenna or other hardware issue\n")
    else:
        print("⚠️  No signals detected in any export")
        print("   → Check antenna connection or device settings\n")

    # Sample frequency comparison
    print("Sample frequency comparison (every ~5 MHz):")
    print(f"{'Freq MHz':>10} {'Max-h':>8} {'P95':>8} {'P50':>8} {'P20':>8} {'Gap P20-Max':>12}")
    print("─" * 60)

    max_dict = dict(max_data)
    p95_dict = dict(p95_data)
    p50_dict = dict(p50_data)
    p20_dict = dict(p20_data)

    for i, freq in enumerate(sorted(max_dict.keys())):
        if i % 200 == 0:  # Every ~5 MHz
            gap = max_dict[freq] - p20_dict[freq]
            print(f"{freq:10.3f} {max_dict[freq]:8.1f} {p95_dict[freq]:8.1f} "
                  f"{p50_dict[freq]:8.1f} {p20_dict[freq]:8.1f} {gap:+12.1f}")

    print(f"\n{'='*70}")
    print("NEXT STEPS")
    print(f"{'='*70}\n")
    print("1. Check the 5 CSV files:")
    print(f"   - Open in spreadsheet or with: head {max_file.name}")
    print(f"   - Look for TV channel signals (470-540 MHz range)")
    print(f"\n2. Compare P20 vs Max-hold:")
    print(f"   - If Max shows signals but P20 doesn't → percentile issue")
    print(f"   - If both show signals → hardware is fine")
    print(f"\n3. Run again with different pass counts:")
    print(f"   - Try with --passes 5 or --passes 10")
    print(f"   - P20 of 10 samples is more meaningful than P20 of 3")
    print(f"\n{'='*70}\n")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Simple RF Scanner diagnostic - exports multiple percentile formats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test focusing on TV channel band
  python3 simple_diagnostic.py --quick

  # Full UHF band with 5 passes for better percentile stats
  python3 simple_diagnostic.py --passes 5

  # Specific frequency range
  python3 simple_diagnostic.py --start 480 --end 550 --passes 3
        """
    )
    parser.add_argument("--port", default=None,
                       help="Serial port (auto-detect if not specified)")
    parser.add_argument("--start", type=float, default=470,
                       help="Start frequency (MHz)")
    parser.add_argument("--end", type=float, default=542,
                       help="End frequency (MHz)")
    parser.add_argument("--passes", type=int, default=3,
                       help="Number of passes (default: 3, recommend 5+ for P20 stats)")
    parser.add_argument("--chunk", type=float, default=2,
                       help="Chunk size (MHz)")
    parser.add_argument("--output", default="./diagnostic_logs",
                       help="Output directory")
    parser.add_argument("--quick", action="store_true",
                       help="Quick test: 490-540 MHz only (TV channels)")

    args = parser.parse_args()

    if args.quick:
        args.start = 490
        args.end = 540

    try:
        success = run_test(
            port=args.port,
            start_mhz=args.start,
            end_mhz=args.end,
            num_passes=args.passes,
            chunk_size=args.chunk,
            output_dir=args.output,
        )
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
