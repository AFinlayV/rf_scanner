// rfe.js — RF Explorer wire protocol over Web Serial.
//
// Port of the parts of the RFExplorer pip package this project actually
// uses. Ground truth was that package's source, read 2026-08-01; the
// formats are recorded in docs/PLAN_browser_serial.md.
//
// On the wire:
//   command      "#" + chr(len(cmd)+2) + cmd
//   config reply "#C2-F:<startKHz 7>,<stepHz 7 or 8>,<top 4>,<bottom 4>,<points 4>,…"
//   sweep frame  "$S" + <length byte> + <N amplitude bytes> + "\r\n"
//   amplitude    dBm = -byte / 2
//   frequency    startMHz + i * stepMHz

const BAUD = 500000;

export class RFExplorer {
  constructor(log = () => {}) {
    this.log = log;
    this.port = null;
    this.reader = null;
    this.writer = null;
    this.buf = new Uint8Array(0);      // raw bytes not yet consumed
    this.config = null;                // {startMHz, stepMHz, points, ...}
    this._pump = null;
    this._closing = false;
  }

  // ── connection ───────────────────────────────────────────────────────

  async connect() {
    this.port = await navigator.serial.requestPort();
    await this.port.open({ baudRate: BAUD });
    this.writer = this.port.writable.getWriter();
    this._closing = false;
    this._pump = this._readLoop();                 // fills this.buf forever
    this.log('port open at ' + BAUD + ' baud');

    // C0 asks the device to send its config and start feeding sweeps.
    await this.send('C0');
    const cfg = await this.waitForConfig(4000);
    this.log(`device config: ${cfg.startMHz.toFixed(3)}–` +
             `${(cfg.startMHz + cfg.stepMHz * (cfg.points - 1)).toFixed(3)} MHz, ` +
             `${cfg.points} points, step ${(cfg.stepMHz * 1000).toFixed(2)} kHz`);
    return cfg;
  }

  async disconnect() {
    this._closing = true;
    try { await this.reader?.cancel(); } catch {}
    try { this.writer?.releaseLock(); } catch {}
    try { await this._pump; } catch {}
    try { await this.port?.close(); } catch {}
    this.port = this.reader = this.writer = null;
    this.buf = new Uint8Array(0);
    this.config = null;
    this.log('port closed');
  }

  // ── raw io ───────────────────────────────────────────────────────────

  async send(cmd) {
    // "#" + length byte + payload. Bytes, not UTF-8 text: a payload byte
    // above 0x7F (e.g. C+\x02 is fine, but be careful) must not be
    // re-encoded into two bytes.
    const body = Uint8Array.from([...cmd].map(c => c.charCodeAt(0) & 0xff));
    const frame = new Uint8Array(2 + body.length);
    frame[0] = 0x23;                    // '#'
    frame[1] = body.length + 2;
    frame.set(body, 2);
    await this.writer.write(frame);
  }

  async _readLoop() {
    this.reader = this.port.readable.getReader();
    try {
      while (!this._closing) {
        const { value, done } = await this.reader.read();
        if (done) break;
        if (value && value.length) this._append(value);
      }
    } catch (e) {
      if (!this._closing) this.log('read loop ended: ' + e.message, 'bad');
    } finally {
      try { this.reader.releaseLock(); } catch {}
    }
  }

  _append(chunk) {
    const merged = new Uint8Array(this.buf.length + chunk.length);
    merged.set(this.buf, 0);
    merged.set(chunk, this.buf.length);
    // Cap the backlog so an idle device can't grow it without bound.
    this.buf = merged.length > 1 << 18 ? merged.slice(-(1 << 17)) : merged;
  }

  // ── stream hygiene ───────────────────────────────────────────────────

  /** Drop every byte received but not yet parsed. Returns how many.
   *
   * scanner.py flushes the serial buffer at the start of a pass because a
   * stopped scan leaves the radio streaming into a buffer nobody drains,
   * and the parser never re-syncs from a truncated message. Here the OS
   * buffer is drained continuously by _readLoop, so the stale bytes sit in
   * this.buf instead — same hazard, different place.
   */
  resetStream() {
    const n = this.buf.length;
    this.buf = new Uint8Array(0);
    return n;
  }

  /** Turn on the on-device averaging calculator (eCalculator.AVG = 2). */
  async setAverageCalculator() {
    await this.send('C+' + String.fromCharCode(2));
  }

  /** Wait for the next sweep frame; returns its raw amplitude bytes. */
  async nextSweep(timeoutMs = 6000) {
    return this._nextSweepRaw(timeoutMs);
  }

