"""Main application window for RF Scanner.

Continuous multi-pass scanning with statistical aggregation.
The scanner runs passes in a loop until the user hits Stop;
each pass feeds into a ScanAccumulator that builds per-bin
distributions for percentile-based export.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import queue
import time
import math
import os
import datetime

from constants import APP_TITLE, APP_VERSION, BAUD_RATE, WWB_MIN_STEP_MHZ
import bands as bandlib
from bands import FREQ_PRESETS
from scanner import RFExplorerScanner, MISSING_DEPS, list_serial_ports
from stats import ScanAccumulator
from export import save_wwb_csv, default_filename

# Check for Live Mode dependencies (matplotlib is optional)
try:
    import numpy as np
    import matplotlib
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


def _format_elapsed(seconds: float) -> str:
    """Human-readable elapsed time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}:{s:02d}"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m:02d}m"


class RFScannerApp:
    PAD = 10

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_TITLE}  v{APP_VERSION}")
        self.root.resizable(True, True)
        self.root.minsize(660, 880)

        self._scanner: RFExplorerScanner | None = None
        self._stop_event = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._accumulator = ScanAccumulator()
        self._scan_start_time: float | None = None
        self._scanning = False
        # Band multiselect: labels from bands.BY_LABEL. Empty = scan the
        # plain start/end range instead.
        self._selected_bands: list[str] = []
        self._suppress_band_clear = False

        self._build_ui()
        self._refresh_ports()
        self._pump_queue()

        if MISSING_DEPS:
            self.root.after(300, self._warn_missing_deps)

    # ── Build UI ──────────────────────────────────────────

    def _build_ui(self):
        p = self.PAD
        self.root.columnconfigure(0, weight=1)

        # ── Connection ──────────────────────────────────
        cf = ttk.LabelFrame(self.root, text="Connection", padding=p)
        cf.grid(row=0, column=0, sticky="ew", padx=p, pady=(p, 0))
        cf.columnconfigure(1, weight=1)

        ttk.Label(cf, text="Port:").grid(row=0, column=0, sticky="w")
        self._port_var = tk.StringVar()
        self._port_cb = ttk.Combobox(cf, textvariable=self._port_var,
                                     width=30, state="readonly")
        self._port_cb.grid(row=0, column=1, sticky="ew", padx=(4, 4))

        ttk.Button(cf, text="Refresh", width=7, command=self._refresh_ports
                   ).grid(row=0, column=2, padx=(0, 6))
        self._conn_btn = ttk.Button(cf, text="Connect",
                                    command=self._toggle_connect, width=12)
        self._conn_btn.grid(row=0, column=3)

        self._conn_label = ttk.Label(cf, text="● Disconnected",
                                     foreground="#c0392b")
        self._conn_label.grid(row=0, column=4, padx=(10, 0))

        # ── Scan Parameters ─────────────────────────────
        pf = ttk.LabelFrame(self.root, text="Scan Parameters", padding=p)
        pf.grid(row=1, column=0, sticky="ew", padx=p, pady=(p, 0))

        ttk.Label(pf, text="Preset:").grid(row=0, column=0, sticky="w", pady=3)
        self._preset_var = tk.StringVar(value="Sub-GHz  470–900 MHz")
        preset_cb = ttk.Combobox(pf, textvariable=self._preset_var,
                                  values=list(FREQ_PRESETS.keys()),
                                  width=22, state="readonly")
        preset_cb.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3,
                       padx=(4, 0))
        preset_cb.bind("<<ComboboxSelected>>", self._on_preset)

        ttk.Button(pf, text="Bands…", command=self._open_band_picker
                   ).grid(row=0, column=3, sticky="ew", pady=3, padx=(6, 0))

        # Summary of the current band multiselect (blank when scanning a
        # plain start/end range).
        self._bandsel_var = tk.StringVar(value="")
        ttk.Label(pf, textvariable=self._bandsel_var, foreground="#58a6ff",
                  wraplength=330, justify="left"
                  ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))

        ttk.Label(pf, text="Start (MHz):").grid(row=1, column=0, sticky="w", pady=3)
        self._start_var = tk.StringVar(value="470")
        ttk.Entry(pf, textvariable=self._start_var, width=10
                  ).grid(row=1, column=1, sticky="w", padx=(4, 16), pady=3)

        ttk.Label(pf, text="End (MHz):").grid(row=1, column=2, sticky="w", pady=3)
        self._end_var = tk.StringVar(value="900")
        ttk.Entry(pf, textvariable=self._end_var, width=10
                  ).grid(row=1, column=3, sticky="w", padx=(4, 0), pady=3)

        ttk.Label(pf, text="Chunk (MHz):").grid(row=2, column=0, sticky="w", pady=3)
        self._chunk_var = tk.StringVar(value="6")
        ttk.Entry(pf, textvariable=self._chunk_var, width=10
                  ).grid(row=2, column=1, sticky="w", padx=(4, 16), pady=3)

        ttk.Label(pf, text="Iterations/chunk:").grid(row=2, column=2, sticky="w", pady=3)
        self._iter_var = tk.IntVar(value=1)
        ttk.Spinbox(pf, from_=1, to=50, textvariable=self._iter_var, width=8
                    ).grid(row=2, column=3, sticky="w", padx=(4, 0), pady=3)

        self._est_var = tk.StringVar()
        ttk.Label(pf, textvariable=self._est_var, foreground="gray"
                  ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        for v in (self._start_var, self._end_var, self._chunk_var, self._iter_var):
            v.trace_add("write", lambda *_: self._update_estimate())
        # Hand-editing Start/End means the operator wants that literal range,
        # so it drops any band multiselect (last edit wins). The picker sets
        # these itself, hence the guard.
        for v in (self._start_var, self._end_var):
            v.trace_add("write", lambda *_: self._on_range_typed())
        self._update_estimate()

        # ── Controls ────────────────────────────────────
        ctrl = ttk.Frame(self.root, padding=(p, p // 2))
        ctrl.grid(row=2, column=0, sticky="ew", padx=p, pady=(p // 2, 0))
        ctrl.columnconfigure(0, weight=1)
        ctrl.columnconfigure(1, weight=1)
        ctrl.columnconfigure(2, weight=1)

        self._scan_btn = ttk.Button(ctrl, text="Start Scan",
                                    command=self._start_scan, state="disabled")
        self._scan_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self._stop_btn = ttk.Button(ctrl, text="Stop",
                                    command=self._stop_scan, state="disabled")
        self._stop_btn.grid(row=0, column=1, sticky="ew", padx=(3, 3))

        self._live_btn = ttk.Button(ctrl, text="Live Mode",
                                    command=self._open_live_mode, state="disabled")
        self._live_btn.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        self._prog_var = tk.IntVar(value=0)
        self._prog_bar = ttk.Progressbar(ctrl, variable=self._prog_var,
                                         maximum=100)
        self._prog_bar.grid(row=1, column=0, columnspan=3, sticky="ew",
                            pady=(8, 0))

        self._status_var = tk.StringVar(value="Connect to RF Explorer to begin.")
        ttk.Label(ctrl, textvariable=self._status_var, anchor="w"
                  ).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        # ── Results ─────────────────────────────────────
        rf = ttk.LabelFrame(self.root, text="Results", padding=p)
        rf.grid(row=3, column=0, sticky="ew", padx=p, pady=(p, 0))
        rf.columnconfigure(0, weight=1)

        self._result_var = tk.StringVar(value="No scan data yet.")
        ttk.Label(rf, textvariable=self._result_var
                  ).grid(row=0, column=0, columnspan=3, sticky="w")

        self._nf_var = tk.StringVar(value="")
        ttk.Label(rf, textvariable=self._nf_var, foreground="gray"
                  ).grid(row=1, column=0, columnspan=3, sticky="w")

        # Percentile control
        pct_frame = ttk.Frame(rf)
        pct_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Label(pct_frame, text="Export mode:").pack(side="left")
        self._export_mode_var = tk.StringVar(value="Max-hold")
        _export_modes = ["Max-hold", "P95", "P80", "P50", "P20"]
        ttk.Combobox(pct_frame, textvariable=self._export_mode_var,
                     values=_export_modes, width=10, state="readonly"
                     ).pack(side="left", padx=(4, 8))
        self._export_hint = tk.StringVar(value="Peak across all passes — best for coordination")
        ttk.Label(pct_frame, textvariable=self._export_hint,
                  foreground="gray").pack(side="left")
        self._export_mode_var.trace_add("write", lambda *_: self._update_export_hint())

        self._save_btn = ttk.Button(rf, text="Save CSV...",
                                    command=self._save_csv, state="disabled")
        self._save_btn.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # ── Spectrum Plot ──────────────────────────────────
        self._plot_frame = None
        self._fig = None
        self._ax = None
        self._canvas = None
        self._scan_line = None
        self._accum_line = None
        self._fill = None
        if _MPL_OK:
            self._build_plot(p)

        # ── Log ─────────────────────────────────────────
        log_row = 5 if _MPL_OK else 4
        lf = ttk.LabelFrame(self.root, text="Log", padding=p)
        lf.grid(row=log_row, column=0, sticky="nsew", padx=p, pady=p)
        if not _MPL_OK:
            self.root.rowconfigure(log_row, weight=1)
        else:
            self.root.rowconfigure(log_row, weight=0)
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

        self._log = scrolledtext.ScrolledText(
            lf, height=6, state="disabled",
            font=("Menlo", 11), wrap="word",
            background="#1e1e1e", foreground="#d4d4d4",
            insertbackground="white",
            takefocus=0,
        )
        self._log.grid(row=0, column=0, sticky="nsew")

        # ── Driver help link ─────────────────────────────
        info = ttk.Label(
            self.root,
            text="No port?  Install Silicon Labs CP210x driver → silabs.com/developers/usb-to-uart-bridge-vcp-drivers",
            foreground="gray", font=("Helvetica", 10),
        )
        info.grid(row=log_row + 1, column=0, sticky="w", padx=p, pady=(0, p // 2))

    # ── Spectrum plot ─────────────────────────────────────

    def _build_plot(self, pad):
        """Create the embedded matplotlib spectrum plot."""
        matplotlib.use("TkAgg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        self._plot_frame = ttk.LabelFrame(self.root, text="Spectrum", padding=2)
        self._plot_frame.grid(row=4, column=0, sticky="nsew", padx=pad, pady=(pad, 0))
        self.root.rowconfigure(4, weight=3)
        self._plot_frame.rowconfigure(0, weight=1)
        self._plot_frame.columnconfigure(0, weight=1)

        self._fig = Figure(figsize=(7, 3), dpi=100, facecolor="#0d1117")
        self._ax = self._fig.add_subplot(111)

        # Dark theme styling
        self._ax.set_facecolor("#0d1117")
        self._ax.set_xlabel("Frequency (MHz)", color="#8b949e", fontsize=9)
        self._ax.set_ylabel("dBm", color="#8b949e", fontsize=9)
        self._ax.tick_params(colors="#8b949e", labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color("#30363d")
        self._ax.grid(True, color="#21262d", linewidth=0.5, alpha=0.7)
        self._ax.set_xlim(470, 900)
        self._ax.set_ylim(-130, -20)

        self._fig.tight_layout(pad=1.5)

        self._canvas = FigureCanvasTkAgg(self._fig, master=self._plot_frame)
        self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self._canvas.draw()

        # Plot data storage
        self._vis_freqs = []
        self._vis_amps = []
        self._max_hold: dict[float, float] = {}   # freq → max dBm across passes
        self._prev_pass_freqs = []                 # last completed pass
        self._prev_pass_amps = []

    def _style_ax(self):
        """Apply dark theme to the axes."""
        self._ax.set_facecolor("#0d1117")
        self._ax.set_xlabel("Frequency (MHz)", color="#8b949e", fontsize=9)
        self._ax.set_ylabel("dBm", color="#8b949e", fontsize=9)
        self._ax.tick_params(colors="#8b949e", labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color("#30363d")
        self._ax.grid(True, color="#21262d", linewidth=0.5, alpha=0.7)

    def _auto_ylim(self, *amp_lists):
        """Set Y axis limits from one or more amplitude lists."""
        all_amps = [a for lst in amp_lists if lst for a in lst]
        if all_amps:
            self._ax.set_ylim(min(all_amps) - 5, max(max(all_amps) + 10, -20))

    def _update_plot_chunk(self, chunk_data):
        """Add a chunk's worth of data to the live spectrum plot."""
        if not self._ax:
            return

        self._vis_freqs.extend([f for f, a in chunk_data])
        self._vis_amps.extend([a for f, a in chunk_data])

        pairs = sorted(zip(self._vis_freqs, self._vis_amps))
        freqs = [f for f, a in pairs]
        amps = [a for f, a in pairs]

        self._ax.clear()
        self._style_ax()

        # Layer 1: previous pass (dim)
        if self._prev_pass_freqs:
            self._ax.fill_between(self._prev_pass_freqs, self._prev_pass_amps,
                                  -140, color="#00d4ff", alpha=0.04)
            self._ax.plot(self._prev_pass_freqs, self._prev_pass_amps,
                          color="#00d4ff", linewidth=0.5, alpha=0.25)

        # Layer 2: max hold (orange, smooth)
        if self._max_hold:
            mf = sorted(self._max_hold.keys())
            ma = [self._max_hold[f] for f in mf]
            self._ax.plot(mf, ma, color="#ff9500", linewidth=1.2, alpha=0.8,
                          label="Max hold")

        # Layer 3: current pass building up (bright cyan)
        self._ax.fill_between(freqs, amps, -140, color="#00d4ff", alpha=0.08)
        self._ax.plot(freqs, amps, color="#00d4ff", linewidth=0.8, alpha=0.9)

        n = self._accumulator.pass_count
        if n > 0:
            self._ax.set_title(
                f"Pass {n + 1} scanning…",
                color="#58a6ff", fontsize=9, pad=4)

        self._auto_ylim(amps, list(self._max_hold.values()))
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

    def _update_plot_pass_complete(self):
        """Update max hold and redraw after a pass completes."""
        if not self._ax or self._accumulator.pass_count == 0:
            return

        data, label = self._get_export_data()
        if not data:
            return

        # Update max hold
        for freq, amp in data:
            if freq not in self._max_hold or amp > self._max_hold[freq]:
                self._max_hold[freq] = amp

        # Store this pass as the "previous" for next pass's background
        self._prev_pass_freqs = [f for f, a in data]
        self._prev_pass_amps = [a for f, a in data]

        # Redraw with all layers
        self._ax.clear()
        self._style_ax()

        freqs = [f for f, a in data]
        amps = [a for f, a in data]

        # Layer 1: current export mode fill
        self._ax.fill_between(freqs, amps, min(amps) - 5,
                              color="#00d4ff", alpha=0.10)
        self._ax.plot(freqs, amps, color="#00d4ff", linewidth=0.8, alpha=0.7,
                      label=label)

        # Layer 2: max hold (orange)
        if self._max_hold:
            mf = sorted(self._max_hold.keys())
            ma = [self._max_hold[f] for f in mf]
            self._ax.plot(mf, ma, color="#ff9500", linewidth=1.2, alpha=0.8,
                          label="Max hold")

        n = self._accumulator.pass_count
        self._ax.set_title(
            f"{label} across {n} pass{'es' if n != 1 else ''} | max hold",
            color="#58a6ff", fontsize=9, pad=4)
        self._ax.legend(loc="upper right", fontsize=7,
                        facecolor="#0d1117", edgecolor="#30363d",
                        labelcolor="#8b949e")

        self._auto_ylim(amps, list(self._max_hold.values()))
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

    def _clear_plot(self):
        """Reset the spectrum plot for a new scan."""
        self._vis_freqs = []
        self._vis_amps = []
        self._max_hold = {}
        self._prev_pass_freqs = []
        self._prev_pass_amps = []
        if not self._ax:
            return
        self._ax.clear()
        self._style_ax()
        self._canvas.draw_idle()

    def _clear_plot_scan_data(self):
        """Clear in-progress chunk data so the next pass starts fresh."""
        self._vis_freqs = []
        self._vis_amps = []

    # ── Log helper ────────────────────────────────────────

    def _write_log(self, text: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.config(state="normal")
        self._log.insert(tk.END, f"[{ts}] {text}\n")
        self._log.see(tk.END)
        self._log.config(state="disabled")
        self.root.focus_set()

    # ── Port helpers ─────────────────────────────────────

    def _refresh_ports(self):
        ports = list_serial_ports()
        self._port_cb["values"] = ports
        if ports and not self._port_var.get():
            self._port_cb.current(0)

    # ── Preset ───────────────────────────────────────────

    def _on_preset(self, _event=None):
        val = FREQ_PRESETS.get(self._preset_var.get())
        if val:
            # A preset is a plain contiguous range — it replaces any band
            # multiselect rather than combining with it.
            self._clear_bands()
            self._start_var.set(str(val[0]))
            self._end_var.set(str(val[1]))

    # ── Band multiselect ─────────────────────────────────

    def _clear_bands(self):
        self._selected_bands = []
        self._bandsel_var.set("")

    def _on_range_typed(self):
        if self._selected_bands and not self._suppress_band_clear:
            self._clear_bands()

    def _current_ranges(self) -> list[tuple[float, float]]:
        """The spectrum to scan: selected bands, else the start/end range."""
        if self._selected_bands:
            return bandlib.ranges_for(self._selected_bands)
        try:
            return [(float(self._start_var.get()), float(self._end_var.get()))]
        except (ValueError, tk.TclError):
            return []

    def _open_band_picker(self):
        """Checklist of manufacturer bands; selection becomes the scan set."""
        win = tk.Toplevel(self.root)
        win.title("Select Bands")
        win.transient(self.root)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        us_only = tk.BooleanVar(value=True)
        summary = tk.StringVar()

        top = ttk.Frame(win, padding=(10, 8, 10, 0))
        top.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(top, text="US-usable only (post-600 MHz repack)",
                        variable=us_only,
                        command=lambda: rebuild()).pack(anchor="w")

        # Scrollable checklist.
        mid = ttk.Frame(win, padding=(10, 6))
        mid.grid(row=1, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        canvas = tk.Canvas(mid, height=340, highlightthickness=0, width=430)
        scroll = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        vars_by_label: dict[str, tk.BooleanVar] = {}

        def update_summary():
            picked = [lab for lab, v in vars_by_label.items() if v.get()]
            if not picked:
                summary.set("Nothing selected — scan uses the Start/End range.")
                return
            spans = bandlib.ranges_for(picked)
            width = bandlib.total_width(spans)
            span_txt = ", ".join(f"{a:g}–{b:g}" for a, b in spans)
            overlap = sum(bandlib.total_width(list(bandlib.BY_LABEL[p].ranges))
                          for p in picked) - width
            note = f"  (overlap merged: {overlap:g} MHz saved)" if overlap > 0.01 else ""
            summary.set(f"{len(picked)} band(s) → {span_txt} MHz  "
                        f"= {width:g} MHz total{note}")

        def rebuild():
            for child in inner.winfo_children():
                child.destroy()
            vars_by_label.clear()
            shown = bandlib.us_bands() if us_only.get() else bandlib.BANDS
            last_group = None
            for band in shown:
                group = f"{band.maker} — {band.family}"
                if group != last_group:
                    ttk.Label(inner, text=group, foreground="#8b949e"
                              ).pack(anchor="w", pady=(8, 2))
                    last_group = group
                var = tk.BooleanVar(value=band.label in self._selected_bands)
                vars_by_label[band.label] = var
                text = f"{band.name:5} {band.span_text}"
                if band.us_legal == "partial":
                    text += "   ⚠ partly outside US spectrum"
                elif band.us_legal == "none":
                    text += "   ⚠ not usable in the US"
                if band.note:
                    text += f"\n      {band.note}"
                ttk.Checkbutton(inner, text=text, variable=var,
                                command=update_summary).pack(anchor="w")
            update_summary()

        rebuild()

        bottom = ttk.Frame(win, padding=(10, 0, 10, 10))
        bottom.grid(row=2, column=0, sticky="ew")
        ttk.Label(bottom, textvariable=summary, foreground="#58a6ff",
                  wraplength=420, justify="left").pack(anchor="w", pady=(0, 8))

        def apply_and_close():
            picked = [lab for lab, v in vars_by_label.items() if v.get()]
            self._selected_bands = picked
            if picked:
                spans = bandlib.ranges_for(picked)
                # Start/End mirror the overall envelope so the export
                # filename and plot limits stay meaningful.
                self._suppress_band_clear = True
                self._start_var.set(str(spans[0][0]))
                self._end_var.set(str(spans[-1][1]))
                self._suppress_band_clear = False
                names = ", ".join(bandlib.BY_LABEL[p].name for p in picked)
                span_txt = ", ".join(f"{a:g}–{b:g}" for a, b in spans)
                self._bandsel_var.set(
                    f"Bands: {names}  →  {span_txt} MHz "
                    f"({bandlib.total_width(spans):g} MHz)")
                self._preset_var.set("Custom")
            else:
                self._clear_bands()
            self._update_estimate()
            win.destroy()

        ttk.Button(bottom, text="Scan these bands", command=apply_and_close
                   ).pack(side="right")
        ttk.Button(bottom, text="Clear",
                   command=lambda: [v.set(False) for v in vars_by_label.values()]
                   or update_summary()).pack(side="right", padx=(0, 6))

        win.geometry("470x520")

    # ── Estimate label ───────────────────────────────────

    def _update_estimate(self):
        try:
            chunk = float(self._chunk_var.get())
            iters = int(self._iter_var.get())
            spans = [(a, b) for a, b in self._current_ranges() if b > a]
            if chunk <= 0 or not spans:
                self._est_var.set("")
                return
            # Chunks never straddle a span boundary, so count per span.
            n_chunks = sum(math.ceil((b - a) / chunk) for a, b in spans)
            # ~0.1s LO settle + ~0.5s flush (2 sweeps) + ~0.3s per iteration
            secs = n_chunks * (0.6 + iters * 0.3)
            t = f"~{secs:.0f}s" if secs < 60 else f"~{secs/60:.1f}min"
            self._est_var.set(
                f"≈ {n_chunks} chunks · {t}/pass · continuous until stopped"
            )
        except (ValueError, tk.TclError):
            self._est_var.set("")

    # ── Noise floor estimate ────────────────────────────

    def _update_noise_floor(self, n_passes: int):
        """Estimate and display the effective noise floor.

        Noise floor depends on:
        - Device base noise (~-110 dBm WSUB1G, ~-125 dBm WSUB1G Plus)
        - RBW: narrower chunk → lower RBW → lower noise floor
          RBW ≈ chunk_mhz / 112 sweep points
          Noise improves by 10·log10(ref_rbw / actual_rbw) vs reference
        - Multi-pass averaging gain: 10·log10(n_passes)
        """
        try:
            chunk = float(self._chunk_var.get())
        except (ValueError, tk.TclError):
            self._nf_var.set("")
            return

        # Base noise for WSUB1G Plus at wide RBW (~200 kHz reference)
        BASE_NOISE_DBM = -110.0
        REF_RBW_KHZ = 200.0

        rbw_khz = (chunk * 1000.0) / 112.0  # chunk MHz → kHz, 112 sweep points
        rbw_gain = 10.0 * math.log10(REF_RBW_KHZ / max(rbw_khz, 0.1))
        avg_gain = 10.0 * math.log10(max(n_passes, 1))
        est_nf = BASE_NOISE_DBM - rbw_gain - avg_gain

        self._nf_var.set(
            f"Est. noise floor: {est_nf:.0f} dBm  "
            f"({rbw_khz:.0f} kHz RBW, {n_passes} pass{'es' if n_passes != 1 else ''})"
        )

    # ── Connection ───────────────────────────────────────

    def _toggle_connect(self):
        if self._scanner and self._scanner.connected:
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        port = self._port_var.get()
        if not port:
            messagebox.showerror("No Port Selected",
                                 "Select a serial port from the dropdown.\n\n"
                                 "If no ports appear, check the Silicon Labs driver.")
            return
        self._conn_btn.config(state="disabled")
        self._conn_label.config(text="● Connecting…", foreground="orange")
        self._write_log(f"Connecting to {port} at {BAUD_RATE:,} baud…")

        def _work():
            sc = RFExplorerScanner(port, BAUD_RATE)
            ok, msg = sc.connect()
            if ok:
                self._scanner = sc
                self._queue.put({'type': 'connected', 'msg': msg})
            else:
                self._queue.put({'type': 'connect_failed', 'msg': msg})

        threading.Thread(target=_work, daemon=True).start()

    def _do_disconnect(self):
        if self._scanner:
            self._scanner.disconnect()
            self._scanner = None
        self._conn_btn.config(text="Connect", state="normal")
        self._conn_label.config(text="● Disconnected", foreground="#c0392b")
        self._scan_btn.config(state="disabled")
        self._live_btn.config(state="disabled")
        self._write_log("Disconnected.")

    # ── Scan ─────────────────────────────────────────────

    def _start_scan(self):
        if not self._scanner or not self._scanner.connected:
            messagebox.showerror("Not Connected",
                                 "Connect to the RF Explorer first.")
            return
        try:
            start = float(self._start_var.get())
            end   = float(self._end_var.get())
            chunk = float(self._chunk_var.get())
            iters = int(self._iter_var.get())
        except ValueError:
            messagebox.showerror("Invalid Parameters",
                                 "Check that all numeric fields contain valid numbers.")
            return
        if start >= end:
            messagebox.showerror("Invalid Range",
                                 "Start frequency must be less than End frequency.")
            return
        if chunk <= 0:
            messagebox.showerror("Invalid Chunk Size",
                                 "Chunk size must be greater than 0.")
            return

        # Selected bands (possibly disjoint) win over the plain start/end
        # range; with none selected this is just [(start, end)].
        scan_ranges = self._current_ranges()
        if self._selected_bands:
            names = ", ".join(bandlib.BY_LABEL[b].name
                              for b in self._selected_bands)
            self._write_log(f"Scanning bands: {names}")

        self._accumulator.clear()
        self._stop_event.clear()
        self._scan_start_time = time.time()
        self._scanning = True
        if _MPL_OK:
            self._clear_plot()

        self._scan_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._live_btn.config(state="disabled")
        self._save_btn.config(state="disabled")
        self._prog_var.set(0)
        self._result_var.set("Starting continuous scan…")

        def _work():
            try:
                pass_num = 0
                while not self._stop_event.is_set():
                    pass_num += 1
                    self._queue.put({'type': 'pass_started', 'pass_number': pass_num})
                    data = self._scanner.scan_pass(
                        start, end, chunk, iters,
                        self._stop_event, self._queue, pass_num,
                        ranges=scan_ranges,
                    )
                    if data:
                        self._queue.put({
                            'type': 'pass_complete',
                            'data': data,
                            'pass_number': pass_num,
                        })
                self._queue.put({'type': 'scan_stopped'})
            except Exception as exc:
                self._queue.put({'type': 'scan_error', 'msg': str(exc)})

        threading.Thread(target=_work, daemon=True).start()

    def _stop_scan(self):
        self._stop_event.set()
        self._stop_btn.config(state="disabled")
        self._write_log("Stop requested — finishing current chunk…")

    # ── Export helpers ────────────────────────────────────

    _EXPORT_HINTS = {
        "Max-hold": "Peak across all passes — best for coordination",
        "P95":      "95th percentile — catches most signals",
        "P80":      "80th percentile — conservative",
        "P50":      "Median — half quiet, half active",
        "P20":      "20th percentile — noise floor characterization",
    }

    def _update_export_hint(self):
        mode = self._export_mode_var.get()
        self._export_hint.set(self._EXPORT_HINTS.get(mode, ""))

    def _get_export_data(self) -> tuple[list, str]:
        """Return (data, label) based on current export mode."""
        mode = self._export_mode_var.get()
        if mode == "Max-hold":
            return self._accumulator.export_max(), "MAX"
        pct = int(mode[1:])  # "P95" → 95
        return self._accumulator.export_percentile(pct), mode

    # ── CSV Save ─────────────────────────────────────────

    def _save_csv(self):
        if self._accumulator.pass_count == 0:
            messagebox.showinfo("No Data", "Run a scan first.")
            return

        data, label = self._get_export_data()

        # Auto-save to Desktop (native file dialog crashes Python 3.10
        # on macOS 15 due to IconServices bug — SIGBUS at 0xbad4007).
        desktop = os.path.expanduser("~/Desktop")
        try:
            start = float(self._start_var.get())
            end = float(self._end_var.get())
        except ValueError:
            start = end = None
        path = os.path.join(desktop, default_filename(
            start_mhz=start,
            end_mhz=end,
            passes=self._accumulator.pass_count,
            label=label,
        ))

        try:
            n = save_wwb_csv(path, data)
            passes = self._accumulator.pass_count
            self._write_log(
                f"Saved {n} points ({label} across {passes} passes) → "
                f"{os.path.basename(path)}"
            )
            self._write_log(f"File: {path}")
        except OSError as exc:
            self._write_log(f"Save error: {exc}")

    # ── Live Mode ─────────────────────────────────────────

    def _open_live_mode(self):
        if not _MPL_OK:
            messagebox.showwarning(
                "Missing Dependencies",
                "Live Mode requires matplotlib and numpy.\n\n"
                "Install with:\n  pip3 install matplotlib numpy"
            )
            return
        if not self._scanner or not self._scanner.connected:
            messagebox.showerror("Not Connected",
                                 "Connect to the RF Explorer first.")
            return

        try:
            start = float(self._start_var.get())
            end   = float(self._end_var.get())
        except ValueError:
            start, end = 470.0, 900.0

        self._scan_btn.config(state="disabled")
        self._live_btn.config(state="disabled")

        from live_mode import LiveModeWindow

        def on_live_close():
            if self._scanner and self._scanner.connected:
                self._scan_btn.config(state="normal")
                self._live_btn.config(state="normal")

        LiveModeWindow(self.root, self._scanner, start, end,
                       on_close_cb=on_live_close)

    # ── Queue pump (thread-safe UI updates) ──────────────

    def _pump_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                mtype = msg.get('type')

                if mtype == 'log':
                    self._write_log(msg['text'])

                elif mtype == 'progress':
                    self._prog_var.set(msg['value'])
                    elapsed = _format_elapsed(
                        time.time() - self._scan_start_time
                    ) if self._scan_start_time else ""
                    self._status_var.set(f"{msg['text']} | {elapsed}")

                elif mtype == 'chunk_vis':
                    if _MPL_OK:
                        self._update_plot_chunk(msg['data'])

                elif mtype == 'pass_started':
                    n = msg['pass_number']
                    prev = self._accumulator.pass_count
                    self._prog_var.set(0)
                    self._status_var.set(
                        f"Pass {n} scanning…"
                        + (f"  ({prev} accumulated)" if prev > 0 else "")
                    )

                elif mtype == 'pass_complete':
                    self._accumulator.add_pass(msg['data'])
                    n_passes = self._accumulator.pass_count
                    n_bins = self._accumulator.bin_count
                    elapsed = _format_elapsed(
                        time.time() - self._scan_start_time
                    )
                    self._result_var.set(
                        f"{n_passes} passes · {n_bins:,} bins · {elapsed}"
                    )
                    self._update_noise_floor(n_passes)
                    self._save_btn.config(state="normal")
                    self._write_log(
                        f"Pass {msg['pass_number']} complete — "
                        f"{len(msg['data'])} points added "
                        f"({n_passes} total passes)"
                    )
                    if _MPL_OK:
                        self._update_plot_pass_complete()
                        self._clear_plot_scan_data()

                elif mtype == 'scan_stopped':
                    self._scanning = False
                    self._scan_btn.config(state="normal")
                    self._stop_btn.config(state="disabled")
                    if self._scanner and self._scanner.connected:
                        self._live_btn.config(
                            state="normal" if _MPL_OK else "disabled")
                    self._prog_var.set(100)
                    n = self._accumulator.pass_count
                    if n > 0:
                        elapsed = _format_elapsed(
                            time.time() - self._scan_start_time
                        )
                        self._status_var.set(
                            f"Scan complete — {n} passes · "
                            f"{self._accumulator.bin_count:,} bins · {elapsed}"
                        )
                        self._write_log(
                            f"Scanning stopped. {n} passes accumulated. "
                            f"Adjust percentile and save CSV when ready."
                        )
                    else:
                        self._status_var.set("Scan stopped — no data collected.")
                        self._write_log("Scan stopped with no data.")

                elif mtype == 'connected':
                    self._conn_btn.config(text="Disconnect", state="normal")
                    self._conn_label.config(text="● Connected",
                                            foreground="#27ae60")
                    self._scan_btn.config(state="normal")
                    self._live_btn.config(
                        state="normal" if _MPL_OK else "disabled")
                    self._status_var.set("Connected — ready to scan.")
                    self._write_log(msg['msg'])
                    if not _MPL_OK:
                        self._write_log(
                            "Live Mode unavailable — install matplotlib & numpy: "
                            "pip3 install matplotlib numpy"
                        )

                elif mtype == 'connect_failed':
                    self._conn_btn.config(state="normal")
                    self._conn_label.config(text="● Disconnected",
                                            foreground="#c0392b")
                    self._write_log(f"Connection failed: {msg['msg']}")
                    messagebox.showerror("Connection Failed", msg['msg'])

                elif mtype == 'scan_error':
                    self._scanning = False
                    self._scan_btn.config(state="normal")
                    self._stop_btn.config(state="disabled")
                    if self._scanner and self._scanner.connected:
                        self._live_btn.config(
                            state="normal" if _MPL_OK else "disabled")
                    self._status_var.set("Scan error — see log.")
                    self._write_log(f"Error: {msg['msg']}")
                    messagebox.showerror("Scan Error", msg['msg'])

        except queue.Empty:
            pass

        self.root.after(50, self._pump_queue)

    # ── Dep warning ──────────────────────────────────────

    def _warn_missing_deps(self):
        cmd = "pip3 install " + " ".join(MISSING_DEPS)
        self._write_log(f"Missing packages: {', '.join(MISSING_DEPS)}")
        self._write_log(f"Install with:  {cmd}")
        messagebox.showwarning(
            "Missing Dependencies",
            f"Required packages are not installed:\n  {', '.join(MISSING_DEPS)}\n\n"
            f"Open Terminal and run:\n  {cmd}"
        )
