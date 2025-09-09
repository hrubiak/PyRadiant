#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, time, serial, sys, traceback
from dataclasses import dataclass
from typing import Optional

from PyQt6 import QtCore

# =========================
# ===== OL16A DRIVER  =====
# =========================

# ---- Control bytes ----
HS_EOT = 0xFF  # handshake (host->device)
ACK    = 0x06
NAK    = 0x15
SOH    = 0x01  # we SEND frames starting with SOH
STX    = 0x02  # device REPLIES starting with STX
ETX    = 0x03

def sum7(b: bytes) -> int:
    return sum(b) & 0x7F

@dataclass
class OL16AConfig:
    port: str = "/dev/tty.usbserial-1440"
    baudrate: int = 9600
    address: int = 1
    timeout: float = 1.0
    settle: float = 0.25        # pacing between handshake and frame
    post_frame_wait: float = 0.12
    max_polls: int = 10
    poll_gap: float = 0.08
    assert_dtr: bool = True
    assert_rts: bool = True
    debug: bool = False
    # Optional safety ceilings. Set to None to disable enforcement.
    max_current: Optional[float] = 2.5
    max_voltage: Optional[float] = 20.0

class OL16A:
    # Status bit names (from manual Appendix C)
    _STATUS_BIT_NAMES = {
        7: "BUSY",
        4: "LAMP_ON",
        1: "SEEKING_CURRENT",
        # other bits reserved
    }

    def __init__(self, cfg: OL16AConfig | None = None):
        self.cfg = cfg or OL16AConfig()
        self.addr7 = self.cfg.address & 0x7F

        self.ser = serial.Serial(
            self.cfg.port, self.cfg.baudrate, timeout=self.cfg.timeout,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, xonxoff=False, rtscts=False, dsrdtr=self.cfg.assert_dtr
        )
        try:
            self.ser.setRTS(self.cfg.assert_rts)
        except Exception:
            pass
        time.sleep(0.2)

        # Always select Lamp 1 for this app; enforce ceilings if configured (no output changes)
        self.select_lamp(1)
        t = self.read_target()
        if t["unit"] not in ("A", "V", "W"):
            raise RuntimeError("Unexpected target unit on startup.")
        if self.cfg.max_current is not None and t["unit"] == "A" and t["value"] > self.cfg.max_current:
            self.set_current(min(0.200, self.cfg.max_current))
        if self.cfg.max_voltage is not None and t["unit"] == "V" and t["value"] > self.cfg.max_voltage:
            self.set_voltage(min(5.0, self.cfg.max_voltage))

    # ---------- internals ----------
    def _expect(self, wanted, label):
        b = self.ser.read(1)
        if not b: raise TimeoutError(f"Timeout waiting for {label}")
        v = b[0]
        if v not in (wanted if isinstance(wanted,(set,tuple,list)) else (wanted,)):
            raise RuntimeError(f"Unexpected 0x{v:02X} while waiting for {label}")
        return v

    def _write_frame(self, payload: bytes):
        """Send handshake + frame, with small automatic retry on NAK."""
        for attempt in range(3):
            # handshake (clear RX only)
            self.ser.reset_input_buffer()
            self.ser.write(bytes([HS_EOT, self.addr7]))
            r = self._expect({ACK, NAK}, "ACK/NAK after EOT+ADDR")

            if r == ACK:
                # proceed to frame
                time.sleep(self.cfg.settle)
                tx_chk = sum7(bytes([self.addr7]) + payload + bytes([ETX]))
                frame = bytes([SOH]) + payload + bytes([ETX, tx_chk])
                self.ser.write(frame); self.ser.flush()

                r2 = self._expect({ACK, NAK}, "ACK/NAK after frame")
                if r2 == ACK:
                    time.sleep(self.cfg.post_frame_wait)
                    return
                # frame NAK — try once more after a drain & tiny pause
                if attempt < 2:
                    self._drain_pending()
                    time.sleep(max(self.cfg.settle, 0.05))
                    continue
                raise RuntimeError("NAK to frame (checksum/message)")

            # handshake NAK — drain anything pending and retry
            if attempt < 2:
                self._drain_pending()
                time.sleep(max(self.cfg.settle, 0.05))
                continue
            raise RuntimeError("NAK to write handshake")

    def _read_frame_once(self):
        """Poll once; return payload bytes or None if no data."""
        # IMPORTANT: don't clear TX here; only RX
        self.ser.reset_input_buffer()
        self.ser.write(bytes([HS_EOT, self.addr7 | 0x80]))
        b = self.ser.read(1)
        if not b: return None
        if b[0] == NAK: return None
        if b[0] != ACK: return None

        # Expect STX … ETX + CHK
        h = self.ser.read(1)
        if h != bytes([STX]): return None
        payload = bytearray()
        while True:
            c = self.ser.read(1)
            if not c: return None
            if c[0] == ETX: break
            payload.append(c[0])
        rchk = self.ser.read(1)
        if not rchk: return None
        rchk = rchk[0] & 0x7F
        ok = (rchk == sum7(bytes([STX]) + bytes(payload) + bytes([ETX])))
        self.ser.write(bytes([ACK]))
        if not ok:
            raise RuntimeError(f"Reply checksum mismatch (rx=0x{rchk:02X})")
        return bytes(payload)

    def _read_frame(self) -> bytes:
        for _ in range(self.cfg.max_polls):
            got = self._read_frame_once()
            if got is not None:
                return got
            time.sleep(self.cfg.poll_gap)
        raise RuntimeError("No data pending (NAK/timeouts on read poll)")

    def _drain_pending(self, max_frames: int = 5):
        """Politely pull and ACK any queued replies so we start clean."""
        for _ in range(max_frames):
            self.ser.write(bytes([HS_EOT, self.addr7 | 0x80]))
            b = self.ser.read(1)
            if not b or b[0] == NAK:
                return
            if b[0] != ACK:
                return
            h = self.ser.read(1)
            if h != bytes([STX]):
                return
            while True:
                c = self.ser.read(1)
                if not c or c[0] == ETX:
                    break
            _ = self.ser.read(1)  # checksum
            self.ser.write(bytes([ACK]))  # ACK discarded frame

    def _resync(self):
        """Try to get back to idle: two consecutive NAKs on read poll."""
        consecutive_naks = 0
        for _ in range(10):
            self.ser.write(bytes([HS_EOT, self.addr7 | 0x80]))
            b = self.ser.read(1)
            if b and b[0] == NAK:
                consecutive_naks += 1
                if consecutive_naks >= 2:
                    return
            elif b and b[0] == ACK:
                h = self.ser.read(1)
                if h != bytes([STX]): continue
                while True:
                    c = self.ser.read(1)
                    if not c or c[0] == ETX:
                        break
                _ = self.ser.read(1)
                self.ser.write(bytes([ACK]))
                consecutive_naks = 0
            else:
                consecutive_naks = 0

    def transact(self, ascii_msg: str, expect_reply: bool = True) -> str | None:
        # Start clean
        self._drain_pending()

        self._write_frame(ascii_msg.encode("ascii"))
        if not expect_reply:
            for _ in range(2):
                got = self._read_frame_once()
                if got is not None:
                    return got.decode("ascii", errors="replace").strip()
                time.sleep(self.cfg.poll_gap)
            return None
        try:
            payload = self._read_frame()
        except RuntimeError:
            # e.g., checksum mismatch due to earlier interleave — resync and one retry
            self._resync()
            payload = self._read_frame()
        return payload.decode("ascii", errors="replace").strip()

    # ---------- regex parsers ----------
    _re_c = re.compile(r"^\s*[Cc]\s+([+-]?\d+(?:\.\d+)?)\s+([0-9A-Fa-f]{2})\s*$")        # present current
    _re_v = re.compile(r"^\s*[Vv]\s+([+-]?\d+(?:\.\d+)?)\s+([0-9A-Fa-f]{2})\s*$")        # present voltage
    _re_t = re.compile(r"^\s*t\s+(\d+)\s+([+-]?\d+(?:\.\d+)?)\s+([AVW])\s+([0-9A-Fa-f]{2})\s*$")  # target
    _re_b = re.compile(r"^\s*b\s+([01])\s+([0-9A-Fa-f]{2})\s*$")                         # output state

    def _decode_status(self, ss_hex: str) -> list[str]:
        """Decode status hex (e.g. '92') into human-readable flags."""
        try:
            val = int(ss_hex, 16) & 0xFF
        except Exception:
            return [f"INVALID({ss_hex!r})"]
        flags = [name for bit, name in sorted(self._STATUS_BIT_NAMES.items(), reverse=True)
                 if val & (1 << bit)]
        return flags or ["OK/IDLE"]

    # ---------- readers (structured) ----------
    def read_current(self) -> dict:
        txt = self.transact("c")
        m = self._re_c.match(txt)
        if not m: raise RuntimeError(f"Unexpected c-reply: {txt!r}")
        amps = float(m.group(1)); ss = m.group(2).upper()
        return {"amps": amps, "status": ss, "flags": self._decode_status(ss)}

    def read_voltage(self) -> dict:
        txt = self.transact("v")
        m = self._re_v.match(txt)
        if not m: raise RuntimeError(f"Unexpected v-reply: {txt!r}")
        volts = float(m.group(1)); ss = m.group(2).upper()
        return {"volts": volts, "status": ss, "flags": self._decode_status(ss)}

    def read_target(self) -> dict:
        txt = self.transact("t")
        m = self._re_t.match(txt)
        if not m: raise RuntimeError(f"Unexpected t-reply: {txt!r}")
        mode = int(m.group(1)); val = float(m.group(2)); unit = m.group(3)
        ss = m.group(4).upper()
        return {"mode": mode, "value": val, "unit": unit, "status": ss, "flags": self._decode_status(ss)}

    def read_output_state(self) -> dict:
        txt = self.transact("b")
        m = self._re_b.match(txt)
        if not m: raise RuntimeError(f"Unexpected b-reply: {txt!r}")
        state = bool(int(m.group(1))); ss = m.group(2).upper()
        return {"on": state, "status": ss, "flags": self._decode_status(ss)}

    # ---------- lamp selection ----------
    def get_lamp(self) -> int:
        return int(self.read_target()["mode"])

    def select_lamp(self, slot: int) -> str:
        if not (1 <= slot <= 9):
            raise ValueError("slot must be 1..9")
        echo = self.transact(f"S {slot}")
        time.sleep(max(self.cfg.post_frame_wait, 0.05))
        if self.get_lamp() != slot:
            raise RuntimeError("Failed to select lamp slot")
        return echo

    # ---------- setters (pure) ----------
    def set_current(self, amps: float) -> dict:
        if self.cfg.max_current is not None and amps > self.cfg.max_current:
            raise ValueError(f"Requested {amps:.3f} A exceeds max_current={self.cfg.max_current:.3f} A")
        _ = self.transact(f"C {amps:.6f}", expect_reply=True)  # reply 'C CV SS'
        time.sleep(max(self.cfg.post_frame_wait, 0.05))
        return self.read_target()

    def set_voltage(self, volts: float) -> dict:
        if self.cfg.max_voltage is not None and volts > self.cfg.max_voltage:
            raise ValueError(f"Requested {volts:.3f} V exceeds max_voltage={self.cfg.max_voltage:.3f} V")
        _ = self.transact(f"V {volts:.6f}", expect_reply=True)  # reply 'V VV SS'
        time.sleep(max(self.cfg.post_frame_wait, 0.05))
        return self.read_target()

    # ---------- output control ----------
    def lamp_on(self) -> str:
        return self.transact("B 1")

    def lamp_off(self) -> str:
        return self.transact("B 0")

    # ---------- stability ----------
    def wait_until_stable(self, timeout: float = 10.0, poll: float = 0.1) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            info = self.read_current()
            flags = set(info["flags"])
            if "BUSY" not in flags and "SEEKING_CURRENT" not in flags:
                return info
            time.sleep(poll)
        raise TimeoutError("Instrument did not become stable before timeout")

    def close(self):
        try: self.ser.close()
        except Exception: pass


