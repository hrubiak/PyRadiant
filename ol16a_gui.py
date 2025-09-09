#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, time, serial, sys, traceback
from dataclasses import dataclass
from typing import Optional
import html
from contextlib import contextmanager
from PyQt6 import QtCore, QtWidgets, QtGui

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

# -------- Model (Qt QObject around OL16A) --------
class InstrumentModel(QtCore.QObject):
    # Signals
    connectedChanged = QtCore.pyqtSignal(bool)
    readingUpdated = QtCore.pyqtSignal(dict)   # {'amps':..,'volts':..,'target':..,'state':..}
    message = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ol: Optional[OL16A] = None
        self._poll_thread: Optional[QtCore.QThread] = None
        self._poller: Optional['Poller'] = None
        self._lock = QtCore.QMutex()

    # shared I/O guard for ALL instrument access
    @contextmanager
    def _io_guard(self):
        locker = QtCore.QMutexLocker(self._lock)
        try:
            yield
        finally:
            del locker

    @QtCore.pyqtSlot(str, int)
    def connect_instr(self, port: str, address: int):
        try:
            if self._ol:
                self.error.emit("Already connected.")
                return
            cfg = OL16AConfig(port=port, address=address, debug=False)
            self._ol = OL16A(cfg)

            # Start poller thread
            self._poll_thread = QtCore.QThread(self)
            self._poller = Poller(self._ol, self._lock, 350)
            self._poller.moveToThread(self._poll_thread)
            self._poll_thread.started.connect(self._poller.start)
            self._poller.reading.connect(self.readingUpdated)
            self._poller.error.connect(self._forward_error)
            self._poller.message.connect(self.message)
            self._poll_thread.start()

            self.connectedChanged.emit(True)
            self.message.emit("Connected.")
        except Exception as e:
            self._ol = None
            self._stop_polling()
            self.error.emit(f"Connect failed: {e}")

    @QtCore.pyqtSlot()
    def disconnect_instr(self):
        try:
            self._stop_polling()
            if self._ol:
                self._ol.close()
                self._ol = None
            self.connectedChanged.emit(False)
            self.message.emit("Disconnected.")
        except Exception as e:
            self.error.emit(f"Disconnect failed: {e}")

    def _stop_polling(self):
        if self._poller:
            self._poller.stop()
            self._poller = None
        if self._poll_thread:
            self._poll_thread.quit()
            self._poll_thread.wait(2000)
            self._poll_thread = None

    def _ensure(self):
        if not self._ol:
            raise RuntimeError("Not connected")

    @QtCore.pyqtSlot(float)
    def set_current(self, amps: float):
        try:
            self._ensure()
            with self._io_guard():
                t = self._ol.set_current(amps)
                state = self._ol.read_output_state()
                amps_actual  = self._ol.read_current()["amps"] if state["on"] else None
                volts_actual = self._ol.read_voltage()["volts"] if state["on"] else None
            self.message.emit(f"Set current target to {t['value']:.6f} {t['unit']}")
            self.readingUpdated.emit({"amps": amps_actual, "volts": volts_actual, "target": t, "state": state})
        except Exception as e:
            self.error.emit(f"Set current failed: {e}")

    @QtCore.pyqtSlot(float)
    def set_voltage(self, volts: float):
        try:
            self._ensure()
            with self._io_guard():
                t = self._ol.set_voltage(volts)
                state = self._ol.read_output_state()
                amps  = self._ol.read_current()["amps"] if state["on"] else None
                volts_actual = self._ol.read_voltage()["volts"] if state["on"] else None
            self.message.emit(f"Set voltage target to {t['value']:.6f} {t['unit']}")
            self.readingUpdated.emit({"amps": amps, "volts": volts_actual, "target": t, "state": state})
        except Exception as e:
            self.error.emit(f"Set voltage failed: {e}")

    @QtCore.pyqtSlot()
    def lamp_on(self):
        try:
            self._ensure()
            with self._io_guard():
                echo = self._ol.lamp_on()
            self.message.emit(f"Output ON: {echo}")
        except Exception as e:
            self.error.emit(f"Output ON failed: {e}")

    @QtCore.pyqtSlot()
    def lamp_off(self):
        try:
            self._ensure()
            with self._io_guard():
                echo = self._ol.lamp_off()
            self.message.emit(f"Output OFF: {echo}")
        except Exception as e:
            self.error.emit(f"Output OFF failed: {e}")

    @QtCore.pyqtSlot()
    def poll_once(self):
        try:
            self._ensure()
            with self._io_guard():
                state = self._ol.read_output_state()
                target = self._ol.read_target()
                if state["on"]:
                    amps  = self._ol.read_current()["amps"]
                    volts = self._ol.read_voltage()["volts"]
                else:
                    amps = volts = None
            self.readingUpdated.emit({"amps": amps, "volts": volts, "target": target, "state": state})
        except Exception as e:
            self.error.emit(f"Poll failed: {e}")

    @QtCore.pyqtSlot(str)
    def _forward_error(self, s: str):
        self.error.emit(s)


