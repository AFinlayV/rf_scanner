"""Tests for ScanAccumulator — multi-pass statistical aggregation."""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stats import ScanAccumulator, BinStats


class TestAddPass:
    def test_add_single_pass(self, accumulator):
        data = [(470.0 + i * 0.025, -100.0) for i in range(100)]
        accumulator.add_pass(data)
        assert accumulator.pass_count == 1
        assert accumulator.bin_count == 100
        assert accumulator.frequencies == sorted([d[0] for d in data])

    def test_add_multiple_passes(self, accumulator):
        data = [(470.0 + i * 0.025, -100.0 + i * 0.1) for i in range(50)]
        for _ in range(5):
            accumulator.add_pass(data)
        assert accumulator.pass_count == 5
        assert accumulator.bin_count == 50

    def test_add_partial_pass(self, accumulator):
        full = [(470.0 + i * 0.025, -100.0) for i in range(100)]
        partial = [(470.0 + i * 0.025, -95.0) for i in range(50)]
        accumulator.add_pass(full)
        accumulator.add_pass(partial)
        assert accumulator.pass_count == 2
        assert accumulator.bin_count == 100
        # First 50 bins have 2 samples, last 50 have 1
        stats_early = accumulator.get_bin_stats(470.0)
        stats_late = accumulator.get_bin_stats(470.0 + 99 * 0.025)
        assert stats_early.sample_count == 2
        assert stats_late.sample_count == 1

    def test_frequencies_cache_invalidation(self, accumulator):
        accumulator.add_pass([(470.0, -100.0)])
        freqs1 = accumulator.frequencies
        assert freqs1 == [470.0]
        accumulator.add_pass([(471.0, -100.0)])
        freqs2 = accumulator.frequencies
        assert 471.0 in freqs2


class TestExportPercentile:
    def test_single_pass_returns_raw(self, accumulator):
        data = [(470.0, -105.0), (470.025, -98.3)]
        accumulator.add_pass(data)
        result = accumulator.export_percentile(20)
        assert result == [(470.0, -105.0), (470.025, -98.3)]

    def test_p20_multiple_passes(self, accumulator):
        values = [-100.0, -95.0, -90.0, -85.0, -80.0,
                  -75.0, -70.0, -65.0, -60.0, -55.0]
        for v in values:
            accumulator.add_pass([(500.0, v)])
        result = accumulator.export_percentile(20)
        expected = round(float(np.percentile(values, 20)), 1)
        assert result[0] == (500.0, expected)

    def test_p50_is_median(self, accumulator):
        rng = np.random.default_rng(123)
        values = rng.normal(-100, 5, 20).tolist()
        for v in values:
            accumulator.add_pass([(470.0, v)])
        result = accumulator.export_percentile(50)
        expected = round(float(np.median(values)), 1)
        assert result[0][1] == expected

    def test_conservative_ordering(self, accumulator):
        rng = np.random.default_rng(456)
        values = rng.normal(-90, 10, 50).tolist()
        for v in values:
            accumulator.add_pass([(470.0, v)])
        p10 = accumulator.export_percentile(10)[0][1]
        p50 = accumulator.export_percentile(50)[0][1]
        p90 = accumulator.export_percentile(90)[0][1]
        assert p10 < p50 < p90

    def test_rounding(self, accumulator):
        for v in [-100.123, -95.678, -90.999]:
            accumulator.add_pass([(470.0, v)])
        result = accumulator.export_percentile(50)
        amp = result[0][1]
        assert amp == round(amp, 1)


class TestExportMinMax:
    def test_export_max(self, accumulator):
        for v in [-100.0, -90.0, -80.0]:
            accumulator.add_pass([(470.0, v)])
        result = accumulator.export_max()
        assert result == [(470.0, -80.0)]

    def test_export_min(self, accumulator):
        for v in [-100.0, -90.0, -80.0]:
            accumulator.add_pass([(470.0, v)])
        result = accumulator.export_min()
        assert result == [(470.0, -100.0)]

    def test_single_pass_min_max_equal(self, accumulator):
        accumulator.add_pass([(470.0, -95.0)])
        assert accumulator.export_min() == accumulator.export_max()


class TestBinStats:
    def test_get_bin_stats_existing(self, accumulator):
        rng = np.random.default_rng(789)
        values = rng.normal(-100, 5, 10).tolist()
        for v in values:
            accumulator.add_pass([(470.0, v)])
        stats = accumulator.get_bin_stats(470.0)
        arr = np.array(values)
        assert stats.sample_count == 10
        assert abs(stats.mean - float(np.mean(arr))) < 0.01
        assert abs(stats.std - float(np.std(arr))) < 0.01
        assert abs(stats.min_val - float(np.min(arr))) < 0.01
        assert abs(stats.max_val - float(np.max(arr))) < 0.01
        assert abs(stats.median - float(np.median(arr))) < 0.01
        assert abs(stats.percentile_10 - float(np.percentile(arr, 10))) < 0.01
        assert abs(stats.percentile_25 - float(np.percentile(arr, 25))) < 0.01
        assert abs(stats.percentile_90 - float(np.percentile(arr, 90))) < 0.01
        assert abs(stats.percentile_95 - float(np.percentile(arr, 95))) < 0.01

    def test_get_bin_stats_nonexistent(self, accumulator):
        accumulator.add_pass([(470.0, -100.0)])
        assert accumulator.get_bin_stats(999.0) is None

    def test_get_bin_stats_single_sample(self, accumulator):
        accumulator.add_pass([(470.0, -100.0)])
        stats = accumulator.get_bin_stats(470.0)
        assert stats.std == 0.0
        assert stats.min_val == stats.max_val == stats.mean == stats.median == -100.0


class TestClear:
    def test_clear_resets_everything(self, accumulator):
        for _ in range(5):
            accumulator.add_pass([(470.0, -100.0)])
        accumulator.clear()
        assert accumulator.pass_count == 0
        assert accumulator.bin_count == 0
        assert accumulator.frequencies == []
