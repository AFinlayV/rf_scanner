"""Shared constants for RF Scanner."""

APP_TITLE   = "RF Scanner"
APP_VERSION = "2.1.0"
BAUD_RATE   = 500_000

# FREQ_PRESETS moved to bands.py, which owns all spectrum definitions
# (manufacturer bands + the plain contiguous quick-picks).

# WWB requires >= 25 kHz step between adjacent data points
WWB_MIN_STEP_MHZ = 0.025
