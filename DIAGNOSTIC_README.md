# RF Scanner Diagnostic Test Guide

## Quick Start

Connect the RF Explorer device and run:

```bash
# Quick test (TV channel band, 3 passes)
python3 simple_diagnostic.py --quick

# Or standard test (full UHF, 3 passes)
python3 simple_diagnostic.py

# Better statistics (full UHF, 5 passes)
python3 simple_diagnostic.py --passes 5
```

The script will:
1. Connect to the device
2. Run 3–5 scanning passes over 470–542 MHz (or 490–540 MHz if `--quick`)
3. Export the same data in 5 different formats:
   - `*_01_MAXHOLD.csv` — Peak signal ever detected
   - `*_02_P95.csv` — 95th percentile (aggressive)
   - `*_03_P50.csv` — Median
   - `*_04_P20_DEFAULT.csv` — 20th percentile (current default)
   - `*_05_MINHOLD.csv` — Noise floor

## What to Look For

### Expected Output

You should see output like:
```
[PASS 1/3] 2871 points | -114.0 to -61.0 dBm
  → PEAKS DETECTED: 127 freq bins > -95 dBm
     494.300 MHz: -64.0 dBm
     498.825 MHz: -65.0 dBm
     ...
```

This means the device IS detecting TV stations on the live scan.

### Compare the Exports

Open the 5 CSV files in a spreadsheet or check with:

```bash
# Show first TV channel peak in each file
grep "^494\." diagnostic_logs/DIAGNOSTIC_*.csv | head -20
```

You should see:
```
diagnostic_logs/DIAGNOSTIC_..._01_MAXHOLD.csv:494.300,-64.0
diagnostic_logs/DIAGNOSTIC_..._02_P95.csv:494.300,-65.2
diagnostic_logs/DIAGNOSTIC_..._03_P50.csv:494.300,-72.1
diagnostic_logs/DIAGNOSTIC_..._04_P20_DEFAULT.csv:494.300,-111.5  ← Problem!
diagnostic_logs/DIAGNOSTIC_..._05_MINHOLD.csv:494.300,-113.0
```

**If P20 shows -111.5 dBm while Max-hold shows -64 dBm, you found the bug.**

## Test Results Interpretation

### Scenario 1: Signals in Max-hold, Missing in P20
```
Max-hold peaks:  127 ← Device detects the signals
P95 peaks:       110
P50 peaks:       45
P20 peaks:       0 ← Signals vanish here!
```
**Diagnosis:** P20 percentile is hiding signals.  
**Root cause:** With only 3 passes, P20 ≈ minimum. If any pass has noise in place of signal, P20 picks it.  
**Fix:** Use max-hold or increase pass count to 10+ for percentile to work.

### Scenario 2: Signals in All Exports
```
Max-hold peaks:  127
P95 peaks:       125
P50 peaks:       118
P20 peaks:       115
```
**Diagnosis:** All export formats see the signals.  
**Implication:** Software pipeline is working. Problem may be antenna/hardware.  
**Next step:** Test with Shure paddle antenna to confirm antenna issue.

### Scenario 3: No Signals Anywhere
```
Max-hold peaks:  0
P95 peaks:       0
P20 peaks:       0
```
**Diagnosis:** Device not detecting any signals.  
**Possible causes:**
- Antenna disconnected or reversed
- Device in wrong frequency band
- RF environment issue (maybe indoors, away from windows?)
- Device firmware issue

**Troubleshooting:**
- Check antenna SMA connector is tight
- Try holding antenna near a window
- Verify device shows frequencies correctly in live graph during scan
- Check device logs for errors

## Understanding the Outputs

### Max-hold
Shows the strongest signal ever detected at each frequency across all passes. Best for finding occupied channels.

### P95 (95th percentile)
Shows 95% of measurements were at this level or quieter. Aggressive signal detection — catches intermittent signals and fading.

### P50 (median)
Middle ground — half the time quieter, half the time stronger. Good for average interference level.

### P20 (20th percentile)
Shows 80% of the time was this quiet or quieter. With only 3 passes, this is almost always the minimum (throws out 1 high value, takes the 2nd lowest). Too conservative for signal detection.

### Min-hold
The absolute quietest reading at each frequency. Shows the noise floor / best-case spectrum.

## Bay Area TV Reference

See `BAY_AREA_REFERENCE.md` for expected signal levels. From 94103 (downtown SF):
- **Ch 14 (KQED)** @ 470–476 MHz: expect -60 to -75 dBm
- **Ch 18 (ABC7)** @ 494–500 MHz: expect -60 to -70 dBm
- **Ch 22 (KPIX)** @ 518–524 MHz: expect -65 to -80 dBm
- **Ch 24 (KTSF)** @ 530–536 MHz: expect -70 to -85 dBm

If your max-hold shows these channels at these levels, the device and antenna are working.

## Advanced Tests

### Test Dithering Effect

The current code uses chunk dithering (randomized ±15% chunk width on passes 2+) to average out IF filter artifacts. This could cause broadband signals to shift between frequency bins.

To test if dithering is the problem, you'd need to:
1. Modify `scanner.py:scan_pass()` to skip dithering (comment out lines ~220-230)
2. Re-run the diagnostic
3. Compare `P20_WITH_DITHER` vs `P20_NO_DITHER`

If signals appear only in the NO_DITHER version, dithering is causing the issue.

### Test Pass Count

The percentile math only works if you have enough samples:
- 3 passes: P20 ≈ minimum (meaningless)
- 5 passes: P20 = 1st percentile (still too conservative)
- 10 passes: P20 = 2nd percentile (useful)
- 20 passes: P20 = 4th percentile (meaningful)

Try running with `--passes 10` and see if P20 looks more reasonable.

## File Locations

Scripts created:
- `simple_diagnostic.py` — Main test runner (run this)
- `diagnostic_test.py` — More advanced version (optional)
- `BAY_AREA_REFERENCE.md` — Expected signal levels for your area

Output files:
- `diagnostic_logs/DIAGNOSTIC_YYYYMMDD_HHMMSS_*.csv` — The 5 export formats

## Troubleshooting the Test Itself

**Q: "ERROR: No device found"**  
A: Device not connected or not detected. Check:
- Device is plugged in via USB
- Device is turned on (if it has a power switch)
- Try specifying port: `--port /dev/ttyUSB0` (Linux) or `--port COM3` (Windows)

**Q: "ERROR: No data returned"**  
A: Device connected but scan failed. Possible causes:
- Device in wrong mode (check live graph works)
- Out of memory or crash (try with smaller range: `--start 500 --end 550`)
- Check device logs

**Q: Takes forever to complete**  
A: Normal for full sweep. Full scan (470–542 MHz, 2 MHz chunks = 36 chunks × 2 sweeps = ~3–4 min per pass). Use `--quick` for faster test.

---

**Once you run this and have the CSV files, share the results and we can pinpoint exactly what's happening.**