# ========================================
# ===== PyQt6 MVC: Model / View / Controller
# ========================================

class Poller(QtCore.QObject):
    """Background poller that periodically reads I/V/target/state."""
    reading = QtCore.pyqtSignal(dict)
    message = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, ol: OL16A, lock: QtCore.QMutex, interval_ms: int = 250):
        super().__init__()
        self.ol = ol
        self.lock = lock
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    @QtCore.pyqtSlot()
    def start(self):
        self._timer.start()

    @QtCore.pyqtSlot()
    def stop(self):
        self._timer.stop()

    @QtCore.pyqtSlot()
    def _tick(self):
        # Try to grab the mutex without blocking; if busy, skip this tick
        if not self.lock.tryLock(0):
            return
        try:
            # Always fetch state + target
            state = self.ol.read_output_state()
            target = self.ol.read_target()

            amps = volts = None
            if state.get("on", False):
                # Read each channel independently; tolerate a transient NAK on either
                try:
                    amps = self.ol.read_current()["amps"]
                except Exception as e:
                    # ignore common transient NAKs/timeouts during ramps
                    if "NAK" not in str(e) and "Timeout" not in str(e):
                        raise
                try:
                    volts = self.ol.read_voltage()["volts"]
                except Exception as e:
                    if "NAK" not in str(e) and "Timeout" not in str(e):
                        raise

            self.reading.emit({"amps": amps, "volts": volts, "target": target, "state": state})
        except Exception as e:
            self.error.emit(f"Polling error: {e}")
        finally:
            self.lock.unlock()

