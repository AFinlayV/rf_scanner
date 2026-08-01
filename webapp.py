"""webapp.py — RF Scanner's browser frontend.

Why this exists: the Tk desktop UI has macOS focus/scroll bugs (window needs
a titlebar click before it accepts input; panes don't scroll). This serves
the same scan engine over HTTP so it can be driven from a phone, tablet or
another laptop.

**This must run on the machine the RF Explorer is plugged into.** It is a USB
serial device; a remote host has no radio. Run it on the Mac (or on a small
box at the gig) and reach it over the LAN or Tailscale.

The engine — scanner.py, stats.py, export.py, bands.py — is imported
unchanged and knows nothing about either frontend. ui.py still works.

Run:   python3 webapp.py            → http://localhost:8080
Env:   PORT, PASSCODE (empty = off), SECRET_KEY, DEBUG=1
"""
from __future__ import annotations

import io
import os
import queue
import secrets
import threading
import time

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import bands as bandlib
from constants import APP_TITLE, APP_VERSION, BAUD_RATE
from export import default_filename, save_wwb_csv
from scanner import MISSING_DEPS, RFExplorerScanner, list_serial_ports
from stats import ScanAccumulator

PORT = int(os.environ.get("PORT", "8080"))
PASSCODE = os.environ.get("PASSCODE", "")
LOG_LIMIT = 200

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)


# ──────────────────────────────────────────────────────────────── live bus ──

class Bus:
    """In-process pub/sub; every open browser holds one SSE connection."""

    def __init__(self):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self, event: str = "state", data: str = "") -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass  # slow client catches up on its next full-state fetch


bus = Bus()


# ─────────────────────────────────────────────────────────────────── state ──

class Session:
    """One RF Explorer, one scan at a time. Single-operator by design."""

    def __init__(self):
        self.lock = threading.RLock()
        self.scanner: RFExplorerScanner | None = None
        self.accumulator = ScanAccumulator()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.port = ""
        self.connected = False
        self.scanning = False
        self.pass_number = 0
        self.progress = 0
        self.status = "Not connected"
        self.log: list[str] = []
        self.spectrum_dirty = True          # tells clients to refetch the plot
        self.selected_bands: list[str] = []
        self.ranges: list[tuple[float, float]] = [(470.0, 608.0)]
        self.chunk = 6.0
        self.iterations = 1

    # -- logging ----------------------------------------------------------
    def write_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.log.append(f"{stamp}  {text}")
            del self.log[:-LOG_LIMIT]

    # -- snapshot ---------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            spans = [[a, b] for a, b in self.ranges]
            width = bandlib.total_width(self.ranges)
            return {
                "connected": self.connected,
                "port": self.port,
                "scanning": self.scanning,
                "pass_number": self.pass_number,
                "pass_count": self.accumulator.pass_count,
                "bin_count": self.accumulator.bin_count,
                "progress": self.progress,
                "status": self.status,
                "log": self.log[-60:],
                "spectrum_dirty": self.spectrum_dirty,
                "selected_bands": list(self.selected_bands),
                "ranges": spans,
                "total_mhz": round(width, 3),
                "chunk": self.chunk,
                "iterations": self.iterations,
                "noise_floor": self.noise_floor(),
                "missing_deps": list(MISSING_DEPS),
            }

    def noise_floor(self) -> float | None:
        """Same model the Tk UI shows: base + RBW gain + averaging gain."""
        import math
        try:
            rbw_khz = (self.chunk * 1000.0) / 112.0
            rbw_gain = 10.0 * math.log10(200.0 / max(rbw_khz, 0.1))
            avg_gain = 10.0 * math.log10(max(self.accumulator.pass_count, 1))
            return round(-110.0 - rbw_gain - avg_gain, 1)
        except (ValueError, ZeroDivisionError):
            return None


S = Session()


def push_state() -> None:
    bus.publish("state", "1")


# ────────────────────────────────────────────────────────────── scan worker ──

