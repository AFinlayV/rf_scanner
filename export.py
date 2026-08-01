"""WWB CSV export.

Format: headerless, two columns — freq_MHz,amp_dBm
  470.000,-109.0
  470.025,-107.5

Import in WWB: Scan Data -> Add Scan From File
"""

import datetime


def save_wwb_csv(path: str, data: list[tuple[float, float]]) -> int:
    """Write WWB-compatible CSV. Returns number of data points written."""
    with open(path, "w") as fh:
        for freq, amp in data:
            fh.write(f"{freq:.3f},{amp:.1f}\n")
    return len(data)


def default_filename(
    prefix: str = "rf_scan",
    start_mhz: float | None = None,
    end_mhz: float | None = None,
    passes: int | None = None,
    percentile: int | None = None,
    label: str | None = None,
) -> str:
    """Generate a descriptive timestamped filename.

    Example: rf_scan_500-650MHz_3passes_MAX_20260322_103045.csv
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [prefix]
    if start_mhz is not None and end_mhz is not None:
        parts.append(f"{start_mhz:.0f}-{end_mhz:.0f}MHz")
    if passes is not None:
        parts.append(f"{passes}pass{'es' if passes != 1 else ''}")
    tag = label if label is not None else (f"P{percentile}" if percentile is not None else None)
    if tag is not None:
        parts.append(tag)
    parts.append(ts)
    return "_".join(parts) + ".csv"