  // ── framing ──────────────────────────────────────────────────────────
  // Pull the next complete message out of this.buf, or null.
  //   '#' → a text line terminated by \r\n
  //   '$S' → length byte then that many amplitude bytes
  _nextMessage() {
    const b = this.buf;
    for (let i = 0; i < b.length; i++) {
      if (b[i] === 0x24 && i + 2 < b.length && b[i + 1] === 0x53) {   // "$S"
        const n = b[i + 2];
        const end = i + 3 + n;
        if (end > b.length) return null;                  // incomplete
        const amps = b.slice(i + 3, end);
        // Skip trailing \r\n if present.
        let after = end;
        if (b[after] === 0x0d) after++;
        if (b[after] === 0x0a) after++;
        this.buf = b.slice(after);
        return { type: 'sweep', amps };
      }
      if (b[i] === 0x23) {                                            // '#'
        for (let j = i + 1; j < b.length - 1; j++) {
          if (b[j] === 0x0d && b[j + 1] === 0x0a) {
            const line = new TextDecoder('latin1').decode(b.slice(i, j));
            this.buf = b.slice(j + 2);
            return { type: 'line', line };
          }
        }
        return null;                                      // line incomplete
      }
    }
    if (b.length > 4096) this.buf = b.slice(-1024);       // nothing parseable
    return null;
  }

  // ── config ───────────────────────────────────────────────────────────

  parseConfig(line) {
    // #C2-F:<startKHz 7>,<stepHz 7|8>,<top 4>,<bottom 4>,<points 4>,...
    if (!(line.startsWith('#C2-F:') || line.startsWith('#C2-f:')) || line.length < 60) {
      return null;
    }
    let pos = 6;
    const startMHz = parseInt(line.slice(pos, pos + 7), 10) / 1000.0;
    pos += 8;
    let stepMHz;
    // The step field is 7 OR 8 characters — branch on where the comma is.
    if (line[pos + 7] === ',') {
      stepMHz = parseInt(line.slice(pos, pos + 7), 10) / 1e6;
      pos += 8;
    } else if (line[pos + 8] === ',') {
      stepMHz = parseInt(line.slice(pos, pos + 8), 10) / 1e6;
      pos += 9;
    } else {
      return null;
    }
    const topDBM = parseInt(line.slice(pos, pos + 4), 10); pos += 5;
    const bottomDBM = parseInt(line.slice(pos, pos + 4), 10); pos += 5;
    const points = line.startsWith('#C2-f:')
      ? parseInt(line.slice(pos, pos + 5), 10)
      : parseInt(line.slice(pos, pos + 4), 10);
    if (!isFinite(startMHz) || !isFinite(stepMHz) || !(points > 0)) return null;
    return { startMHz, stepMHz, topDBM, bottomDBM, points };
  }

  async waitForConfig(timeoutMs = 4000) {
    const cfg = await this._until(m => {
      if (m.type !== 'line') return null;
      const c = this.parseConfig(m.line);
      return c || null;
    }, timeoutMs, 'device config (#C2-F:)');
    this.config = cfg;
    return cfg;
  }

  // ── sweeping ─────────────────────────────────────────────────────────

  /** Retune, wait for the config echo, discard settling sweeps, return one. */
  async sweepOnce(startMHz, endMHz, { settle = 2, timeoutMs = 6000 } = {}) {
    await this.setRange(startMHz, endMHz);
    // The device echoes a new #C2-F: after retuning; that tells us the real
    // start/step it chose, which is what the bins must be derived from.
    const cfg = await this.waitForConfig(timeoutMs);
    for (let i = 0; i < settle; i++) {
      await this._nextSweepRaw(timeoutMs);       // discard: AGC/detector settling
    }
    const amps = await this._nextSweepRaw(timeoutMs);
    return this.toPoints(amps, cfg);
  }

  async setRange(startMHz, endMHz) {
    const kHz = m => String(Math.round(m * 1000)).padStart(7, '0');
    const top = this.config?.topDBM ?? -30;
    const bottom = this.config?.bottomDBM ?? -120;
    const cmd = `C2-F:${kHz(startMHz)},${kHz(endMHz)},` +
                `${String(top).padStart(4, '0')},${String(bottom).padStart(4, '0')}`;
    await this.send(cmd);
  }

  async _nextSweepRaw(timeoutMs) {
    return this._until(m => (m.type === 'sweep' ? m.amps : null),
                       timeoutMs, 'sweep ($S)');
  }

  toPoints(amps, cfg = this.config) {
    const out = [];
    for (let i = 0; i < amps.length; i++) {
      out.push([cfg.startMHz + i * cfg.stepMHz, -amps[i] / 2.0]);
    }
    return out;
  }

  // ── plumbing ─────────────────────────────────────────────────────────

  /** Drain messages until `pick` returns non-null, or time out. */
  async _until(pick, timeoutMs, what) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      let msg;
      while ((msg = this._nextMessage()) !== null) {
        const got = pick(msg);
        if (got !== null && got !== undefined) return got;
      }
      await new Promise(r => setTimeout(r, 15));
    }
    throw new Error(`timed out waiting for ${what}`);
  }
}
