"""Shared fixtures for RF Scanner test suite."""

import sys
import os
import importlib
import threading
import queue

import pytest
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.mock_rfexplorer import (
    MockRFECommunicator,
    MockRFE_Common,
    InterferenceSource,
    make_mock_rfexplorer_module,
)


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch):
    """Replace time.sleep with a no-op in scanner to keep tests fast."""
    import time
    _real_sleep = time.sleep
    def _fast_sleep(seconds):
        # Only skip sleeps > 10ms (scanner settle times)
        if seconds > 0.01:
            return
        _real_sleep(seconds)
    monkeypatch.setattr(time, "sleep", _fast_sleep)


@pytest.fixture
def mock_rfe_module():
    """Inject mock RFExplorer module into sys.modules and reload scanner."""
    mock_mod = make_mock_rfexplorer_module()
    rfe_common_mod = type(sys)("RFE_Common")
    rfe_common_mod.eCalculator = MockRFE_Common.eCalculator
    rfe_common_mod.eModel = MockRFE_Common.eModel

    old_rfe = sys.modules.get("RFExplorer")
    old_common = sys.modules.get("RFExplorer.RFE_Common")

    sys.modules["RFExplorer"] = mock_mod
    sys.modules["RFExplorer.RFE_Common"] = rfe_common_mod

    # Reload scanner so it picks up the mock
    import scanner as scanner_mod
    importlib.reload(scanner_mod)

    yield mock_mod, scanner_mod

    # Restore
    if old_rfe is not None:
        sys.modules["RFExplorer"] = old_rfe
    else:
        sys.modules.pop("RFExplorer", None)
    if old_common is not None:
        sys.modules["RFExplorer.RFE_Common"] = old_common
    else:
        sys.modules.pop("RFExplorer.RFE_Common", None)
    importlib.reload(scanner_mod)


@pytest.fixture
def connected_scanner(mock_rfe_module):
    """Return a scanner that's already connected with a mock communicator."""
    mock_mod, scanner_mod = mock_rfe_module
    scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)

    # Directly inject a mock communicator (bypass connect handshake)
    mock_comm = MockRFECommunicator(seed=42)
    mock_comm._connected = True
    mock_comm.MainBoardModel = "WSUB1G_Plus"
    scanner._rfe = mock_comm
    scanner.connected = True

    return scanner, mock_comm, scanner_mod


@pytest.fixture
def scanner_with_interference(mock_rfe_module):
    """Scanner with realistic interference sources injected."""
    mock_mod, scanner_mod = mock_rfe_module
    scanner = scanner_mod.RFExplorerScanner("/dev/mock", 500000)

    sources = [
        InterferenceSource(600.0, 6.0, -30.0, 1.0),       # TV transmitter (always on)
        InterferenceSource(530.0, 0.2, -55.0, 0.4),        # Intermittent walkie
        InterferenceSource(485.0, 10.0, -80.0, 1.0),       # Broadband noise
    ]
    mock_comm = MockRFECommunicator(
        interference=sources, seed=42, noise_floor_dbm=-110.0
    )
    mock_comm._connected = True
    mock_comm.MainBoardModel = "WSUB1G_Plus"
    scanner._rfe = mock_comm
    scanner.connected = True

    return scanner, mock_comm, scanner_mod


@pytest.fixture
def msg_queue():
    return queue.Queue()


@pytest.fixture
def stop_event():
    return threading.Event()


@pytest.fixture
def accumulator():
    from stats import ScanAccumulator
    return ScanAccumulator()
