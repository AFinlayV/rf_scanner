#!/usr/bin/env python3
"""
Minimal direct test of RF Explorer device.
Bypasses scanner.py wrapper — uses RFExplorer library directly.
Tests a single TV channel chunk to confirm device returns data.
"""

import sys
import time
import serial.tools.list_ports

sys.path.insert(0, '.')

import RFExplorer
from RFExplorer import RFE_Common


def find_port():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    likely = [p for p in ports if "SLAB" in p or "usbserial" in p]
    return likely[0] if likely else (ports[0] if ports else None)


def main():
    port = find_port()
    if not port:
        print("ERROR: No serial port found")
        sys.exit(1)
    print(f"Port: {port}")

    rfe = RFExplorer.RFECommunicator()
    rfe.AutoConfigure = True

    # Connect
    print("Connecting...")
    rfe.ConnectPort(port, 500000)
    time.sleep(2)

    # Wait for device to be ready
    print("Waiting for device config...")
    for _ in range(20):
        rfe.ProcessReceivedString(True)
        if rfe.ActiveModel != RFE_Common.eModel.MODEL_NONE:
            break
        time.sleep(0.2)

    print(f"Model: {rfe.ActiveModel}")
    print(f"Connected: {rfe.IsConnected}")

    # Scan TV channel 18 (494–500 MHz) — should be a strong signal in SF
    TEST_START = 494.0
    TEST_END   = 500.0
    print(f"\nScanning {TEST_START}–{TEST_END} MHz (TV Ch18)...")
    rfe.UpdateDeviceConfig(TEST_START, TEST_END)
    time.sleep(0.3)
    rfe.SweepData.CleanAll()

    # Collect sweeps for 10 seconds
    sweeps_received = []
    deadline = time.time() + 10.0
    last_count = 0

    print("Reading sweeps (10 sec)...")
    while time.time() < deadline:
        rfe.ProcessReceivedString(True)
        count = rfe.SweepData.Count
        if count > last_count:
            sweep = rfe.SweepData.GetData(count - 1)
            if sweep:
                n_pts = sweep.TotalDataPoints() if callable(sweep.TotalDataPoints) else sweep.TotalDataPoints
                sw_start = sweep.StartFrequencyMHZ() if callable(sweep.StartFrequencyMHZ) else sweep.StartFrequencyMHZ
                if n_pts > 0:
                    amps = [sweep.GetAmplitude_DBM(i) for i in range(n_pts)]
                    sweeps_received.append((sw_start, amps))
                    print(f"  Sweep {len(sweeps_received)}: start={sw_start:.3f} MHz, {n_pts} pts, "
                          f"min={min(amps):.1f} max={max(amps):.1f} dBm")
            last_count = count
        time.sleep(0.05)

    print(f"\nTotal sweeps received: {len(sweeps_received)}")

    if sweeps_received:
        # Print the best (highest peak) sweep in detail
        best = max(sweeps_received, key=lambda s: max(s[1]))
        sw_start, amps = best
        step = (TEST_END - TEST_START) / (len(amps) - 1)
        print(f"\nBest sweep detail (start={sw_start:.3f} MHz, step={step*1000:.0f} kHz):")
        print(f"{'Freq MHz':>10}  {'dBm':>8}")
        for i, amp in enumerate(amps):
            freq = sw_start + i * step
            marker = " ← SIGNAL" if amp > -95 else ""
            print(f"{freq:10.3f}  {amp:8.1f}{marker}")

        # Also scan a quiet region for comparison
        print(f"\nScanning 478–484 MHz (quiet, between TV channels)...")
        rfe.UpdateDeviceConfig(478.0, 484.0)
        time.sleep(0.3)
        rfe.SweepData.CleanAll()

        quiet_sweeps = []
        deadline2 = time.time() + 5.0
        last_count2 = 0
        while time.time() < deadline2:
            rfe.ProcessReceivedString(True)
            count = rfe.SweepData.Count
            if count > last_count2:
                sweep = rfe.SweepData.GetData(count - 1)
                if sweep:
                    n_pts = sweep.TotalDataPoints() if callable(sweep.TotalDataPoints) else sweep.TotalDataPoints
                    if n_pts > 0:
                        amps2 = [sweep.GetAmplitude_DBM(i) for i in range(n_pts)]
                        quiet_sweeps.append(amps2)
                last_count2 = count
            time.sleep(0.05)

        if quiet_sweeps:
            avg_quiet = sum(sum(s)/len(s) for s in quiet_sweeps) / len(quiet_sweeps)
            print(f"Quiet region average: {avg_quiet:.1f} dBm ({len(quiet_sweeps)} sweeps)")

        print("\n✓ Device is returning data")
    else:
        print("\n⚠ No sweeps received — device may not be responding to frequency config")

    rfe.ClosePort()


if __name__ == "__main__":
    main()
