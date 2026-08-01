"""Shared constants for RF Coordinator."""

APP_TITLE   = "RF Scanner"
APP_VERSION = "2.0.0"
BAUD_RATE   = 500_000

FREQ_PRESETS = {
    "Sub-GHz  470–900 MHz": (470.0, 900.0),
    "UHF      470–698 MHz": (470.0, 698.0),
    "UHF      470–608 MHz": (470.0, 608.0),
    "VHF      174–216 MHz": (174.0, 216.0),
    "Custom":               None,
}

# WWB requires >= 25 kHz step between adjacent data points
WWB_MIN_STEP_MHZ = 0.025
