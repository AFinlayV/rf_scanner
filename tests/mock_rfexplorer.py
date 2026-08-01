"""Mock RF Explorer hardware for testing.

Simulates the RFExplorer Python library's behavior:
  - RFECommunicator (serial connection + sweep streaming)
  - SweepData buffer
  - Individual sweep objects with frequency/amplitude data

Realistic noise model with configurable interference sources.

Mock Fidelity Notes:
  - ProcessReceivedString(True) blocks in the real lib until one serial
    message arrives. Here it immediately generates a sweep from the current
    config. Real device has ~100-500ms latency.
  - SweepData.Count in the real lib tracks all received sweeps since last
    CleanAll(). The mock matches this behavior.
  - TotalDataPoints is an attribute (not method) in the mock. The scanner's
    callable() guard handles both cases.
"""

import numpy as np
from dataclasses import dataclass, field
from types import SimpleNamespace


@dataclass
class InterferenceSource:
    """A simulated RF interference source."""
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    presence_probability: float = 1.0  # 1.0 = always on, 0.5 = 50% of sweeps


class MockSweep:
    """Simulates one sweep from the RF Explorer."""

    def __init__(self, start_mhz: float, end_mhz: float, n_points: int,
                 noise_floor_dbm: float = -110.0, noise_sigma: float = 2.0,
                 interference: list[InterferenceSource] | None = None,
                 rng: np.random.Generator | None = None):
        self._rng = rng or np.random.default_rng()
        self._n_points = n_points
        self._start_mhz = start_mhz
        self._end_mhz = end_mhz
        self._step_mhz = (end_mhz - start_mhz) / max(n_points - 1, 1)

        # Generate amplitude data
        self._amps = self._rng.normal(noise_floor_dbm, noise_sigma, n_points)

        # Apply interference
        if interference:
            freqs = np.linspace(start_mhz, end_mhz, n_points)
            for src in interference:
                if src.presence_probability < 1.0:
                    if self._rng.random() > src.presence_probability:
                        continue  # source not present this sweep
                half_bw = src.bandwidth_mhz / 2
                mask = (freqs >= src.center_mhz - half_bw) & (freqs <= src.center_mhz + half_bw)
                src_power = src.power_dbm + self._rng.normal(0, 1.0, n_points)
                self._amps[mask] = np.maximum(self._amps[mask], src_power[mask])

    # These are attributes (not methods) to match common RFExplorer lib versions.
    # The scanner's callable() guard handles both cases.
    @property
    def TotalDataPoints(self):
        return self._n_points

    @property
    def StartFrequencyMHZ(self):
        return self._start_mhz

    def GetAmplitude_DBM(self, i: int) -> float:
        return float(self._amps[i])

    def GetFrequencyMHZ(self, i: int) -> float:
        return self._start_mhz + i * self._step_mhz


class MockSweepData:
    """Simulates rfe.SweepData — buffer of received sweeps."""

    def __init__(self):
        self._sweeps: list[MockSweep] = []

    @property
    def Count(self) -> int:
        return len(self._sweeps)

    def GetData(self, idx: int) -> MockSweep | None:
        if 0 <= idx < len(self._sweeps):
            return self._sweeps[idx]
        return None

    def CleanAll(self):
        self._sweeps.clear()

    def _add_sweep(self, sweep: MockSweep):
        self._sweeps.append(sweep)


class MockRFECommunicator:
    """Simulates RFExplorer.RFECommunicator."""

    def __init__(
        self,
        noise_floor_dbm: float = -110.0,
        noise_sigma: float = 2.0,
        points_per_sweep: int = 112,
        interference: list[InterferenceSource] | None = None,
        fail_connect: bool = False,
        stale_sweep_count: int = 0,
        timeout_chunks: list[tuple[float, float]] | None = None,
        seed: int = 42,
    ):
        self.AutoConfigure = True  # scanner sets this to False
        self.SweepData = MockSweepData()
        self.MainBoardModel = None

        self._noise_floor = noise_floor_dbm
        self._noise_sigma = noise_sigma
        self._points_per_sweep = points_per_sweep
        self._interference = interference or []
        self._fail_connect = fail_connect
        self._stale_sweep_count = stale_sweep_count
        self._timeout_chunks = timeout_chunks or []
        self._rng = np.random.default_rng(seed)

        self._connected = False
        self._config_start = 470.0
        self._config_end = 472.0
        self._prev_start = 470.0  # for stale sweep simulation
        self._sweeps_since_config = 0
        self._calculator_mode = None

    @property
    def PortConnected(self) -> bool:
        return self._connected

    def ConnectPort(self, port: str, baud: int):
        if self._fail_connect:
            return
        self._connected = True
        self.MainBoardModel = "WSUB1G_Plus"

    def ClosePort(self):
        self._connected = False

    def SendCommand(self, cmd):
        """Mock SendCommand — tracks calculator mode changes."""
        if cmd.startswith("C+") and len(cmd) >= 3:
            mode_val = ord(cmd[2])
            mode_map = {0: "NORMAL", 2: "AVERAGE", 4: "MAX_HOLD"}
            self._calculator_mode = mode_map.get(mode_val, f"UNKNOWN({mode_val})")

    def UpdateDeviceConfig(self, start_mhz: float, end_mhz: float):
        self._prev_start = self._config_start
        self._config_start = start_mhz
        self._config_end = end_mhz
        self._sweeps_since_config = 0

    def ProcessReceivedString(self, flag: bool):
        """Generate a mock sweep based on current config."""
        if not self._connected:
            return

        # Check for timeout chunks
        for ts, te in self._timeout_chunks:
            if abs(self._config_start - ts) < 0.01 and abs(self._config_end - te) < 0.01:
                return  # simulate no response

        # Stale sweep simulation: first N sweeps after config change
        # use the previous frequency range
        if self._sweeps_since_config < self._stale_sweep_count:
            sweep_start = self._prev_start
            sweep_end = self._prev_start + (self._config_end - self._config_start)
        else:
            sweep_start = self._config_start
            sweep_end = self._config_end

        self._sweeps_since_config += 1

        sweep = MockSweep(
            start_mhz=sweep_start,
            end_mhz=sweep_end,
            n_points=self._points_per_sweep,
            noise_floor_dbm=self._noise_floor,
            noise_sigma=self._noise_sigma,
            interference=self._interference,
            rng=self._rng,
        )
        self.SweepData._add_sweep(sweep)


class MockRFE_Common:
    """Simulates RFExplorer.RFE_Common — matches real library enum names."""
    class eCalculator:
        AVG = SimpleNamespace(value=2)
        NORMAL = SimpleNamespace(value=0)
        MAX_HOLD = SimpleNamespace(value=4)

    class eModel:
        MODEL_NONE = 0xFF
        MODEL_WSUB1G = 3
        MODEL_WSUB1G_PLUS = 10


def make_mock_rfexplorer_module(**communicator_kwargs):
    """Create a mock 'RFExplorer' module suitable for sys.modules injection."""
    mod = SimpleNamespace()
    mod.RFE_Common = MockRFE_Common

    # Factory: each call to RFECommunicator() returns a new mock
    # But we store a reference so tests can inspect it
    _instances = []

    def _factory():
        inst = MockRFECommunicator(**communicator_kwargs)
        _instances.append(inst)
        return inst

    mod.RFECommunicator = _factory
    mod._instances = _instances  # test access
    return mod
