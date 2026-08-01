"""Tests for RFExplorerScanner — serial comms + scan logic."""

import threading
import queue
import sys
import os

import pytest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.mock_rfexplorer import MockRFECommunicator, InterferenceSource


class TestConnection:
    def test_connect_success(self, mock_rfe_module):
        mock_mod, scanner_mod = mock_rfe_module
        scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)
        ok, msg = scanner.connect()
        assert ok is True
        assert "Connected" in msg
        assert scanner.connected is True
        assert scanner.rfe is not None

    def test_connect_timeout(self, mock_rfe_module):
        mock_mod, scanner_mod = mock_rfe_module
        # Patch the module to create a fail_connect communicator
        orig_factory = mock_mod.RFECommunicator

        def _fail_factory():
            comm = MockRFECommunicator(fail_connect=True)
            mock_mod._instances.append(comm)
            return comm

        mock_mod.RFECommunicator = _fail_factory
        scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)
        ok, msg = scanner.connect()
        assert ok is False
        assert scanner.connected is False
        mock_mod.RFECommunicator = orig_factory

    def test_disconnect(self, connected_scanner):
        scanner, mock_comm, _ = connected_scanner
        assert scanner.connected is True
        scanner.disconnect()
        assert scanner.connected is False
        assert scanner.rfe is None
        assert mock_comm._connected is False


