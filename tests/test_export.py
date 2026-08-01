"""Tests for WWB CSV export."""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from export import save_wwb_csv, default_filename


class TestSaveWWBCSV:
    def test_format(self, tmp_path):
        data = [(470.000, -109.0), (470.025, -107.5), (470.050, -105.3)]
        path = str(tmp_path / "test.csv")
        save_wwb_csv(path, data)
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 3
        # No header
        assert "freq" not in lines[0].lower()
        # Check format: freq.3f,amp.1f
        for line in lines:
            parts = line.strip().split(",")
            assert len(parts) == 2
            assert re.match(r"^\d+\.\d{3}$", parts[0])
            assert re.match(r"^-?\d+\.\d{1}$", parts[1])

    def test_frequency_precision(self, tmp_path):
        data = [(470.0, -100.0), (470.025, -95.0)]
        path = str(tmp_path / "test.csv")
        save_wwb_csv(path, data)
        with open(path) as f:
            lines = f.readlines()
        assert lines[0].startswith("470.000,")
        assert lines[1].startswith("470.025,")

    def test_amplitude_precision(self, tmp_path):
        data = [(470.0, -109.0)]
        path = str(tmp_path / "test.csv")
        save_wwb_csv(path, data)
        with open(path) as f:
            content = f.read().strip()
        assert content == "470.000,-109.0"

    def test_returns_count(self, tmp_path):
        data = [(470.0 + i * 0.025, -100.0) for i in range(50)]
        path = str(tmp_path / "test.csv")
        n = save_wwb_csv(path, data)
        assert n == 50

    def test_empty_data(self, tmp_path):
        path = str(tmp_path / "test.csv")
        n = save_wwb_csv(path, [])
        assert n == 0
        assert os.path.exists(path)
        with open(path) as f:
            assert f.read() == ""

    def test_25khz_step_compliance(self, tmp_path):
        data = [(470.0 + i * 0.025, -100.0 + i * 0.1) for i in range(100)]
        path = str(tmp_path / "test.csv")
        save_wwb_csv(path, data)
        with open(path) as f:
            freqs = [float(line.split(",")[0]) for line in f.readlines()]
        for i in range(1, len(freqs)):
            step = round(freqs[i] - freqs[i-1], 4)
            assert abs(step - 0.025) < 1e-6


class TestDefaultFilename:
    def test_format(self):
        name = default_filename()
        assert name.startswith("rf_scan_")
        assert name.endswith(".csv")
        assert re.match(r"^rf_scan_\d{8}_\d{6}\.csv$", name)

    def test_custom_prefix(self):
        name = default_filename("venue_scan")
        assert name.startswith("venue_scan_")
        assert name.endswith(".csv")
