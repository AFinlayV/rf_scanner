"""Statistical accumulation and analysis for multi-pass RF scan data.

Each scan pass adds one amplitude reading per frequency bin. Over N passes,
each bin accumulates a distribution of N values.  At export time we compute
per-bin statistics (percentiles, std, min, max) to produce an honest picture
of the RF environment rather than a single-moment snapshot.
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class BinStats:
    """Statistical summary for a single frequency bin."""
    freq_mhz: float
    mean: float
    std: float
    min_val: float
    max_val: float
    median: float
    percentile_10: float
    percentile_25: float
    percentile_90: float
    percentile_95: float
    sample_count: int


class ScanAccumulator:
    """
    Accumulates amplitude readings across multiple scan passes.

    After N passes each bin has N values from which we compute:
      - Nth percentile  (WWB export — configurable, default ~20th)
      - 90th/95th       (worst-case interference)
      - Std deviation   (burstiness — high std = intermittent interference)
      - Min / max       (display only)
    """

    def __init__(self):
        self._data: dict[float, list[float]] = {}
        self._pass_count = 0
        self._freq_keys: list[float] | None = None

    @property
    def pass_count(self) -> int:
        return self._pass_count

    @property
    def bin_count(self) -> int:
        return len(self._data)

    @property
    def frequencies(self) -> list[float]:
        if self._freq_keys is None:
            self._freq_keys = sorted(self._data.keys())
        return self._freq_keys

    def add_pass(self, data: list[tuple[float, float]]):
        """Add one complete (or partial) pass of (freq_mhz, amp_dbm) data."""
        for freq, amp in data:
            self._data.setdefault(freq, []).append(amp)
        self._pass_count += 1
        self._freq_keys = None  # invalidate sorted cache

    def get_bin_stats(self, freq: float) -> BinStats | None:
        """Get full statistics for a single frequency bin."""
        values = self._data.get(freq)
        if not values:
            return None
        arr = np.array(values)
        return BinStats(
            freq_mhz=freq,
            mean=float(np.mean(arr)),
            std=float(np.std(arr)),
            min_val=float(np.min(arr)),
            max_val=float(np.max(arr)),
            median=float(np.median(arr)),
            percentile_10=float(np.percentile(arr, 10)),
            percentile_25=float(np.percentile(arr, 25)),
            percentile_90=float(np.percentile(arr, 90)),
            percentile_95=float(np.percentile(arr, 95)),
            sample_count=len(values),
        )

    def export_percentile(self, percentile: float) -> list[tuple[float, float]]:
        """
        Export (freq, amp) using the Nth percentile at each bin.
        Primary export method for WWB CSV.
        """
        result = []
        for freq in self.frequencies:
            values = self._data[freq]
            if len(values) == 1:
                amp = values[0]
            else:
                amp = float(np.percentile(values, percentile))
            result.append((freq, round(amp, 1)))
        return result

    def export_max(self) -> list[tuple[float, float]]:
        """Export using max-hold (worst case interference)."""
        return [
            (freq, round(max(self._data[freq]), 1))
            for freq in self.frequencies
        ]

    def export_min(self) -> list[tuple[float, float]]:
        """Export using min-hold (best case — quietest moment)."""
        return [
            (freq, round(min(self._data[freq]), 1))
            for freq in self.frequencies
        ]

    def clear(self):
        """Reset all accumulated data."""
        self._data.clear()
        self._pass_count = 0
        self._freq_keys = None