# -------- View (MainWindow) + Controller wiring --------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OL-16A Controller")
        self.setMinimumSize(720, 420)
        self.model = InstrumentModel(self)
        self._build_ui()
        self._wire_controller()

    def _build_ui(self):
        w = QtWidgets.QWidget()
        self.setCentralWidget(w)
        layout = QtWidgets.QVBoxLayout(w)

        # Connection row
        conn = QtWidgets.QHBoxLayout()
        self.portEdit = QtWidgets.QLineEdit("/dev/tty.usbserial-1440")
        self.addrSpin = QtWidgets.QSpinBox()
        self.addrSpin.setRange(1, 127); self.addrSpin.setValue(1)
        self.btnConnect = QtWidgets.QPushButton("Connect")
        self.btnDisconnect = QtWidgets.QPushButton("Disconnect")
        conn.addWidget(QtWidgets.QLabel("Port:")); conn.addWidget(self.portEdit, 2)
        conn.addWidget(QtWidgets.QLabel("Addr:")); conn.addWidget(self.addrSpin)
        conn.addStretch(1); conn.addWidget(self.btnConnect); conn.addWidget(self.btnDisconnect)
        layout.addLayout(conn)

        # Targets row
        targets = QtWidgets.QHBoxLayout()
        self.curSpin = QtWidgets.QDoubleSpinBox()
        self.curSpin.setRange(0.0, 20.0); self.curSpin.setDecimals(6); self.curSpin.setSingleStep(0.01)
        self.btnSetI = QtWidgets.QPushButton("Set Current (A)")

        self.voltSpin = QtWidgets.QDoubleSpinBox()
        self.voltSpin.setRange(0.0, 50.0); self.voltSpin.setDecimals(6); self.voltSpin.setSingleStep(0.01)
        self.btnSetV = QtWidgets.QPushButton("Set Voltage (V)")

        targets.addWidget(QtWidgets.QLabel("I target (A):")); targets.addWidget(self.curSpin)
        targets.addWidget(self.btnSetI)
        targets.addSpacing(20)
        targets.addWidget(QtWidgets.QLabel("V target (V):")); targets.addWidget(self.voltSpin)
        targets.addWidget(self.btnSetV)
        layout.addLayout(targets)

        # Output control row
        out = QtWidgets.QHBoxLayout()
        self.btnOn = QtWidgets.QPushButton("Output ON")
        self.btnOff = QtWidgets.QPushButton("Output OFF")
        out.addWidget(self.btnOn); out.addWidget(self.btnOff); out.addStretch(1)
        layout.addLayout(out)

        # Indicators group
        grid = QtWidgets.QGridLayout()
        self.lblLamp = QtWidgets.QLabel("OFF")
        self.lblLamp.setStyleSheet("font-weight:bold; color:#aa0000")
        self.lblI = QtWidgets.QLabel("— (OFF)")
        self.lblV = QtWidgets.QLabel("— (OFF)")
        self.lblTarget = QtWidgets.QLabel("—")
        self.lblFlags = QtWidgets.QLabel("—")
        grid.addWidget(QtWidgets.QLabel("Lamp:"), 0, 0); grid.addWidget(self.lblLamp, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Present I:"), 1, 0); grid.addWidget(self.lblI, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Present V:"), 2, 0); grid.addWidget(self.lblV, 2, 1)
        grid.addWidget(QtWidgets.QLabel("Target:"), 3, 0); grid.addWidget(self.lblTarget, 3, 1)
        grid.addWidget(QtWidgets.QLabel("Status Flags:"), 4, 0); grid.addWidget(self.lblFlags, 4, 1)
        layout.addLayout(grid)

        # Log console
        self.txtLog = QtWidgets.QPlainTextEdit()
        self.txtLog.setReadOnly(True)
        self.txtLog.setMaximumBlockCount(1000)
        layout.addWidget(self.txtLog, 1)

        # Initial state
        self._set_connected(False)

    def _wire_controller(self):
        # Buttons
        self.btnConnect.clicked.connect(self._on_connect)
        self.btnDisconnect.clicked.connect(self.model.disconnect_instr)
        self.btnSetI.clicked.connect(self._on_set_current)
        self.btnSetV.clicked.connect(self._on_set_voltage)
        self.btnOn.clicked.connect(self.model.lamp_on)
        self.btnOff.clicked.connect(self.model.lamp_off)

        # Model signals
        self.model.connectedChanged.connect(self._set_connected)
        self.model.message.connect(lambda s: self._log(s))
        self.model.error.connect(lambda s: self._log(s, True))
        self.model.readingUpdated.connect(self._update_readings)

    # ----- controller slots -----
    def _on_connect(self):
        port = self.portEdit.text().strip()
        addr = self.addrSpin.value()
        self.model.connect_instr(port, addr)

    def _on_set_current(self):
        self.model.set_current(self.curSpin.value())

    def _on_set_voltage(self):
        self.model.set_voltage(self.voltSpin.value())

    # ----- view updates -----
    def _set_connected(self, ok: bool):
        self.btnConnect.setEnabled(not ok)  # noqa: E999 (PyQt expression style)
        self.btnDisconnect.setEnabled(ok)
        self.btnSetI.setEnabled(ok)
        self.btnSetV.setEnabled(ok)
        self.btnOn.setEnabled(ok)
        self.btnOff.setEnabled(ok)

    def _update_readings(self, data: dict):
        amps = data.get("amps", None)
        volts = data.get("volts", None)
        t = data.get("target", {})
        st = data.get("state", {})

        # Target readback is always shown (authoritative setpoint)
        if t:
            self.lblTarget.setText(f"{t.get('value', 0):.4f} {t.get('unit','')}")

        # Actual readings only when output is ON
        if st.get("on", False):
            self.lblI.setText(f"{amps:.4f} A" if amps is not None else "—")
            self.lblV.setText(f"{volts:.4f} V" if volts is not None else "—")
        else:
            self.lblI.setText("— (OFF)")
            self.lblV.setText("— (OFF)")

        # Flags (combine target + state flags)
        flags = set()
        flags.update(t.get("flags", []))
        flags.update(st.get("flags", []))
        self.lblFlags.setText(", ".join(sorted(flags)) if flags else "—")

        # Lamp indicator
        on = st.get("on", False)
        self.lblLamp.setText("ON" if on else "OFF")
        self.lblLamp.setStyleSheet(
            "font-weight:bold; color:#009900" if on else "font-weight:bold; color:#aa0000"
        )

        seeking = ("SEEKING_CURRENT" in t.get("flags", [])) or ("BUSY" in st.get("flags", []))
        suffix = "  (seeking…)" if seeking and st.get("on", False) else ""

        if st.get("on", False):
            self.lblI.setText((f"{amps:.4f} A" if amps is not None else "—") + suffix)
            self.lblV.setText((f"{volts:.4f} V" if volts is not None else "—") + suffix)
        else:
            self.lblI.setText("— (OFF)")
            self.lblV.setText("— (OFF)")

    def _log(self, msg: str, is_error: bool = False):
        color = "#aa0000" if is_error else "#444"
        safe = html.escape(msg)
        self.txtLog.appendHtml(f'<span style="color:{color}">{safe}</span>')

    # Close gracefully
    def closeEvent(self, e: QtGui.QCloseEvent):
        try:
            self.model.disconnect_instr()
        except Exception:
            traceback.print_exc()
        e.accept()


# =========================
# ========  Main  =========
# =========================

def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()