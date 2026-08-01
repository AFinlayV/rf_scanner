# Bay Area TV Station Reference (San Francisco, 94103)

Zip code 94103 is downtown San Francisco. Strong TV stations should be visible from this location.

## UHF Broadcast Channels (470–542 MHz)

| Channel | Frequency | Station | Notes |
|---------|-----------|---------|-------|
| **14** | **470–476 MHz** | **KQED** | Strong, public TV (PBS) |
| 15 | 482–488 MHz | — | Secondary |
| 16 | 488–494 MHz | — | Secondary |
| **18** | **494–500 MHz** | **ABC7 / KGO** | Very strong, major market |
| 20 | 506–512 MHz | **KTVU** | Strong, NBC Bay Area |
| **22** | **518–524 MHz** | **KPIX** | Strong, CBS San Francisco |
| **24** | **530–536 MHz** | **KTSF** | Local, ABC |
| 26 | 542–548 MHz | KBCW | CW, CBS affiliate |

## Expected Signal Levels at Downtown SF Location

Assuming outdoor or near-window antenna:
- **Strong stations (Ch 14, 18, 22, 24):** -60 to -80 dBm
- **Weaker stations (Ch 20, 26):** -75 to -95 dBm
- **Noise floor (no signal):** -100 to -115 dBm

## Test Targets

When running diagnostic scans, you should see:

### In the LIVE GRAPH (while scanning):
- Ch 14 @ 470–476 MHz: Strong signal peak
- Ch 18 @ 494–500 MHz: Very strong signal peak
- Ch 22 @ 518–524 MHz: Strong signal peak
- Ch 24 @ 530–536 MHz: Strong signal peak

### In the CSV EXPORT (after scan):
**With current P20 export:** Signals may disappear (this is the bug we're testing)  
**With max-hold export:** Signals should be visible at same levels as live graph

## Running the Test

```bash
# Quick test: focus on the TV channel region
python3 diagnostic_test.py --quick --passes 3

# Full test: entire UHF band
python3 diagnostic_test.py --passes 3
```

After running, check:
1. Do the `_MAXHOLD.csv` files show signals at Ch 14, 18, 22, 24?
2. Do the `_P20.csv` files show the same signals, or are they at noise floor?
3. Is there a difference between `_WITH_DITHER_P20.csv` and `_NO_DITHER_P20.csv`?

If max-hold shows signals but P20 doesn't, the issue is the percentile export.  
If no-dither shows signals but with-dither doesn't, the issue is the dithering + resampling.
