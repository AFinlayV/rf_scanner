"""Integration tests — full scan-to-export pipeline."""

import threading
import queue
import os
import sys

import pytest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stats import ScanAccumulator
from export import save_wwb_csv
from tests.mock_rfexplorer import InterferenceSource, MockRFECommunicator


class TestMultiPassLoop:
    def test_continuous_scan_three_passes(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        pass_results = []

        def _work():
            pass_num = 0
            while not stop_event.is_set():
                pass_num += 1
                data = scanner.scan_pass(
                    470.0, 474.0, 2.0, 3,
                    stop_event, msg_queue, pass_num
                )
                if data:
                    msg_queue.put({
                        'type': 'pass_complete',
                        'data': data,
                        'pass_number': pass_num,
                    })
                if pass_num >= 3:
                    stop_event.set()
            msg_queue.put({'type': 'scan_stopped'})

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(timeout=30)
        assert not t.is_alive(), "Scan thread didn't finish"

        # Drain queue
        messages = []
        while not msg_queue.empty():
            messages.append(msg_queue.get())

        pass_completes = [m for m in messages if m.get('type') == 'pass_complete']
        assert len(pass_completes) == 3
        for pc in pass_completes:
            assert len(pc['data']) > 0

    def test_continuous_scan_stop_mid_pass(self, connected_scanner, msg_queue, stop_event):
        scanner, mock_comm, _ = connected_scanner
        call_count = [0]
        orig_update = mock_comm.UpdateDeviceConfig

        def _counting_update(start, end):
            call_count[0] += 1
            # Stop during second pass (after ~6 chunks total — first pass has 2 chunks)
            if call_count[0] >= 4:
                stop_event.set()
            return orig_update(start, end)

        mock_comm.UpdateDeviceConfig = _counting_update

        def _work():
            pass_num = 0
            try:
                while not stop_event.is_set():
                    pass_num += 1
                    data = scanner.scan_pass(
                        470.0, 474.0, 2.0, 3,
                        stop_event, msg_queue, pass_num
                    )
                    if data:
                        msg_queue.put({
                            'type': 'pass_complete',
                            'data': data,
                            'pass_number': pass_num,
                        })
                msg_queue.put({'type': 'scan_stopped'})
            except Exception as exc:
                msg_queue.put({'type': 'error', 'msg': str(exc)})

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(timeout=30)

        messages = []
        while not msg_queue.empty():
            messages.append(msg_queue.get())

        # Should have at least 1 complete pass
        pass_completes = [m for m in messages if m.get('type') == 'pass_complete']
        assert len(pass_completes) >= 1

    def test_full_pipeline_scan_to_csv(self, connected_scanner, msg_queue, stop_event, tmp_path):
        scanner, _, _ = connected_scanner
        acc = ScanAccumulator()

        # Run 3 passes
        for pass_num in range(1, 4):
            data = scanner.scan_pass(
                470.0, 474.0, 2.0, 3,
                stop_event, msg_queue, pass_num
            )
            assert len(data) > 0
            acc.add_pass(data)

        assert acc.pass_count == 3
        assert acc.bin_count > 0

        # Export P20 to CSV
        export_data = acc.export_percentile(20)
        csv_path = str(tmp_path / "test_scan.csv")
        n = save_wwb_csv(csv_path, export_data)
        assert n == len(export_data)
        assert n > 0

        # Read back and validate
        with open(csv_path) as f:
            lines = f.readlines()
        assert len(lines) == n
        freqs = []
        for line in lines:
            parts = line.strip().split(",")
            freq = float(parts[0])
            amp = float(parts[1])
            freqs.append(freq)
            assert 460 < freq < 480
            assert -140 < amp < 0

        # Verify 25 kHz step
        for i in range(1, len(freqs)):
            step = round(freqs[i] - freqs[i-1], 4)
            assert abs(step - 0.025) < 0.001

    def test_accumulator_reflects_interference(self, scanner_with_interference, msg_queue, stop_event):
        scanner, _, _ = scanner_with_interference
        acc = ScanAccumulator()

        # The TV is at 600 MHz. Scan a range that covers it and a quiet zone.
        # The interference sources are: TV at 600 (bw 6, always on),
        # walkie at 530 (bw 0.2, 40%), broadband 485 (bw 10).
        # So 470-474 should be quiet (well below 480 broadband start).
        for pass_num in range(1, 11):
            # Scan the interference zone
            data_loud = scanner.scan_pass(
                598.0, 602.0, 2.0, 3,
                stop_event, msg_queue, pass_num
            )
            # Scan a quiet zone
            data_quiet = scanner.scan_pass(
                470.0, 474.0, 2.0, 3,
                stop_event, msg_queue, pass_num
            )
            if data_loud:
                acc.add_pass(data_loud)
            if data_quiet:
                acc.add_pass(data_quiet)

        freqs = acc.frequencies
        interference_bin = min(freqs, key=lambda f: abs(f - 600.0))
        quiet_bin = min(freqs, key=lambda f: abs(f - 472.0))

        p20_data = dict(acc.export_percentile(20))

        # The TV at 600 MHz (-30 dBm) should be much louder than
        # the quiet bin near 472 MHz (noise floor ~-110 dBm)
        assert p20_data[interference_bin] > p20_data[quiet_bin] + 40


class TestEdgeCases:
    def test_very_narrow_range(self, connected_scanner, msg_queue, stop_event):
        scanner, _, _ = connected_scanner
        result = scanner.scan_pass(470.0, 470.050, 0.050, 3, stop_event, msg_queue)
        assert len(result) >= 2

    def test_wide_range_no_gaps(self, connected_scanner, msg_queue, stop_event):
        scanner, _, _ = connected_scanner
        # 10 MHz range = 5 chunks of 2 MHz
        result = scanner.scan_pass(470.0, 480.0, 2.0, 2, stop_event, msg_queue)
        assert len(result) > 0
        freqs = sorted([f for f, a in result])
        # The gap this test exists to catch is a chunk seam the overlap
        # padding failed to cover — that would show up as hundreds of kHz.
        # A skipped 25 kHz slot is not that: the radio's native bins are
        # ~36 kHz at this chunk width, so the grid is finer than the
        # measurement and some slots are legitimately empty.
        for i in range(1, len(freqs)):
            gap = round(freqs[i] - freqs[i-1], 4)
            assert 0.025 <= gap <= 0.050, \
                f"Gap of {gap} MHz between {freqs[i-1]} and {freqs[i]}"

    def test_callable_vs_property_sweep_attributes(self, mock_rfe_module, msg_queue, stop_event):
        """Verify the callable() guard works for both attribute and method styles."""
        _, scanner_mod = mock_rfe_module
        scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)

        # MockSweep uses properties (not methods) — test that the scanner handles it
        mock_comm = MockRFECommunicator(seed=42)
        mock_comm._connected = True
        mock_comm.MainBoardModel = "WSUB1G_Plus"
        scanner._rfe = mock_comm
        scanner.connected = True

        result = scanner.scan_pass(470.0, 472.0, 2.0, 3, stop_event, msg_queue)
        assert len(result) > 0