class TestScanChunk:
    def test_basic(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        result = scanner._scan_chunk(
            mock_comm, 470.0, 472.0, 5, stop_event, msg_queue
        )
        assert result is not None
        assert len(result) > 0
        freqs = [f for f, a in result]
        assert min(freqs) >= 470.0 - 0.01
        assert max(freqs) <= 472.0 + 0.01
        amps = [a for f, a in result]
        assert all(-140 < a < 0 for a in amps)

    def test_averages_iterations(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        # With deterministic seed, results should be consistent
        result = scanner._scan_chunk(
            mock_comm, 470.0, 472.0, 10, stop_event, msg_queue
        )
        assert result is not None
        # Each point should be averaged across 10 sweeps.
        # Edge trimming removes 10 bins from each side → 112 - 20 = 92
        EDGE_TRIM = 10
        assert len(result) == mock_comm._points_per_sweep - 2 * EDGE_TRIM

    def test_stale_sweep_rejection(self, mock_rfe_module, msg_queue, stop_event):
        _, scanner_mod = mock_rfe_module
        scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)
        # 2 stale sweeps before valid data
        mock_comm = MockRFECommunicator(stale_sweep_count=2, seed=42)
        mock_comm._connected = True
        mock_comm.MainBoardModel = "WSUB1G_Plus"
        scanner._rfe = mock_comm
        scanner.connected = True

        result = scanner._scan_chunk(
            mock_comm, 480.0, 482.0, 3, stop_event, msg_queue
        )
        assert result is not None
        freqs = [f for f, a in result]
        # Should have frequencies in the 480-482 range (not stale)
        assert min(freqs) >= 479.9
        assert max(freqs) <= 482.1

    def test_timeout_no_data(self, mock_rfe_module, msg_queue, stop_event):
        _, scanner_mod = mock_rfe_module
        scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)
        mock_comm = MockRFECommunicator(
            timeout_chunks=[(470.0, 472.0)], seed=42
        )
        mock_comm._connected = True
        scanner._rfe = mock_comm
        scanner.connected = True

        result = scanner._scan_chunk(
            mock_comm, 470.0, 472.0, 5, stop_event, msg_queue
        )
        assert result is None

    def test_config_error(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        # Make UpdateDeviceConfig raise
        def _raise(*args):
            raise RuntimeError("Config failed")
        mock_comm.UpdateDeviceConfig = _raise

        result = scanner._scan_chunk(
            mock_comm, 470.0, 472.0, 5, stop_event, msg_queue
        )
        assert result is None
        # Check error was logged
        messages = []
        while not msg_queue.empty():
            messages.append(msg_queue.get())
        assert any("Config error" in m.get("text", "") for m in messages)


class TestScanPass:
    def test_single_chunk(self, connected_scanner, msg_queue, stop_event):
        scanner, _, _ = connected_scanner
        result = scanner.scan_pass(470.0, 472.0, 2.0, 3, stop_event, msg_queue)
        assert len(result) > 0
        freqs = [f for f, a in result]
        assert min(freqs) >= 470.0
        assert max(freqs) <= 472.0

    def test_multiple_chunks(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        result = scanner.scan_pass(470.0, 478.0, 2.0, 3, stop_event, msg_queue)
        assert len(result) > 0
        freqs = [f for f, a in result]
        assert min(freqs) >= 470.0
        assert max(freqs) <= 478.0
        # Should cover full range without big gaps
        for i in range(1, len(freqs)):
            gap = freqs[i] - freqs[i-1]
            assert gap <= 0.026  # 25 kHz step with tiny floating point tolerance

    def test_grid_alignment(self, connected_scanner, msg_queue, stop_event):
        scanner, _, _ = connected_scanner
        # Non-aligned inputs
        result = scanner.scan_pass(470.013, 474.987, 2.1, 3, stop_event, msg_queue)
        assert len(result) > 0
        for freq, amp in result:
            # Every frequency should be on the 25 kHz grid
            remainder = round(freq / 0.025, 6) % 1
            assert remainder < 0.001 or remainder > 0.999, \
                f"Frequency {freq} not on 25 kHz grid"

    def test_stop_event_mid_scan(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        call_count = [0]
        orig_update = mock_comm.UpdateDeviceConfig

        def _counting_update(start, end):
            call_count[0] += 1
            if call_count[0] >= 3:  # stop after 2 chunks
                stop_event.set()
            return orig_update(start, end)

        mock_comm.UpdateDeviceConfig = _counting_update

        result = scanner.scan_pass(470.0, 480.0, 2.0, 3, stop_event, msg_queue)
        # Should have partial data (not all 5 chunks)
        assert len(result) > 0
        freqs = [f for f, a in result]
        assert max(freqs) < 480.0  # didn't finish full range

    def test_resampling_25khz(self, connected_scanner, msg_queue, stop_event):
        scanner, _, _ = connected_scanner
        result = scanner.scan_pass(470.0, 472.0, 2.0, 3, stop_event, msg_queue)
        for freq, amp in result:
            remainder = round((freq / 0.025) % 1, 6)
            assert remainder < 0.001 or remainder > 0.999

    def test_first_pass_sets_calculator(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        scanner.scan_pass(470.0, 472.0, 2.0, 3, stop_event, msg_queue, pass_number=1)
        assert mock_comm._calculator_mode == "AVERAGE"

    def test_subsequent_pass_skips_calculator(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        scanner.scan_pass(470.0, 472.0, 2.0, 3, stop_event, msg_queue, pass_number=2)
        # On pass 2, SetCalculator should NOT be called
        assert mock_comm._calculator_mode is None


class TestResample:
    def test_identity(self, connected_scanner):
        scanner, _, scanner_mod = connected_scanner
        points = [(470.0, -100.0), (470.025, -95.0), (470.050, -90.0)]
        result = scanner_mod.RFExplorerScanner._resample(points, 0.025)
        assert result == points

    def test_averaging(self, connected_scanner):
        scanner, _, scanner_mod = connected_scanner
        # Two points that both round to the 470.025 bin
        points = [(470.030, -100.0), (470.035, -90.0)]
        result = scanner_mod.RFExplorerScanner._resample(points, 0.025)
        assert len(result) == 1
        assert result[0][0] == 470.025  # both snap to nearest 25 kHz
        # Power-domain average: 10·log10((10^(-100/10) + 10^(-90/10)) / 2) ≈ -92.6
        assert result[0][1] == -92.6

    def test_empty(self, connected_scanner):
        scanner, _, scanner_mod = connected_scanner
        result = scanner_mod.RFExplorerScanner._resample([], 0.025)
        assert result == []

    def test_uneven_spacing(self, connected_scanner):
        scanner, _, scanner_mod = connected_scanner
        points = [(470.001, -100.0), (470.030, -95.0), (470.070, -90.0)]
        result = scanner_mod.RFExplorerScanner._resample(points, 0.025)
        for freq, amp in result:
            remainder = round((freq / 0.025) % 1, 6)
            assert remainder < 0.001 or remainder > 0.999
