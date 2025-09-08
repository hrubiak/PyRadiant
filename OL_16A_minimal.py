#!/usr/bin/env python3
import re, time, serial
from dataclasses import dataclass
from typing import Optional

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
    settle: float = 0.20        # pacing between handshake and frame
    post_frame_wait: float = 0.08
    max_polls: int = 8
    poll_gap: float = 0.06
    assert_dtr: bool = True
    assert_rts: bool = True
    debug: bool = False
    # Optional safety ceilings. Set to None to disable enforcement.
    max_current: Optional[float] = 2.5
    max_voltage: Optional[float] = 20.0

class OL16A:
    # Status bit names per manual
    _STATUS_BIT_NAMES = {
        7: "BUSY",
        4: "LAMP_ON",
        1: "SEEKING_CURRENT",
        # others reserved
    }

    # ---------- init ----------
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

        # Always select Lamp 1 at startup (your request) — but only change target if unsafe ceilings are configured
        print("Selecting lamp 1:", self.select_lamp(1))
        t = self.read_target()
        print("Lamp 1 initial target:", t)
        if t["unit"] not in ("A", "V", "W"):
            raise RuntimeError("Unexpected target unit on startup.")
        # If ceilings are set, gently enforce them by lowering the target (never turns output on/off)
        if self.cfg.max_current is not None and t["unit"] == "A" and t["value"] > self.cfg.max_current:
            print(f"Lowering current target from {t['value']} A to {min(0.200, self.cfg.max_current)} A")
            self.set_current(min(0.200, self.cfg.max_current))
        if self.cfg.max_voltage is not None and t["unit"] == "V" and t["value"] > self.cfg.max_voltage:
            new_v = min(5.0, self.cfg.max_voltage)
            print(f"Lowering voltage target from {t['value']} V to {new_v} V")
            self.set_voltage(new_v)

    # ---------- debug ----------
    def _dbg(self, msg: str):
        if self.cfg.debug:
            print(msg, flush=True)

    # ---------- low-level I/O ----------
    def _expect(self, wanted, label):
        b = self.ser.read(1)
        if not b: raise TimeoutError(f"Timeout waiting for {label}")
        v = b[0]
        if v not in (wanted if isinstance(wanted,(set,tuple,list)) else (wanted,)):
            raise RuntimeError(f"Unexpected 0x{v:02X} while waiting for {label}")
        return v

    def _write_frame(self, payload: bytes):
        # handshake
        self.ser.reset_input_buffer(); self.ser.reset_output_buffer()
        self._dbg(f"> HS: EOT {HS_EOT:#04x}, ADDR {self.addr7:#04x}")
        self.ser.write(bytes([HS_EOT, self.addr7]))
        r = self._expect({ACK,NAK}, "ACK/NAK after EOT+ADDR")
        self._dbg(f"< HS resp: {r:#04x}")
        if r == NAK:
            raise RuntimeError("NAK to write handshake")
        time.sleep(self.cfg.settle)

        # frame: SOH payload ETX CHK ; CHK=sum7(ADDR+payload+ETX)
        tx_chk = sum7(bytes([self.addr7]) + payload + bytes([ETX]))
        frame = bytes([SOH]) + payload + bytes([ETX, tx_chk])
        self._dbg(f"> TX frame: SOH {SOH:#04x} ... ETX {ETX:#04x} CHK {tx_chk:#04x} payload={payload!r}")
        self.ser.write(frame); self.ser.flush()

        r = self._expect({ACK,NAK}, "ACK/NAK after frame")
        self._dbg(f"< frame resp: {r:#04x}")
        if r == NAK:
            raise RuntimeError("NAK to frame (checksum/message)")
        time.sleep(self.cfg.post_frame_wait)

    def _read_frame_once(self):
        """Poll once; return payload bytes or None if no data."""
        self.ser.reset_input_buffer(); self.ser.reset_output_buffer()
        self._dbg(f"> RD poll: EOT {HS_EOT:#04x}, ADDR|0x80 {(self.addr7|0x80):#04x}")
        self.ser.write(bytes([HS_EOT, self.addr7 | 0x80]))
        b = self.ser.read(1)
        if not b:
            self._dbg("< RD poll: timeout")
            return None
        if b[0] == NAK:
            self._dbg("< RD poll: NAK (no data)")
            return None
        if b[0] != ACK:
            self._dbg(f"< RD poll: unexpected {b[0]:#04x}")
            return None

        # Expect STX … ETX + CHK
        h = self.ser.read(1)
        if h != bytes([STX]):
            self._dbg(f"< RD: missing STX (got {h})")
            return None
        payload = bytearray()
        while True:
            c = self.ser.read(1)
            if not c:
                self._dbg("< RD: timeout in payload")
                return None
            if c[0] == ETX:
                break
            payload.append(c[0])
        rchk = self.ser.read(1)
        if not rchk:
            self._dbg("< RD: missing checksum")
            return None
        rchk = rchk[0] & 0x7F
        ok = (rchk == sum7(bytes([STX]) + bytes(payload) + bytes([ETX])))
        self.ser.write(bytes([ACK]))  # always ACK to be polite
        if not ok:
            raise RuntimeError(f"Reply checksum mismatch (rx=0x{rchk:02X})")
        self._dbg(f"< RD: payload={bytes(payload)!r}, chk=0x{rchk:02X} (ok)")
        return bytes(payload)

    def _read_frame(self) -> bytes:
        for _ in range(self.cfg.max_polls):
            got = self._read_frame_once()
            if got is not None:
                return got
            time.sleep(self.cfg.poll_gap)
        raise RuntimeError("No data pending (NAK/timeouts on read poll)")

    def _poll_once(self):
        try:
            return self._read_frame_once()
        except Exception:
            return None

    def transact(self, ascii_msg: str, expect_reply: bool = True) -> str | None:
        """Send an ASCII payload; optionally allow no immediate reply."""
        self._write_frame(ascii_msg.encode("ascii"))
        if not expect_reply:
            # best-effort quick poll; ignore if nothing is pending
            for _ in range(2):
                got = self._poll_once()
                if got is not None:
                    return got.decode("ascii", errors="replace").strip()
                time.sleep(self.cfg.poll_gap)
            return None
        payload = self._read_frame()
        return payload.decode("ascii", errors="replace").strip()

    # ---------- regex parsers ----------
    _re_c = re.compile(r"^\s*[Cc]\s+([+-]?\d+(?:\.\d+)?)\s+([0-9A-Fa-f]{2})\s*$")        # present current
    _re_v = re.compile(r"^\s*[Vv]\s+([+-]?\d+(?:\.\d+)?)\s+([0-9A-Fa-f]{2})\s*$")        # present voltage
    _re_t = re.compile(r"^\s*t\s+(\d+)\s+([+-]?\d+(?:\.\d+)?)\s+([AVW])\s+([0-9A-Fa-f]{2})\s*$")  # target
    _re_b = re.compile(r"^\s*b\s+([01])\s+([0-9A-Fa-f]{2})\s*$")                         # output state

    # ---------- status decoding ----------
    def _decode_status(self, ss_hex: str) -> list[str]:
        """Decode status hex (e.g. '92') into human-readable flags."""
        try:
            val = int(ss_hex, 16) & 0xFF
        except Exception:
            return [f"INVALID({ss_hex!r})"]
        flags = [name for bit, name in sorted(self._STATUS_BIT_NAMES.items(), reverse=True)
                 if val & (1 << bit)]
        return flags or ["OK/IDLE"]

    # ---------- readers (simple names, include flags) ----------
    def read_current(self) -> dict:
        """Return {'amps': float, 'status': 'SS', 'flags': [...]}"""
        txt = self.transact("c")
        m = self._re_c.match(txt)
        if not m: raise RuntimeError(f"Unexpected c-reply: {txt!r}")
        amps = float(m.group(1)); ss = m.group(2).upper()
        return {"amps": amps, "status": ss, "flags": self._decode_status(ss)}

    def read_voltage(self) -> dict:
        """Return {'volts': float, 'status': 'SS', 'flags': [...]}"""
        txt = self.transact("v")
        m = self._re_v.match(txt)
        if not m: raise RuntimeError(f"Unexpected v-reply: {txt!r}")
        volts = float(m.group(1)); ss = m.group(2).upper()
        return {"volts": volts, "status": ss, "flags": self._decode_status(ss)}

    def read_target(self) -> dict:
        """Return {'mode': int, 'value': float, 'unit': 'A|V|W', 'status': 'SS', 'flags': [...]}"""
        txt = self.transact("t")
        m = self._re_t.match(txt)
        if not m: raise RuntimeError(f"Unexpected t-reply: {txt!r}")
        mode = int(m.group(1)); val = float(m.group(2)); unit = m.group(3)
        ss = m.group(4).upper()
        return {"mode": mode, "value": val, "unit": unit, "status": ss, "flags": self._decode_status(ss)}

    def read_output_state(self) -> dict:
        """Return {'on': bool, 'status': 'SS', 'flags': [...]}"""
        txt = self.transact("b")
        m = self._re_b.match(txt)
        if not m: raise RuntimeError(f"Unexpected b-reply: {txt!r}")
        state = bool(int(m.group(1))); ss = m.group(2).upper()
        return {"on": state, "status": ss, "flags": self._decode_status(ss)}

    # ---------- lamp selection ----------
    def get_lamp(self) -> int:
        """Return current lamp setup number (1..9) via 't' first field."""
        return int(self.read_target()["mode"])

    def select_lamp(self, slot: int) -> str:
        """Select lamp setup (1..9). Returns the instrument echo (e.g., 'S  1 00')."""
        if not (1 <= slot <= 9):
            raise ValueError("slot must be 1..9")
        echo = self.transact(f"S {slot}")
        time.sleep(max(self.cfg.post_frame_wait, 0.05))
        actual = self.get_lamp()
        if actual != slot:
            raise RuntimeError(f"Requested slot {slot} but instrument reports slot {actual}")
        return echo

    # ---------- setters (pure: set target only, never change output state) ----------
    def set_current(self, amps: float) -> dict:
        """Set current target using 'C <value>' and return the new target dict."""
        if self.cfg.max_current is not None and amps > self.cfg.max_current:
            raise ValueError(f"Requested {amps:.3f} A exceeds configured max_current={self.cfg.max_current:.3f} A")
        _reply = self.transact(f"C {amps:.6f}", expect_reply=True)  # reply is 'C CV SS'
        time.sleep(max(self.cfg.post_frame_wait, 0.05))
        return self.read_target()

    def set_voltage(self, volts: float) -> dict:
        """Set voltage target using 'V <value>' and return the new target dict."""
        if self.cfg.max_voltage is not None and volts > self.cfg.max_voltage:
            raise ValueError(f"Requested {volts:.3f} V exceeds configured max_voltage={self.cfg.max_voltage:.3f} V")
        _reply = self.transact(f"V {volts:.6f}", expect_reply=True)  # reply is 'V VV SS'
        time.sleep(max(self.cfg.post_frame_wait, 0.05))
        return self.read_target()

    # ---------- verification helpers (require YOU to enable output first) ----------
    def verify_current(self, amps: float, tol: float = 0.005, timeout: float = 10.0) -> dict:
        """
        Verify actual current is within ±tol A of amps.
        Requires output to be ON. Returns read_current() dict or raises RuntimeError.
        """
        self.wait_until_stable(timeout=timeout)
        c = self.read_current()
        if abs(c["amps"] - amps) > tol:
            raise RuntimeError(f"Actual {c['amps']:.4f} A not within ±{tol:.4f} A of {amps:.4f} A")
        return c

    def verify_voltage(self, volts: float, tol: float = 0.02, timeout: float = 10.0) -> dict:
        """
        Verify actual voltage is within ±tol V of volts.
        Requires output to be ON. Returns read_voltage() dict or raises RuntimeError.
        """
        self.wait_until_stable(timeout=timeout)
        v = self.read_voltage()
        if abs(v["volts"] - volts) > tol:
            raise RuntimeError(f"Actual {v['volts']:.3f} V not within ±{tol:.3f} V of {volts:.3f} V")
        return v

    # ---------- output control (explicit; setters never touch these) ----------
    def lamp_on(self) -> str:
        return self.transact("B 1")

    def lamp_off(self) -> str:
        return self.transact("B 0")

    # ---------- stability ----------
    def wait_until_stable(self, timeout: float = 10.0, poll: float = 0.1) -> dict:
        """
        Wait until BUSY and SEEKING_CURRENT are clear.
        Returns read_current() dict (for convenience) when stable; raises on timeout.
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            info = self.read_current()
            flags = set(info["flags"])
            if "BUSY" not in flags and "SEEKING_CURRENT" not in flags:
                return info
            time.sleep(poll)
        raise TimeoutError("Instrument did not become stable before timeout")

    # ---------- misc ----------
    def send_raw(self, payload: str, expect_reply: bool = True) -> str | None:
        return self.transact(payload, expect_reply=expect_reply)

    def close(self):
        try: self.ser.close()
        except Exception: pass


# ---------- example usage ----------
if __name__ == "__main__":
    ol = OL16A(OL16AConfig(
        port="/dev/tty.usbserial-1440",
        address=1,
        debug=False,
        max_current=2.5,   # set None to disable enforcement
        max_voltage=3.0   # set None to disable enforcement
    ))
    try:
        print("Target:", ol.read_target())
        print("State:", ol.read_output_state())
        print("Present I:", ol.read_current())
        print("Present V:", ol.read_voltage())

        # Pure setters (do not change output)
        print("Set voltage to 1.000 V:", ol.set_voltage(1.000))
        #print("Set current to 0.200 A:", ol.set_current(0.200))

        # Turn ON explicitly (only when you're ready)
        print("Lamp ON:", ol.lamp_on())
        # Verify after ON (optional)
        print("Verify V:", ol.verify_voltage(1.000, tol=0.02, timeout=25.0))
        #print("Verify I:", ol.verify_current(0.200, tol=0.005, timeout=8.0))

        # Done
        print("Lamp OFF:", ol.lamp_off())

    finally:
        ol.close()