def scan_worker(ranges, chunk, iterations):
    """Mirrors the Tk app's scan loop, but publishes to the SSE bus.

    The engine reports through a plain queue; this drains it and folds the
    messages into session state.
    """
    msg_q: queue.Queue = queue.Queue()
    pass_num = 0
    start_env, end_env = ranges[0][0], ranges[-1][1]

    def drain():
        while True:
            try:
                m = msg_q.get_nowait()
            except queue.Empty:
                return
            kind = m.get("type")
            if kind == "log":
                S.write_log(m["text"])
            elif kind == "progress":
                with S.lock:
                    S.progress = m.get("value", 0)
                    S.status = m.get("text", "")

    try:
        while not S.stop_event.is_set():
            pass_num += 1
            with S.lock:
                S.pass_number = pass_num
            data = S.scanner.scan_pass(
                start_env, end_env, chunk, iterations,
                S.stop_event, msg_q, pass_num, ranges=ranges,
            )
            drain()
            if data:
                with S.lock:
                    S.accumulator.add_pass(data)
                    S.spectrum_dirty = True
                    S.progress = 100
                    S.status = (f"Pass {pass_num} complete · "
                                f"{S.accumulator.bin_count} bins")
            push_state()
    except Exception as exc:                       # noqa: BLE001 - surface it
        S.write_log(f"Scan error: {exc}")
        with S.lock:
            S.status = f"Error: {exc}"
    finally:
        drain()
        with S.lock:
            S.scanning = False
            if not S.status.startswith("Error"):
                S.status = (f"Stopped after {S.accumulator.pass_count} pass"
                            f"{'es' if S.accumulator.pass_count != 1 else ''}")
        S.write_log("Scan stopped.")
        push_state()


def state_pusher():
    """While scanning, nudge clients ~2×/sec so progress and log stay live."""
    while True:
        time.sleep(0.5)
        if S.scanning:
            push_state()


threading.Thread(target=state_pusher, daemon=True).start()


# ──────────────────────────────────────────────────────────── passcode gate ──

@app.before_request
def gate():
    if not PASSCODE:
        return None
    if request.endpoint in ("gate_page", "static", "healthz", None):
        return None
    if session.get("ok"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "locked"}), 403
    return redirect(url_for("gate_page", next=request.path))


@app.route("/gate", methods=["GET", "POST"])
def gate_page():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("passcode", ""), PASSCODE):
            session["ok"] = True
            return redirect(request.args.get("next") or "/")
        return render_template("gate.html", error=True), 403
    return render_template("gate.html", error=False)


@app.route("/healthz")
def healthz():
    return {"ok": True}


# ───────────────────────────────────────────────────────────────── the page ──

@app.route("/")
def index():
    return render_template("index.html", title=APP_TITLE, version=APP_VERSION)


@app.route("/events")
def events():
    def stream():
        q = bus.subscribe()
        try:
            yield ": hello\n\n"
            while True:
                try:
                    event, data = q.get(timeout=20)
                    yield f"event: {event}\ndata: {data}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ─────────────────────────────────────────────────────────────────────  API ──

@app.get("/api/state")
def api_state():
    return jsonify(S.snapshot())


@app.get("/api/ports")
def api_ports():
    return jsonify({"ports": list_serial_ports()})


@app.get("/api/bands")
def api_bands():
    out = []
    for b in bandlib.BANDS:
        out.append({
            "label": b.label, "name": b.name, "maker": b.maker,
            "family": b.family, "spans": [[a, c] for a, c in b.ranges],
            "span_text": b.span_text, "us_legal": b.us_legal, "note": b.note,
        })
    return jsonify({"bands": out,
                    "presets": {k: v for k, v in bandlib.FREQ_PRESETS.items() if v}})


@app.post("/api/connect")
def api_connect():
    port = (request.json or {}).get("port", "").strip()
    if not port:
        return jsonify({"error": "No port given"}), 400
    with S.lock:
        if S.scanning:
            return jsonify({"error": "Stop the scan first"}), 409
        if S.scanner and S.connected:
            try:
                S.scanner.disconnect()
            except Exception:                       # noqa: BLE001
                pass
        S.scanner = RFExplorerScanner(port, BAUD_RATE)
    ok, msg = S.scanner.connect()
    with S.lock:
        S.connected = ok
        S.port = port if ok else ""
        S.status = msg if not ok else f"Connected on {port}"
    S.write_log(msg)
    push_state()
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 502)


@app.post("/api/disconnect")
def api_disconnect():
    with S.lock:
        if S.scanning:
            return jsonify({"error": "Stop the scan first"}), 409
        if S.scanner:
            try:
                S.scanner.disconnect()
            except Exception:                       # noqa: BLE001
                pass
        S.connected = False
        S.port = ""
        S.status = "Not connected"
    S.write_log("Disconnected.")
    push_state()
    return jsonify({"ok": True})


