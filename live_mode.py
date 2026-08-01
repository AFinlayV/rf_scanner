"""Live Mode window — real-time spectrum + waterfall display.

Requires numpy and matplotlib. The main app checks availability before
opening this window.
"""

import tkinter as tk
from tkinter import ttk
import threading
import queue
import time

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.gridspec import GridSpec

try:
    from RFExplorer import RFE_Common
    _RFEX_OK = True
except ImportError:
    _RFEX_OK = False


class LiveModeWindow(tk.Toplevel):
    """
    Floating spectrum analyser window with two panels:
      - Spectrum   — instantaneous amplitude vs frequency (line plot)
      - Waterfall  — scrolling time history, colour = amplitude (imshow)

    The background scan loop runs in a daemon thread and posts sweep dicts
    to a Queue; the Tk event loop drains the queue at ~30 fps via after().
    """

    WATERFALL_ROWS = 200
    DB_MIN_DEFAULT = -130.0
    DB_MAX_DEFAULT = -20.0
    FIG_BG      = '#0d0d0d'
    AX_BG       = '#0d0d0d'
    BAR_BG      = '#161616'
    GRID_COLOR  = '#252525'
    LABEL_COLOR = '#707070'
    SPEC_COLOR  = '#00e676'   # bright green — current sweep
    PEAK_COLOR  = '#ff6d00'   # orange       — peak hold
    CMAP        = 'inferno'

    def __init__(
        self,
        parent: tk.Tk,
        scanner,
        start_mhz: float,
        end_mhz: float,
        on_close_cb=None,
    ):
        super().__init__(parent)
        self.title('RF Live Monitor')
        self.configure(bg=self.FIG_BG)
        self.geometry('1100x760')
        self.minsize(700, 500)

        self._scanner     = scanner
        self._start_mhz   = start_mhz
        self._end_mhz     = end_mhz
        self._on_close_cb = on_close_cb
        self._running     = False
        self._stop_event  = threading.Event()
        self._queue: queue.Queue = queue.Queue()

        # Plot data (initialised on first sweep)
        self._freq_arr:  'np.ndarray | None' = None
        self._waterfall: 'np.ndarray | None' = None
        self._peak_amps: 'np.ndarray | None' = None

        self._build_controls()
        self._build_plots()
        self._pump_queue()
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── Controls bar ──────────────────────────────────────

    def _build_controls(self):
        bar = tk.Frame(self, bg=self.BAR_BG, pady=5, padx=8)
        bar.pack(side='top', fill='x')

        lc = self.LABEL_COLOR

        # Start / Stop
        self._start_btn = ttk.Button(bar, text='Start',
                                     command=self._start, width=9)
        self._start_btn.pack(side='left', padx=(0, 3))

        self._stop_btn = ttk.Button(bar, text='Stop',
                                    command=self._stop,
                                    state='disabled', width=9)
        self._stop_btn.pack(side='left', padx=(0, 14))

        # Frequency range
        def lbl(text):
            tk.Label(bar, text=text, bg=self.BAR_BG, fg=lc,
                     font=('Helvetica', 10)).pack(side='left')

        lbl('Start MHz:')
        self._live_start_var = tk.StringVar(value=str(self._start_mhz))
        ttk.Entry(bar, textvariable=self._live_start_var,
                  width=7).pack(side='left', padx=(2, 8))

        lbl('End MHz:')
        self._live_end_var = tk.StringVar(value=str(self._end_mhz))
        ttk.Entry(bar, textvariable=self._live_end_var,
                  width=7).pack(side='left', padx=(2, 8))

        ttk.Button(bar, text='Apply', command=self._apply_range,
                   width=6).pack(side='left', padx=(0, 14))

        # dBm scale
        lbl('dBm min:')
        self._db_min_var = tk.DoubleVar(value=self.DB_MIN_DEFAULT)
        ttk.Entry(bar, textvariable=self._db_min_var,
                  width=6).pack(side='left', padx=(2, 6))

        lbl('max:')
        self._db_max_var = tk.DoubleVar(value=self.DB_MAX_DEFAULT)
        ttk.Entry(bar, textvariable=self._db_max_var,
                  width=6).pack(side='left', padx=(2, 8))

        ttk.Button(bar, text='Scale', command=self._apply_scale,
                   width=6).pack(side='left', padx=(0, 14))

        # Peak hold
        self._peak_hold_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='Peak Hold',
                        variable=self._peak_hold_var,
                        command=self._on_peak_toggle
                        ).pack(side='left', padx=(0, 4))

        ttk.Button(bar, text='Clear', command=self._clear_peak,
                   width=5).pack(side='left', padx=(0, 14))

        # Status (right-aligned)
        self._live_status = tk.StringVar(value='Stopped')
        tk.Label(bar, textvariable=self._live_status,
                 bg=self.BAR_BG, fg='#555555',
                 font=('Menlo', 10)).pack(side='right', padx=8)

    # ── Matplotlib figure ──────────────────────────────────

    def _build_plots(self):
        frame = tk.Frame(self, bg=self.FIG_BG)
        frame.pack(side='top', fill='both', expand=True)

        self._fig = Figure(figsize=(11, 7.5),
                           facecolor=self.FIG_BG, dpi=100)
        gs = GridSpec(3, 1, figure=self._fig, hspace=0.04,
                      left=0.07, right=0.96, top=0.97, bottom=0.05)

        # ── Spectrum (top third) ──────────────────────────
        self._ax_spec = self._fig.add_subplot(gs[0])
        self._style_ax(self._ax_spec, ylabel='dBm')
        self._ax_spec.set_xlim(self._start_mhz, self._end_mhz)
        self._ax_spec.set_ylim(self.DB_MIN_DEFAULT, self.DB_MAX_DEFAULT)
        self._ax_spec.xaxis.set_ticklabels([])

        ix = np.array([self._start_mhz, self._end_mhz])
        iy = np.array([self.DB_MIN_DEFAULT, self.DB_MIN_DEFAULT])
        self._spec_line, = self._ax_spec.plot(
            ix, iy, color=self.SPEC_COLOR, lw=0.9, antialiased=True)
        self._peak_line, = self._ax_spec.plot(
            ix, iy, color=self.PEAK_COLOR, lw=0.8, alpha=0.85,
            linestyle='--')
        self._peak_line.set_visible(False)

        # ── Waterfall (bottom two thirds) ─────────────────
        self._ax_wf = self._fig.add_subplot(gs[1:])
        self._style_ax(self._ax_wf, xlabel='MHz')
        self._ax_wf.set_yticks([])

        n_ph = max(2, int((self._end_mhz - self._start_mhz) / 0.025))
        wf_ph = np.full((self.WATERFALL_ROWS, n_ph),
                        self.DB_MIN_DEFAULT, dtype=np.float32)
        self._wf_img = self._ax_wf.imshow(
            wf_ph, aspect='auto', origin='upper',
            extent=[self._start_mhz, self._end_mhz,
                    self.WATERFALL_ROWS, 0],
            vmin=self.DB_MIN_DEFAULT, vmax=self.DB_MAX_DEFAULT,
            cmap=self.CMAP, interpolation='nearest',
        )
        self._ax_wf.set_xlim(self._start_mhz, self._end_mhz)

        cb = self._fig.colorbar(self._wf_img, ax=self._ax_wf,
                                 pad=0.01, fraction=0.015)
        cb.ax.tick_params(colors=self.LABEL_COLOR, labelsize=7)
        cb.outline.set_edgecolor(self.GRID_COLOR)
        cb.set_label('dBm', color=self.LABEL_COLOR, fontsize=8)

        self._canvas = FigureCanvasTkAgg(self._fig, master=frame)
        self._canvas.get_tk_widget().pack(fill='both', expand=True)
        self._canvas.draw()

    def _style_ax(self, ax, xlabel=None, ylabel=None):
        ax.set_facecolor(self.AX_BG)
        ax.tick_params(colors=self.LABEL_COLOR, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(self.GRID_COLOR)
        ax.grid(True, color=self.GRID_COLOR, linewidth=0.5, alpha=0.9)
        if xlabel:
            ax.set_xlabel(xlabel, color=self.LABEL_COLOR, fontsize=9)
        if ylabel:
            ax.set_ylabel(ylabel, color=self.LABEL_COLOR, fontsize=9)

    # ── Control actions ────────────────────────────────────

    def _start(self):
        self._running = True
        self._stop_event.clear()
        self._start_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._live_status.set('Running…')
        threading.Thread(target=self._live_loop, daemon=True).start()

    def _stop(self):
        self._stop_event.set()
        self._stop_btn.config(state='disabled')
        self._live_status.set('Stopping…')

    def _apply_range(self):
        try:
            s = float(self._live_start_var.get())
            e = float(self._live_end_var.get())
        except ValueError:
            return
        if s >= e:
            return
        self._start_mhz = s
        self._end_mhz   = e
        self._freq_arr  = None
        if self._running:
            self._stop_event.set()

    def _apply_scale(self):
        try:
            lo = float(self._db_min_var.get())
            hi = float(self._db_max_var.get())
        except ValueError:
            return
        if lo >= hi:
            return
        self._ax_spec.set_ylim(lo, hi)
        self._wf_img.set_clim(lo, hi)
        self._canvas.draw_idle()

    def _on_peak_toggle(self):
        self._peak_line.set_visible(self._peak_hold_var.get())
        if not self._peak_hold_var.get():
            self._clear_peak()
        self._canvas.draw_idle()

    def _clear_peak(self):
        if self._peak_amps is not None:
            floor = float(self._db_min_var.get())
            self._peak_amps[:] = floor
            if self._peak_line.get_visible():
                self._peak_line.set_ydata(self._peak_amps)
                self._canvas.draw_idle()

    # ── Background scan loop ───────────────────────────────

    def _live_loop(self):
        rfe = self._scanner.rfe
        try:
            try:
                rfe.SetCalculator(RFE_Common.eCalculator.Normal)
            except Exception:
                pass

            while not self._stop_event.is_set():
                s, e = self._start_mhz, self._end_mhz
                rfe.UpdateDeviceConfig(s, e)
                time.sleep(0.4)
                try:
                    rfe.SweepData.CleanAll()
                except Exception:
                    pass

                cfg = (s, e)
                while not self._stop_event.is_set():
                    if (self._start_mhz, self._end_mhz) != cfg:
                        break

                    rfe.ProcessReceivedString(True)

                    count = rfe.SweepData.Count
                    if count < 1:
                        continue

                    sweep = rfe.SweepData.GetData(count - 1)
                    if sweep is None:
                        continue

                    n = (sweep.TotalDataPoints()
                         if callable(sweep.TotalDataPoints)
                         else sweep.TotalDataPoints)
                    if n < 2:
                        continue

                    f0 = sweep.GetFrequencyMHZ(0)
                    f1 = sweep.GetFrequencyMHZ(n - 1)
                    amps = [sweep.GetAmplitude_DBM(i) for i in range(n)]

                    self._queue.put(
                        {'type': 'sweep', 'f0': f0, 'f1': f1,
                         'n': n, 'amps': amps}
                    )
                    try:
                        rfe.SweepData.CleanAll()
                    except Exception:
                        pass

        except Exception as exc:
            self._queue.put({'type': 'error', 'msg': str(exc)})
        finally:
            self._queue.put({'type': 'stopped'})

    # ── Queue pump ─────────────────────────────────────────

    def _pump_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                mt  = msg.get('type')

                if mt == 'sweep':
                    self._handle_sweep(msg)

                elif mt == 'error':
                    self._live_status.set(f"Error: {msg['msg']}")
                    self._running = False
                    self._start_btn.config(state='normal')
                    self._stop_btn.config(state='disabled')

                elif mt == 'stopped':
                    self._running = False
                    self._start_btn.config(state='normal')
                    self._stop_btn.config(state='disabled')
                    self._live_status.set('Stopped')

        except queue.Empty:
            pass
        self.after(33, self._pump_queue)

    def _handle_sweep(self, msg: dict):
        f0, f1, n = msg['f0'], msg['f1'], msg['n']
        amps = np.array(msg['amps'], dtype=np.float32)

        if self._freq_arr is None or len(self._freq_arr) != n:
            self._freq_arr  = np.linspace(f0, f1, n)
            floor           = float(self._db_min_var.get())
            self._waterfall = np.full((self.WATERFALL_ROWS, n),
                                      floor, dtype=np.float32)
            self._peak_amps = np.full(n, floor, dtype=np.float32)

            self._ax_spec.set_xlim(f0, f1)
            self._ax_wf.set_xlim(f0, f1)
            self._wf_img.set_extent([f0, f1, self.WATERFALL_ROWS, 0])
            self._spec_line.set_xdata(self._freq_arr)
            self._peak_line.set_xdata(self._freq_arr)

        if self._peak_hold_var.get():
            np.maximum(self._peak_amps, amps, out=self._peak_amps)
        else:
            np.copyto(self._peak_amps, amps)

        self._waterfall = np.roll(self._waterfall, 1, axis=0)
        self._waterfall[0] = amps

        self._spec_line.set_ydata(amps)
        self._peak_line.set_ydata(self._peak_amps)
        self._wf_img.set_data(self._waterfall)

        pts_label = f'{n} pts  ·  {(f1-f0)/n*1000:.0f} kHz/pt'
        self._live_status.set(
            f'Live  {f0:.1f}–{f1:.1f} MHz  ·  {pts_label}')

        self._canvas.draw_idle()

    # ── Close ──────────────────────────────────────────────

    def _on_close(self):
        self._stop_event.set()
        if self._on_close_cb:
            self._on_close_cb()
        self.after(250, self.destroy)
