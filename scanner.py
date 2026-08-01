"""RF Explorer serial communication and scan logic.

Handles:
  - Device connection / disconnection
  - Single-pass sweep (all chunks, with on-device averaging per chunk)
  - 25 kHz grid alignment and resampling for WWB compatibility

The multi-pass loop and statistical aggregation live in the UI layer;
this module only knows how to do one pass at a time.
"""

import threading
import queue
import time
import math
import random
import sys
import os

from constants import BAUD_RATE, WWB_MIN_STEP_MHZ

# Suppress noisy library warnings (e.g. calculator echo parse errors).
# The RFExplorer library prints to stdout when it fails to parse echoed
# commands like "C+\x02".  We redirect stdout to devnull during
# ProcessReceivedString calls.
_devnull = open(os.devnull, "w")

# ─────────────────────────────────────────────────────────
# Dependency checks (soft — we show guidance in the UI)
# ─────────────────────────────────────────────────────────
MISSING_DEPS: list[str] = []

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    MISSING_DEPS.append("pyserial")

try:
    import RFExplorer
    from RFExplorer import RFE_Common
    _RFEX_OK = True
except ImportError:
    _RFEX_OK = False
    MISSING_DEPS.append("RFExplorer")

try:
    import numpy as np
except ImportError:
    MISSING_DEPS.append("numpy")


def list_serial_ports() -> list[str]:
    """List available serial ports, RF Explorer-likely ports first."""
    if "pyserial" in MISSING_DEPS:
        return []
    ports = [p.device for p in serial.tools.list_ports.comports()]
    # CP210x ports show as SLAB_USBtoUART (older driver) or usbserial-XXXX (newer driver)
    likely = [p for p in ports if "SLAB" in p or "usbserial" in p]
    others = [p for p in ports if p not in likely]
    return likely + others