@app.post("/api/scan/start")
def api_scan_start():
    body = request.json or {}
    with S.lock:
        if not (S.scanner and S.connected):
            return jsonify({"error": "Not connected"}), 409
        if S.scanning:
            return jsonify({"error": "Already scanning"}), 409

    band_labels = [b for b in body.get("bands", []) if b in bandlib.BY_LABEL]
    if band_labels:
        ranges = bandlib.ranges_for(band_labels)
    else:
        try:
            start = float(body.get("start"))
            end = float(body.get("end"))
        except (TypeError, ValueError):
            return jsonify({"error": "Start/End must be numbers"}), 400
        if start >= end:
            return jsonify({"error": "Start must be below End"}), 400
        ranges = [(start, end)]

    try:
        chunk = float(body.get("chunk", 6.0))
        iterations = int(body.get("iterations", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "Chunk/iterations must be numbers"}), 400
    if chunk <= 0:
        return jsonify({"error": "Chunk must be greater than 0"}), 400

    with S.lock:
        S.accumulator.clear()
        S.stop_event.clear()
        S.selected_bands = band_labels
        S.ranges = ranges
        S.chunk = chunk
        S.iterations = iterations
        S.scanning = True
        S.pass_number = 0
        S.progress = 0
        S.spectrum_dirty = True
        S.status = "Starting scan…"

    if band_labels:
        names = ", ".join(bandlib.BY_LABEL[b].name for b in band_labels)
        S.write_log(f"Scanning bands: {names}")
    span_txt = ", ".join(f"{a:g}–{b:g}" for a, b in ranges)
    S.write_log(f"Spectrum: {span_txt} MHz "
                f"({bandlib.total_width(ranges):g} MHz total)")

    S.worker = threading.Thread(target=scan_worker,
                                args=(ranges, chunk, iterations), daemon=True)
    S.worker.start()
    push_state()
    return jsonify({"ok": True})


@app.post("/api/scan/stop")
def api_scan_stop():
    S.stop_event.set()
    with S.lock:
        S.status = "Stopping after this chunk…"
    S.write_log("Stop requested.")
    push_state()
    return jsonify({"ok": True})


def _export_data(mode: str):
    """(points, label) for an export mode: 'MAX' or 'P<n>'."""
    with S.lock:
        if S.accumulator.pass_count == 0:
            return [], mode
        if mode.upper() in ("MAX", "MAX-HOLD"):
            return S.accumulator.export_max(), "MAX"
        try:
            pct = int(mode.lstrip("Pp"))
        except ValueError:
            pct = 20
        return S.accumulator.export_percentile(pct), f"P{pct}"


@app.get("/api/spectrum")
def api_spectrum():
    mode = request.args.get("mode", "P20")
    points, label = _export_data(mode)
    with S.lock:
        S.spectrum_dirty = False
        spans = [[a, b] for a, b in S.ranges]
        passes = S.accumulator.pass_count
    return jsonify({"points": points, "mode": label,
                    "spans": spans, "passes": passes})


@app.post("/api/export")
def api_export():
    """Save a copy to the Desktop (parity with the Tk app)."""
    mode = (request.json or {}).get("mode", "P20")
    points, label = _export_data(mode)
    if not points:
        return jsonify({"error": "Run a scan first"}), 400
    with S.lock:
        spans, passes = S.ranges, S.accumulator.pass_count
    name = default_filename(start_mhz=spans[0][0], end_mhz=spans[-1][1],
                            passes=passes, label=label)
    path = os.path.join(os.path.expanduser("~/Desktop"), name)
    try:
        n = save_wwb_csv(path, points)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    S.write_log(f"Saved {n} points ({label}, {passes} passes) → {name}")
    push_state()
    return jsonify({"ok": True, "path": path, "filename": name, "points": n})


@app.get("/api/export/download")
def api_export_download():
    """Stream the CSV to whatever device is browsing."""
    mode = request.args.get("mode", "P20")
    points, label = _export_data(mode)
    if not points:
        return jsonify({"error": "Run a scan first"}), 400
    with S.lock:
        spans, passes = S.ranges, S.accumulator.pass_count
    name = default_filename(start_mhz=spans[0][0], end_mhz=spans[-1][1],
                            passes=passes, label=label)
    buf = io.BytesIO()
    buf.write("".join(f"{f:.3f},{a:.1f}\n" for f, a in points).encode())
    buf.seek(0)
    return send_file(buf, mimetype="text/csv",
                     as_attachment=True, download_name=name)


if __name__ == "__main__":
    print(f"{APP_TITLE} {APP_VERSION} — web UI on http://localhost:{PORT}")
    if MISSING_DEPS:
        print(f"  ! missing deps: {', '.join(MISSING_DEPS)}")
    if not PASSCODE:
        print("  ! no PASSCODE set — fine on a trusted LAN/tailnet")
    app.run(host="0.0.0.0", port=PORT,
            debug=os.environ.get("DEBUG") == "1", threaded=True)
