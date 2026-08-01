# RF Scanner vs. Shure WWB Scan Comparison Analysis

**Date:** April 27, 2026  
**Analysis Type:** Hardware/antenna behavior with software recommendations

---

## Executive Summary

The RF Explorer scanner's apparent inability to detect TV stations visible in a Shure WWB scan is **primarily an antenna issue, not a software bug**. The device successfully detects strong signals at 620–900 MHz but is severely attenuated in the 470–540 MHz band. The included sub-GHz antenna is electrically too small at UHF TV frequencies, causing ~30–50 dB signal loss through impedance mismatch and poor efficiency.

**Next step:** Test with a proper UHF antenna (Shure paddle or log-periodic) to confirm. Expected result: RF Explorer readings will align with Shure within ~10 dB (the residual being legitimate RBW difference).

---

## Data Summary

### Test Setup
- **Location:** Same stage, ~1m offset
- **Time:** Shure at 11:21:25, RF Explorer at 11:24:55 (3.5 min apart, same day April 10, 2026)
- **Antenna:** RF Explorer with included sub-GHz whip; Shure with paddle antenna
- **Frequency range overlap:** 470.125–541.875 MHz (Shure), 470.100–541.875 MHz (RF Explorer)

### Raw Observations

| Metric | Shure | RF Explorer | Notes |
|--------|-------|-------------|-------|
| **Noise floor (quiet spectrum)** | -102.7 dBm avg | -112.2 dBm avg | RFE is ~9.5 dB *lower* (better) |
| **TV Ch18 (494–500 MHz) peak** | -61 dBm | -111.5 dBm | 50 dB gap |
| **TV Ch22 (518–522 MHz) peak** | -76 dBm | -108.4 dBm | 32 dB gap |
| **Max signal anywhere in overlap** | -61 dBm | -106.5 dBm | RFE never exceeds -100 dBm |
| **RFE max at 620–670 MHz** | N/A (Shure doesn't scan there) | -67.1 dBm | Strong reception at higher freq |

### The Smoking Gun

ATSC pilot tone at 494.31 MHz (discrete CW carrier):
- **Shure:** -64 dBm
- **RF Explorer:** -111 dBm
- **Gap:** **47 dB on a CW signal**

This is the critical data point: a CW carrier is narrowband and RBW-independent. The 47 dB gap on a pure tone is purely antenna + polarization + path loss, with no RBW math involved.

---

## Root Cause Analysis

### PRIMARY THEORY: Software Data Collection / Averaging Pipeline Issue

**User observation:** TV stations ARE visible on the live spectrum graph during scanning, but disappear in the CSV export (P20 percentile).

**This is critical evidence that the device hardware is working.** The signals are being detected and displayed in real-time, but the post-processing layer (multi-pass accumulation + percentile export) is losing them.

**Suspect mechanisms in scanner.py:**

#### 1. **Stale Sweep Rejection Too Aggressive**
Lines 350-382 in `_scan_chunk()` discard sweeps if `sweep.StartFrequencyMHZ()` doesn't match the intended chunk frequency (tolerance ~1 MHz). For broadband signals like ATSC:
- Device reports sweep start frequency (e.g., 494.0 MHz for a 494-496 MHz chunk)
- TV station spans 6 MHz and has no single "start" frequency
- If the stale-sweep check is mis-identifying valid signal-containing sweeps as "stale," it discards them
- Result: A pass that clearly shows the TV station gets thrown out before accumulation

**Fix:** Review the tolerance logic. The check should verify the LO locked *at the intended frequency*, not reject broadband content that spans beyond chunk edges.

#### 2. **Chunk Dithering + Resampling Artifact**
Lines ~220-230: On Pass 2+, chunk width is randomized ±15% (to average out IF filter rolloff artifacts at chunk edges). Combined with resampling to 25 kHz grid:
- Pass 1: Chunk 494.0-496.0 MHz captures TV signal at bin 494.3 MHz
- Pass 2: Chunk dithered to 493.8-496.2 MHz (example), TV signal now at slightly different bin after resampling
- Pass 3: Dithered to 494.2-496.2 MHz, signal again at different bin

When P20 is calculated at a FIXED frequency grid point (e.g., 494.300 MHz):
- Pass 1 at 494.300: Signal present
- Pass 2 at 494.300: Might be offset to 494.325 due to dither/resample
- Pass 3 at 494.300: Again offset, possibly to noise

**Result:** P20 across 3 passes might pick the quietest bin, missing the signal.

**Fix:** Either disable dithering for broadband scanning (accept some IF artifacts), or use max-hold instead of percentile for detection.

#### 3. **Linear-Domain Averaging of Noise-Like ATSC Signal**
Lines 438-449: Within each chunk, sweeps are averaged in linear domain (correct approach). However:
- ATSC is noise-like (8-VSB modulation spreads power across 6 MHz)
- In a 2 MHz chunk, only part of the ATSC signal is captured
- The noise-like content + pilot tone doesn't compress well across passes if there's any timing offset

This is less likely to be the culprit (linear averaging is correct) but worth checking if the device's on-board averaging differs.

#### 4. **Device Sweep Timing / Buffering Issue**
The scanning loop in `ui.py` uses a message queue with 50 ms poll rate (`root.after(50, _pump_queue)`). If the device's sweep timing has a period close to this polling interval:
- Some sweeps might be read twice (stale)
- Some might be skipped (buffering overflow)
- Result: Intermittent signals could be under-represented

**Evidence for this theory:**
- Live graph shows signals (raw, real-time, per-chunk)
- CSV export (accumulated from queue over many seconds) loses signals
- Suggests timing/synchronization issue between device sweeps and host polling

### SECONDARY THEORY: Antenna Frequency Response Collapse Below ~550 MHz

The WSUB1G Plus includes a small sub-GHz whip antenna (~5–8 cm), typically optimized for 868 MHz or 915 MHz ISM bands. At these design frequencies, it's close to a quarter-wavelength (good radiation resistance). At 470–540 MHz:

- **Electrical length:** ~6–8 cm is much shorter than λ/4 (λ/4 @ 494 MHz = ~15 cm)
- **Radiation resistance:** Collapses as antenna becomes electrically small (R_rad ∝ (2πL/λ)²)
- **Impedance mismatch:** Large return loss, poor impedance coupling to 50 Ω line
- **Typical loss:** -30 to -40 dB from impedance mismatch + poor efficiency

At 620+ MHz, the same antenna is closer to resonance, explaining the -67 dBm signals readily detected there.

### Quantitative Breakdown (494.31 MHz pilot)

| Factor | Loss | Source |
|--------|------|--------|
| Antenna efficiency (electrically small) | 20–35 dB | Small antenna at low freq has poor R_rad |
| Polarization mismatch | 3–15 dB | Shure paddle (vertical) vs. RFE whip (angle unknown) |
| Receiver NF / other | 2–5 dB | Likely small difference |
| **Total expected loss** | **25–55 dB** | — |
| **Observed gap** | **47 dB** | ✓ Fits within expected range |

### Why Noise Floor Appears "Lower" (Better)

The paradox: RFE has -9.5 dB better noise floor than Shure, yet completely misses signals. Resolution:

- **Noise floor** = receiver's intrinsic thermal noise + IF bandwidth. RBW = 10·log₁₀(BW):
  - Shure RBW: ~200 kHz (typical for WWB scanners)
  - RF Explorer RBW: ~18 kHz (from 2 MHz chunk with 18 kHz IF)
  - Difference: 10·log₁₀(200/18) ≈ **+10.4 dB** in Shure's favor
  - Observed difference in quiet bands: **-9.5 dB** (RFE lower) ✓

- **Signal level** = what comes through the antenna. Bad antenna = weak signals regardless of receiver quality.

Result: RFE can measure tiny signals in a quiet band (good receiver), but can't detect real broadcast signals because 30+ dB is lost before the receiver.

### Evidence from Higher Frequencies

The RFE's full 470–900 MHz sweep shows:

| Band | Max signal | Status |
|------|-----------|--------|
| 470–520 MHz | -106.5 dBm | Blind (antenna worst here) |
| 570–620 MHz | -80.7 dBm | Marginal |
| **620–670 MHz** | **-67.1 dBm** | **Strong reception** |
| 720–770 MHz | -74.0 dBm | Good reception |

This frequency-dependent reception curve is classic antenna frequency response, not receiver degradation.

---

## Software-Side Issues (Secondary)

### 1. P20 Percentile Choice is Wrong for Coordination

Current default: **P20 (20th percentile)**  
What it means: "This frequency was this quiet 80% of the time"  
What coordination users actually need: "Is there ever a signal here?"

**Impact:** With only 3 passes, P20 ≈ minimum. A TV station present in all 3 passes but with noise floor variations could be hidden if one pass had a quieter (deeper) sample point.

**Fix:**
- Default to **max-hold** or **P95** for coordination workflows
- Keep P20 as an option for noise-floor characterization only
- Add preset selector: "Coordination scan" vs. "Noise floor survey"

### 2. Percentile-based Export Hides Signal Variability

The 3-pass dataset is too small for meaningful percentiles:
- P20 of 3 samples ≈ minimum (just discard 1 high value, take the lowest)
- P95 of 3 samples ≈ maximum (just discard 1 low value, take the highest)

**Fix:** Recommend minimum 10 passes for percentile work. For 3-pass quick scans, use **max-hold** only.

### 3. RBW Offset Not Documented

The -9.5 dB noise floor difference is legitimate (RBW math), but users see it as "the RFE is quieter" which might create false confidence about sensitivity.

**Fix:**
- Display actual device RBW in the log: e.g., "RBW: 18 kHz (2 MHz chunk spacing)"
- Document: "Noise floor reads ~10 dB lower than Shure WWB due to narrower RBW; this is expected and correct"
- Add optional **calibration offset** (user-entered, saved per-device) to align with reference scans

### 4. Chunk Size May Be Suboptimal

Current: 2 MHz chunks → ~18 kHz RBW → -112 dBm noise floor  
Trade-off: Better for narrow-band wireless mic detection, worse for broadband signal (ATSC, DTV, cellular) visibility.

**Consider:** 4–6 MHz chunks for field coordination work where antenna loss dominates anyway.

---

## Broadband Signal Math (Why ATSC Appears Weaker)

ATSC TV signals are noise-like, spread across ~6 MHz with a discrete pilot. In narrow RBW, the broadband component gets attenuated:

**TV signal at -61 dBm peak in 6 MHz (Shure observation):**

| RBW | Expected level |
|-----|---|
| 200 kHz (Shure) | -75.3 dBm |
| 30 kHz | -83.6 dBm |
| 18 kHz (RFE) | -85.8 dBm |

This RBW math accounts for ~10–15 dB of the gap between Shure (-61) and RFE (-111). The remaining 30–35 dB is antenna loss.

---

## Recommended Tests

### Priority 1: Antenna Swap Test
**Procedure:**
1. Disconnect sub-GHz whip from RFE
2. Connect RFE via adapter to Shure paddle antenna
3. Re-run scan 470–542 MHz, 3 passes, same location/orientation
4. Export P95 (for peak detection)

**Expected result:** TV channels visible at -65 to -85 dBm, aligning with Shure readings (±10 dB).  
**If confirmed:** Proves antenna is sole culprit; proceed with software fixes.

### Priority 2: CW Reference Tone
If you have a wireless mic transmitter:
1. Key it on 490 MHz at known power level (e.g., +15 dBm transmit)
2. Measure with stock whip on RFE
3. Measure with paddle on RFE
4. Calculate antenna gain delta

### Priority 3: Antenna Frequency Sweep
With a known reference signal at variable frequency (490, 550, 600, 700, 800 MHz), measure RFE response to map the antenna's frequency-dependent gain rolloff.

---

## Recommended Software Changes

### CRITICAL (blocks field deployment)

**Fix stale sweep rejection logic** in `scanner.py:_scan_chunk()`:
- Current: Rejects sweeps if `StartFrequencyMHZ()` doesn't match chunk center (±1 MHz tolerance)
- Problem: For broadband signals (ATSC, DTV) that span multiple chunks, valid signal data is discarded
- Solution: Verify the intent of stale-sweep rejection. Is it checking LO lock stability, or rejecting out-of-band data? For a broadband signal that legitimately spans the chunk boundary, the current logic may be over-aggressive.

**Disable or adjust chunk dithering** for initial deployments:
- Dithering (±15% chunk width on Pass 2+) is meant to average IF filter artifacts
- But for broadband signals, it causes the signal to shift between frequency bins across passes
- On percentile export, this makes the signal "dilute" across bins
- Recommendation: Disable dithering, accept 2–3 dB IF ripple rather than lose signals entirely

**Change default export to max-hold** for coordination scanning:
- Current: P20 (20th percentile) — picks the quietest pass
- Problem: A signal that's present in 2 of 3 passes gets hidden
- Solution: Default to max-hold (peak) for coordination, keep P20 only as an analysis option

### High Priority

1. **Add pass-by-pass logging/visualization:**
   - Show which sweeps were discarded as "stale" and why
   - Plot min/max/mean across passes at each frequency, not just percentile
   - Helps user see if signals are intermittent vs. steady

2. **Increase default pass count:**
   - Current: 3 passes (too few for percentile statistics)
   - For P20 to be meaningful, need ~10–20 passes minimum
   - Default to 5+ for coordination work

3. **Add "signal presence" export mode:**
   - Instead of percentile amplitude, export binary: "signal detected" vs. "quiet" above threshold
   - Useful for quick "which channels are occupied" workflow

### Medium Priority

4. **Waterfall visualization during scan:**
   - Show signal evolution across the N passes
   - Red = signal, blue = noise floor
   - Reveals if signals are consistently detected or intermittent

5. **Device diagnostics logging:**
   - Log stale sweeps rejected, reason, and frequency
   - Log actual chunk widths used (for dithering verification)
   - Export this to CSV for troubleshooting

6. **Calibration offset input:**
   - Allow user to add/subtract fixed dB offset to all exports
   - Saves as config for the device/antenna combo
   - Aligns with reference scans (e.g., Shure WWB)

---

## Conclusion

The RF Explorer scanner **hardware is detecting the TV stations correctly** (visible on live graph), but the **software data collection pipeline is discarding or failing to accumulate the signal data** into the exported CSV.

**Root cause is most likely:**
1. **Stale sweep rejection** in `_scan_chunk()` is too strict, discarding valid broadband signal sweeps
2. **Chunk dithering + resampling** causes the signal to shift between frequency bins across passes, diluting the P20 percentile
3. **Polling/timing issue** in the message queue could cause sweeps with signals to be skipped or merged incorrectly

The ~47 dB gap between Shure (-64 dBm at pilot) and RFE CSV (-111 dBm) is NOT from antenna loss — it's from the software pipeline losing the data entirely.

**Path forward:**
1. **Immediately:** Review and fix stale-sweep rejection logic (lines 350-382 in scanner.py). Verify it doesn't discard broadband signals.
2. **Immediately:** Disable chunk dithering or switch to max-hold export instead of P20.
3. **Next build:** Add logging to show which sweeps are rejected and why.
4. **Test:** Rescan the same location with fixed chunk width (no dithering) and max-hold export. Should see TV stations appear.

The antenna can be tested with the paddle swap, but the primary issue is the software, not the hardware.

---

## Appendix: Test Data

### Detailed Frequency Comparison (Shure vs. RF Explorer)

**TV Ch18 Region (494–500 MHz):**

| Freq (MHz) | Shure (dBm) | RFE (dBm) | Diff |
|---|---|---|---|
| 494.300 | -64.0 | -111.0 | 47.0 |
| 495.000 | -65.0 | -111.2 | 46.2 |
| 496.000 | -67.0 | -111.3 | 44.3 |
| 497.500 | -62.0 | -111.5 | 49.5 |
| 498.825 | -65.0 | -111.4 | 46.4 |

**Quiet Spectrum Region (<-100 dBm Shure):**

- Shure avg: -102.7 dBm
- RFE avg: -112.2 dBm
- Diff: -9.5 dBm (RFE lower = better receiver, NOT better antenna)

### RF Explorer Full Sweep Peak Signals

| Sub-band | Max Signal | Notes |
|---|---|---|
| 470–520 MHz | -106.5 dBm | Worst reception; antenna rolls off |
| 520–570 MHz | -96.8 dBm | Improving |
| 570–620 MHz | -80.7 dBm | Good |
| 620–670 MHz | **-67.1 dBm** | TV stations clearly visible |
| 720–770 MHz | -74.0 dBm | LTE/cell bands |

---

**Analysis completed:** April 27, 2026  
**Next review:** After antenna swap test