class RFExplorerScanner:
    """
    Wraps the official RFExplorer Python library.
    All blocking work happens in the caller's thread (which should be a
    daemon thread so it doesn't prevent the app from exiting).
    Progress / log messages are delivered via a Queue[dict].
    """

    def __init__(self, port: str, baud: int = BAUD_RATE):
        self.port = port
        self.baud = baud
        self._rfe = None
        self.connected = False

    @property
    def rfe(self):
        """Access the underlying RFE communicator (used by Live Mode)."""
        return self._rfe

    # ── Connection ────────────────────────────────────────

    def connect(self) -> tuple[bool, str]:
        """Attempt connection. Returns (success, human-readable message)."""
        if MISSING_DEPS:
            return False, (
                f"Missing packages: {', '.join(MISSING_DEPS)}\n"
                f"Run:  pip3 install {' '.join(MISSING_DEPS)}"
            )
        try:
            rfe = RFExplorer.RFECommunicator()
            # AutoConfigure=True tells the library to request device config
            # after ConnectPort, which is how we learn the model/firmware.
            rfe.AutoConfigure = True

            # The library's GetConnectedPorts() only accepts ports with
            # "SLAB_USB" in the name on macOS.  Newer Silicon Labs drivers
            # use "usbserial-XXXX" instead, so the port gets rejected.
            # Workaround: inject the port directly into the valid-ports list.
            import serial.tools.list_ports as _slp
            for p in _slp.comports():
                if p.device == self.port:
                    rfe.m_arrValidCP2102Ports = [p]
                    break

            rfe.ConnectPort(self.port, self.baud)

            deadline = time.time() + 10.0
            while time.time() < deadline:
                _real_stdout = sys.stdout
                sys.stdout = _devnull
                try:
                    rfe.ProcessReceivedString(True)
                finally:
                    sys.stdout = _real_stdout
                # Library uses PortConnected (not IsPortConnected)
                # and MainBoardModel (not MainBoard)
                if rfe.PortConnected and rfe.MainBoardModel != RFE_Common.eModel.MODEL_NONE:
                    break
                time.sleep(0.1)

            if not rfe.PortConnected or rfe.MainBoardModel == RFE_Common.eModel.MODEL_NONE:
                rfe.ClosePort()
                return False, (
                    "Device did not respond.\n\n"
                    "- Check USB cable (data-capable, not charge-only)\n"
                    "- On device: Menu > Config > USB Baud > 500K\n"
                    "- Ensure Silicon Labs CP210x driver is installed and allowed\n"
                    "  in System Settings > General > Login Items & Extensions"
                )

            # NOTE: LNA command ("a2") removed — caused device timeouts.
            # Input stage should be set manually on device for now:
            #   Attenuator Menu → Input → LNA

            # Diagnostic: log device state for debugging amplitude offset
            diag_parts = []
            try:
                diag_parts.append(f"InputStage={rfe.InputStage}")
            except Exception:
                diag_parts.append("InputStage=?")
            try:
                diag_parts.append(f"OffsetDB={rfe.AmplitudeOffsetDB}")
            except Exception:
                diag_parts.append("OffsetDB=?")
            try:
                # fOffset_dB from device config — this is the value
                # that gets baked into every amplitude reading by the library
                cfg_offset = getattr(rfe, 'm_fOffset_dB', '?')
                diag_parts.append(f"CfgOffset={cfg_offset}")
            except Exception:
                diag_parts.append("CfgOffset=?")
            try:
                diag_parts.append(f"CalcMode={rfe.Calculator}")
            except Exception:
                diag_parts.append("CalcMode=?")
            diag = " | ".join(diag_parts)

            self._rfe = rfe
            self.connected = True
            model = str(rfe.MainBoardModel) if rfe.MainBoardModel else "RF Explorer"
            return True, f"Connected: {model}\nDevice state: {diag}"

        except Exception as exc:
            return False, str(exc)

    def reset_stream(self) -> int:
        """Discard buffered serial bytes and parsed sweeps. Returns the
        number of stale bytes dropped (-1 if the port couldn't be read).

        Stopping a scan leaves the radio sweeping and streaming, and once
        nothing is reading the port the OS buffer fills and starts dropping
        bytes. The library's parser never re-syncs from a truncated
        message, so every chunk of the *next* scan times out and returns
        zero data — the failure looks like a dead display rather than a
        desynced link. Short gaps between scans are harmless; a few idle
        minutes are not. Flushing at scan start makes stop/start safe,
        which matters because stop/start is what you do all night.
        """
        rfe = self._rfe
        if rfe is None:
            return -1
        dropped = -1
        try:
            with rfe.m_hSerialPortLock:
                dropped = rfe.m_objSerialPort.in_waiting
                rfe.m_objSerialPort.reset_input_buffer()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            rfe.SweepData.CleanAll()
        except Exception:                                   # noqa: BLE001
            pass
        return dropped

    def reconnect(self) -> tuple[bool, str]:
        """Close and reopen the port, redoing the full device handshake.

        Empirically the only thing that revives a radio which has stopped
        answering after an interrupted scan (2026-08-01, measured): merely
        flushing the serial buffer does not — after four idle minutes only
        53 stale bytes were waiting, and every chunk still timed out. What
        works is `connect()`'s handshake, which builds a fresh communicator
        and re-requests the device config. So recovery is a reconnect, not
        a flush.
        """
        try:
            self.disconnect()
        except Exception:                                   # noqa: BLE001
            pass
        time.sleep(0.5)
        return self.connect()

    def disconnect(self):
        if self._rfe:
            try:
                self._rfe.ClosePort()
            except Exception:
                pass
        self._rfe = None
        self.connected = False

    # ── Full single pass ──────────────────────────────────

    def scan_pass(
        self,
        start_mhz: float,
        end_mhz: float,
        chunk_mhz: float,
        iterations: int,
        stop_event: threading.Event,
        msg_q: queue.Queue,
        pass_number: int = 1,
        smooth_window: int = 3,
        ranges: list[tuple[float, float]] | None = None,
    ) -> list[tuple[float, float]]:
        """
        One full pass: sweep the requested spectrum in chunk_mhz-wide slices.

        `ranges` is a list of (start, end) MHz spans — pass several to scan
        disjoint spectrum in one pass (band multiselect, or a single band
        that the 600 MHz repack split in two). It supersedes start_mhz/
        end_mhz, which remain for callers scanning one contiguous span.

        For each slice the device runs `iterations` averaged sweeps.
        Returns a sorted list of (freq_mhz, amp_dbm) at >= 25 kHz resolution.
        Partial data is returned if stop_event fires mid-pass.
        """
        rfe = self._rfe

        # Enable on-device average calculator (only log on first pass)
        if pass_number == 1:
            # Clear anything a previously-interrupted scan left mid-message,
            # or every chunk below times out. See reset_stream().
            stale = self.reset_stream()
            if stale > 0:
                msg_q.put({'type': 'log', 'text':
                           f"Flushed {stale} stale bytes from the serial "
                           f"buffer before starting."})
            try:
                # eCalculator.AVG = 2; send raw command "C+" + mode byte
                rfe.SendCommand("C+" + chr(RFE_Common.eCalculator.AVG.value))
                msg_q.put({'type': 'log', 'text': "Average calculator mode enabled."})
            except Exception:
                msg_q.put({'type': 'log',
                           'text': "Note: could not set Average mode (firmware may not support it)."})

        # ── Normalise to a list of spans ───────────────────────────
        # One contiguous span is just a list of length 1, so everything
        # below has a single code path.
        spans_in = list(ranges) if ranges else [(start_mhz, end_mhz)]

        # ── Snap to 25 kHz grid ────────────────────────────────────
        STEP = WWB_MIN_STEP_MHZ
        chunk_aligned = round(max(STEP, round(chunk_mhz / STEP) * STEP), 3)
        spans = [(round(math.floor(a / STEP) * STEP, 3),
                  round(math.ceil(b / STEP) * STEP, 3)) for a, b in spans_in]

        if pass_number == 1 and (spans, chunk_aligned) != (
                [tuple(s) for s in spans_in], chunk_mhz):
            was = ", ".join(f"{a:g}–{b:g}" for a, b in spans_in)
            now = ", ".join(f"{a:g}–{b:g}" for a, b in spans)
            msg_q.put({'type': 'log', 'text': (
                f"Snapped to 25 kHz grid: {now} MHz, chunk {chunk_aligned} MHz  "
                f"(was {was}, chunk {chunk_mhz})"
            )})
        chunk_mhz = chunk_aligned
        start_mhz, end_mhz = spans[0][0], spans[-1][1]

        # Build chunk list with dithered boundaries.
        # Randomising chunk width ±15% each pass means IF filter rolloff
        # artifacts land at different frequencies, so they average out
        # across passes (spatial dithering for RF).
        # Chunks never straddle a span boundary — a gap between spans is
        # spectrum we were told not to scan, not spectrum to sweep through.
        DITHER = 0.15  # ±15% chunk width variation
        # Each chunk carries its span's hard edges: overlap padding may
        # never spill past them into spectrum we were told to skip.
        chunks: list[tuple[float, float, float, float]] = []
        for span_start, span_end in spans:
            f = span_start
            while f < span_end - STEP / 2:
                # Dither chunk width (skip on pass 1 so estimate is accurate)
                if pass_number > 1:
                    jitter = chunk_mhz * random.uniform(-DITHER, DITHER)
                    c_width = round(max(STEP * 4, chunk_mhz + jitter), 3)
                else:
                    c_width = chunk_mhz
                c_end = round(min(f + c_width, span_end), 3)
                chunks.append((round(f, 3), c_end, span_start, span_end))
                f = c_end
        total = len(chunks)

        span_text = ", ".join(f"{a:.3f}–{b:.3f}" for a, b in spans)
        msg_q.put({'type': 'log', 'text': (
            f"Pass {pass_number}: {span_text} MHz | "
            f"{total} chunks × ~{chunk_mhz} MHz | {iterations} iter/chunk"
            f"{' (dithered)' if pass_number > 1 else ''}"
        )})

        # Accumulate raw data for this pass
        accum: dict[float, list[float]] = {}

        # Overlap padding: widen each chunk so the edge bins trimmed by
        # EDGE_TRIM are covered by the adjacent chunk.
        # EDGE_TRIM=10 at ~58 kHz/bin = ~580 kHz removed from each end.
        # OVERLAP must exceed that or gaps appear at chunk boundaries.
        OVERLAP_MHZ = 1.0

        for idx, (c_start, c_end, span_lo, span_hi) in enumerate(chunks):
            if stop_event.is_set():
                msg_q.put({'type': 'log', 'text': f"Pass {pass_number} interrupted at chunk {idx+1}/{total}."})
                break

            pct = int(100 * idx / total)
            msg_q.put({
                'type': 'progress',
                'value': pct,
                'text': f"Pass {pass_number} | Chunk {idx+1}/{total}: {c_start:.1f}–{c_end:.1f} MHz",
            })

            # Pad chunk edges, clamped to this chunk's own span — a span
            # boundary is a hard edge, not a chunk seam to blend across.
            padded_start = round(max(span_lo, c_start - OVERLAP_MHZ), 3)
            padded_end   = round(min(span_hi, c_end   + OVERLAP_MHZ), 3)

            chunk_points = self._scan_chunk(
                rfe, padded_start, padded_end, iterations, stop_event, msg_q
            )
            if chunk_points:
                # Diagnostic: log raw values from first chunk of first pass
                if pass_number == 1 and idx == 0:
                    sample = chunk_points[:5]
                    min_amp = min(a for _, a in chunk_points)
                    max_amp = max(a for _, a in chunk_points)
                    msg_q.put({'type': 'log', 'text': (
                        f"DIAG first chunk raw: min={min_amp:.1f} max={max_amp:.1f} dBm | "
                        f"samples: {[(f'{f:.3f}', f'{a:.1f}') for f, a in sample]}"
                    )})
                for freq, amp in chunk_points:
                    key = round(freq, 3)
                    accum.setdefault(key, []).append(amp)
                # Send chunk data for live visualization
                msg_q.put({
                    'type': 'chunk_vis',
                    'data': chunk_points,
                    'chunk_idx': idx,
                    'total_chunks': total,
                })

        if not accum:
            return []

        # Average within-chunk overlaps in linear power domain
        raw_points = []
        for freq, vals in sorted(accum.items()):
            mw_sum = sum(10.0 ** (v / 10.0) for v in vals)
            avg_dbm = 10.0 * math.log10(max(mw_sum / len(vals), 1e-20))
            raw_points.append((freq, round(avg_dbm, 1)))

        # Smooth at native device resolution before resampling.
        # Target ~160 kHz smoothing bandwidth regardless of chunk size.
        # Native bin spacing = chunk_mhz / 112 points.
        # window is caller-supplied (default 3); if default, auto-scale to
        # keep smoothing bandwidth constant as chunk size changes.
        if smooth_window == 3:
            bin_khz = (chunk_mhz * 1000.0) / 112.0
            target_khz = 160.0
            smooth_window = max(3, int(round(target_khz / bin_khz)))
            if smooth_window % 2 == 0:
                smooth_window += 1  # keep odd for symmetric window
        raw_points = self._smooth(raw_points, window=smooth_window)

        result = self._resample(raw_points, WWB_MIN_STEP_MHZ)

        msg_q.put({'type': 'log', 'text': (
            f"Pass {pass_number} {'partial — ' if stop_event.is_set() else ''}"
            f"{len(result)} data points."
        )})
        return result

    # ── Single chunk ──────────────────────────────────────

    def _scan_chunk(
        self,
        rfe,
        start_mhz: float,
        end_mhz: float,
        iterations: int,
        stop_event: threading.Event,
        msg_q: queue.Queue,
    ) -> list[tuple[float, float]] | None:
        """Scan one frequency chunk. Returns [(freq_mhz, amp_dbm), ...] or None."""

        # Clear stale sweep data
        try:
            rfe.SweepData.CleanAll()
        except Exception:
            pass

        # Configure the device for this chunk
        try:
            rfe.UpdateDeviceConfig(start_mhz, end_mhz)
        except Exception as exc:
            msg_q.put({'type': 'log', 'text': f"  Config error {start_mhz:.1f}–{end_mhz:.1f} MHz: {exc}"})
            return None

        # Brief pause for LO lock, then flush initial sweeps to let the
        # detector/AGC circuitry stabilize at the new frequency.  The first
        # 1-2 valid sweeps after a retune often show amplitude bias.
        time.sleep(0.1)

        # ── Flush phase: discard SETTLE_FLUSH valid sweeps ──
        SETTLE_FLUSH = 2
        flushed = 0
        flush_deadline = time.time() + 3.0
        while flushed < SETTLE_FLUSH and not stop_event.is_set():
            if time.time() > flush_deadline:
                break
            _real_stdout = sys.stdout
            sys.stdout = _devnull
            try:
                rfe.ProcessReceivedString(True)
            finally:
                sys.stdout = _real_stdout
            count = rfe.SweepData.Count
            if count < 1:
                continue
            sweep = rfe.SweepData.GetData(count - 1)
            if sweep is None:
                continue
            sw_start = sweep.StartFrequencyMHZ() if callable(sweep.StartFrequencyMHZ) else sweep.StartFrequencyMHZ
            if abs(sw_start - start_mhz) > 1.0:
                # Still stale — flush and keep waiting
                try:
                    rfe.SweepData.CleanAll()
                except Exception:
                    pass
                continue
            # Valid sweep at correct frequency — discard it (circuit settling)
            flushed += 1
            try:
                rfe.SweepData.CleanAll()
            except Exception:
                pass

        # ── Collection phase: gather `iterations` sweeps ──
        sweep_arrays: list[list[float]] = []
        n_points = 0
        deadline = time.time() + max(iterations * 1.5 + 2.0, 5.0)

        while len(sweep_arrays) < iterations and not stop_event.is_set():
            if time.time() > deadline:
                if not sweep_arrays:
                    msg_q.put({'type': 'log', 'text': f"  Timeout on {start_mhz:.1f}–{end_mhz:.1f} MHz — skipping."})
                    return None
                break  # got some sweeps, proceed with what we have

            # Suppress library print noise (echo parse warnings)
            _real_stdout = sys.stdout
            sys.stdout = _devnull
            try:
                rfe.ProcessReceivedString(True)
            finally:
                sys.stdout = _real_stdout

            count = rfe.SweepData.Count
            if count < 1:
                continue

            sweep = rfe.SweepData.GetData(count - 1)
            if sweep is None:
                continue

            # Handle property vs method (version-dependent)
            n_pts = sweep.TotalDataPoints() if callable(sweep.TotalDataPoints) else sweep.TotalDataPoints
            if n_pts < 1:
                continue

            sw_start = sweep.StartFrequencyMHZ() if callable(sweep.StartFrequencyMHZ) else sweep.StartFrequencyMHZ
            if abs(sw_start - start_mhz) > 1.0:
                # Stale sweep from previous range — flush and retry immediately
                try:
                    rfe.SweepData.CleanAll()
                except Exception:
                    pass
                continue

            amps = [sweep.GetAmplitude_DBM(i) for i in range(n_pts)]
            sweep_arrays.append(amps)
            n_points = n_pts

            try:
                rfe.SweepData.CleanAll()
            except Exception:
                pass

        if not sweep_arrays:
            return None

        # Average amplitude values across sweeps in LINEAR power domain.
        # Averaging dBm directly (log domain) produces a biased-low result
        # by ~2.5 dB for noise (Jensen's inequality).
        n_sweeps = len(sweep_arrays)
        averaged = []
        for i in range(n_points):
            mw_sum = sum(
                10.0 ** (s[i] / 10.0)
                for s in sweep_arrays if i < len(s)
            )
            avg_dbm = 10.0 * math.log10(max(mw_sum / n_sweeps, 1e-20))
            averaged.append(avg_dbm)

        # Map indices to frequencies.
        # RF Explorer distributes n_points across the span with
        # step = span / (n_points - 1), so the last point lands on end_mhz.
        step_mhz = (end_mhz - start_mhz) / max(n_points - 1, 1)

        # Trim edge bins — IF filter rolloff degrades amplitude accuracy
        # at the edges of each sweep, causing chunk-boundary ripple.
        EDGE_TRIM = 10
        if n_points > 2 * EDGE_TRIM + 10:
            averaged = averaged[EDGE_TRIM:n_points - EDGE_TRIM]
            first_idx = EDGE_TRIM
        else:
            first_idx = 0

        return [
            (round(start_mhz + (first_idx + i) * step_mhz, 4), round(amp, 1))
            for i, amp in enumerate(averaged)
        ]

    # ── Resampling ────────────────────────────────────────

    @staticmethod
    def _resample(
        points: list[tuple[float, float]],
        step_mhz: float,
    ) -> list[tuple[float, float]]:
        """Bin (freq, amp) data into step_mhz-wide bins. Ensures >= 25 kHz step for WWB.

        Averaging is done in linear power domain to avoid log-domain bias.
        """
        if not points:
            return points

        bins: dict[float, list[float]] = {}
        for freq, amp in points:
            key = round(round(freq / step_mhz) * step_mhz, 3)
            bins.setdefault(key, []).append(amp)

        result = []
        for freq, amps in sorted(bins.items()):
            mw_sum = sum(10.0 ** (a / 10.0) for a in amps)
            avg_dbm = 10.0 * math.log10(max(mw_sum / len(amps), 1e-20))
            result.append((freq, round(avg_dbm, 1)))
        return result

    @staticmethod
    def _smooth(
        points: list[tuple[float, float]],
        window: int = 3,
    ) -> list[tuple[float, float]]:
        """Moving-average smoothing in linear power domain.

        Uses a symmetric window — edges keep their original values.
        This reduces noise spikiness without smearing narrow signals.
        """
        if len(points) < window or window < 2:
            return points

        half = window // 2
        result = []
        for i, (freq, amp) in enumerate(points):
            if i < half or i >= len(points) - half:
                result.append((freq, amp))
                continue
            neighbors = points[i - half : i + half + 1]
            mw_sum = sum(10.0 ** (a / 10.0) for _, a in neighbors)
            avg_dbm = 10.0 * math.log10(max(mw_sum / len(neighbors), 1e-20))
            result.append((freq, round(avg_dbm, 1)))
        return result
