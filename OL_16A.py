#!/usr/bin/env python3
"""
OL-16A serial controller (RS-232, framed protocol with address handshake).

Protocol (per OL-16A guide, Appendix C – summarized):
  1) Host sends: EOT (0xFF) then device ADDRESS (7-bit value from front panel).
     Device replies ACK (0x06) or NAK (0x15).
  2) Host sends a framed message:
        STX (0x02) + ASCII message + ETX (0x03) + CHK
     where CHK is a 7-bit checksum (sum of message bytes & 0x7F).
  3) Device replies with a framed response using the same STX/ETX/CHK form.
     (Some devices send an ACK/NAK after receiving the frame; we handle both.)

NOTES:
- Set the instrument to RS-232, 9600-8-N-1. Address default in this script = 1.
- Wire straight-through 3-wire: PC TX (DB9-3) -> OL RX (pin 2),
  PC RX (DB9-2) <- OL TX (pin 3), GND (DB9-5) <-> GND (pin 5).
- If silent: try swapping TX/RX (null-modem behavior) and ensure REMOTE mode.

High-level helpers included:
  - set_current(amps)
  - read_current()
  - lamp_on() / lamp_off()
  - select_lamp(slot)
  - read_target()      # returns target mode and value (per device response)
  - send_ascii("...")  # low-level: send an arbitrary ASCII message (e.g., "c")

Edit COMMAND MAP near the bottom if your manual shows slightly different mnemonics.
"""

from __future__ import annotations
import time
import serial
from dataclasses import dataclass

EOT = 0xFF
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03

class OL16AError(Exception):
    pass

@dataclass
class OL16AConfig:
    port: str = "/dev/tty.usbserial-1440"
    baudrate: int = 9600
    address: int = 1          # matches front-panel setting
    timeout: float = 1.0      # read timeout (s)
    settle: float = 0.15      # delay after writes before reads (s)
    assert_dtr: bool = True   # some legacy gear needs DTR/RTS true
    assert_rts: bool = True

class OL16A:
    def __init__(self, cfg: OL16AConfig | None = None):
        self.cfg = cfg or OL16AConfig()
        if not (0 <= self.cfg.address <= 127):
            raise ValueError("Address must be 0..127 (7-bit)")

        self.ser = serial.Serial(
            self.cfg.port,
            baudrate=self.cfg.baudrate,
            timeout=self.cfg.timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False, rtscts=False, dsrdtr=self.cfg.assert_dtr
        )
        # Optionally assert RTS as well (without enabling RTS/CTS flow control).
        try:
            self.ser.setRTS(bool(self.cfg.assert_rts))
        except Exception:
            pass
        time.sleep(0.2)

    # ---------- framing / checksum ----------

    @staticmethod
    def _checksum7(msg_bytes: bytes) -> int:
        """7-bit accumulative checksum = sum(msg bytes) & 0x7F."""
        return sum(msg_bytes) & 0x7F

    def _expect_byte(self, expect_set: set[int]) -> int:
        b = self.ser.read(1)
        if not b:
            raise OL16AError("Timeout waiting for byte")
        val = b[0]
        if val not in expect_set:
            raise OL16AError(f"Unexpected byte 0x{val:02X}, expected one of {[hex(x) for x in expect_set]}")
        return val

    def _handshake(self) -> None:
        """EOT + ADDR, expect ACK (or NAK -> raise)."""
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self.ser.write(bytes([EOT, self.cfg.address & 0x7F]))
        b = self._expect_byte({ACK, NAK})
        if b == NAK:
            raise OL16AError("Instrument sent NAK on EOT/ADDR")

    def _send_frame(self, ascii_msg: str) -> None:
        """Send STX + ascii + ETX + CHK."""
        payload = ascii_msg.encode("ascii")
        chk = self._checksum7(payload)
        frame = bytes([STX]) + payload + bytes([ETX, chk])
        self.ser.write(frame)
        self.ser.flush()

    def _read_frame(self) -> str:
        """
        Read STX, then bytes until ETX, then one checksum byte.
        Validate checksum over the message bytes only (per guide).
        Return ASCII string of message.
        """
        # Read until STX
        start = self.ser.read(1)
        if not start:
            raise OL16AError("Timeout waiting for STX")
        if start[0] != STX:
            # Some units may send ACK before STX; tolerate an initial ACK/NAK.
            if start[0] in (ACK, NAK):
                # try reading next byte for STX
                start2 = self.ser.read(1)
                if not start2 or start2[0] != STX:
                    raise OL16AError(f"Expected STX, got 0x{start[0]:02X} then {start2 and hex(start2[0])}")
            else:
                raise OL16AError(f"Expected STX, got 0x{start[0]:02X}")

        # Collect bytes until ETX
        msg_bytes = bytearray()
        while True:
            b = self.ser.read(1)
            if not b:
                raise OL16AError("Timeout reading response (waiting for ETX)")
            if b[0] == ETX:
                break
            msg_bytes.append(b[0])

        # Read checksum
        chk_b = self.ser.read(1)
        if not chk_b:
            raise OL16AError("Timeout waiting for checksum")
        recv_chk = chk_b[0] & 0x7F
        calc_chk = self._checksum7(bytes(msg_bytes))
        if recv_chk != calc_chk:
            # Some firmwares include ETX in checksum; if mismatch, try that once.
            alt_chk = (calc_chk + ETX) & 0x7F
            if recv_chk != alt_chk:
                raise OL16AError(f"Checksum mismatch: recv=0x{recv_chk:02X}, calc=0x{calc_chk:02X} (alt=0x{alt_chk:02X})")

        # Return ASCII
        try:
            return bytes(msg_bytes).decode("ascii").strip()
        except UnicodeDecodeError:
            # Fallback: return repr of bytes
            return repr(bytes(msg_bytes))

    # ---------- public API ----------

    def transact(self, ascii_msg: str, expect_reply: bool = True) -> str | None:
        """Do a full transaction: EOT/ADDR -> ACK, then framed send, then read framed reply."""
        # 1) handshake
        self._handshake()
        time.sleep(self.cfg.settle)

        # 2) send frame
        self._send_frame(ascii_msg)
        time.sleep(self.cfg.settle)

        # 3) read framed reply
        if expect_reply:
            return self._read_frame()
        return None

    # --- convenience wrappers (edit names if your manual uses slightly different mnemonics)

    def set_current(self, amps: float) -> str:
        """
        Set current target. Manual typically shows uppercase command for SET.
        Example payload: 'C 1.234'
        """
        return self.transact(f"C {amps:.6f}")  # higher precision, device will round/display its format

    def read_current(self) -> float:
        """
        Read present current. Manual typically shows lowercase for READ: 'c'
        Returns the numeric value parsed from a response like 'C 1.234 1F'.
        """
        resp = self.transact("c")
        # Try to parse the first float in the response
        if not resp:
            raise OL16AError("Empty response to current read")
        # Responses often look like: 'C 1.234 1F' (value then status hex)
        parts = resp.replace(",", " ").split()
        for tok in parts:
            try:
                return float(tok)
            except ValueError:
                continue
        raise OL16AError(f"Unable to parse current from response: {resp!r}")

    def lamp_on(self) -> str:
        """Lamp/output ON. Example payload: 'B 1'."""
        return self.transact("B 1")

    def lamp_off(self) -> str:
        """Lamp/output OFF. Example payload: 'B 0'."""
        return self.transact("B 0")

    def select_lamp(self, slot: int) -> str:
        """Select lamp setup number (0..9 typically). Example payload: 'S 2'."""
        if not (0 <= slot <= 9):
            raise ValueError("Lamp slot must be 0..9")
        return self.transact(f"S {slot}")

    def read_target(self) -> str:
        """
        Request target (mode + value).
        Example response formats vary (e.g., 't 2 5.00 A 20').
        Return raw string so caller can interpret mode.
        """
        return self.transact("t")

    # Low-level escape hatch
    def send_ascii(self, message: str) -> str:
        """Send an arbitrary ASCII payload and return raw ASCII reply."""
        return self.transact(message)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# ----------------- Example usage -----------------
if __name__ == "__main__":
    cfg = OL16AConfig(
        port="/dev/tty.usbserial-1440",
        baudrate=9600,
        address=1,
        timeout=1.0,
        settle=0.15,
        assert_dtr=True,
        assert_rts=True,
    )
    ol = OL16A(cfg)
    try:
        # Safe probe: read target and current with output presumed OFF
        print("Reading target:", ol.read_target())
        cur = ol.read_current()
        print(f"Present current: {cur:.6f} A")

        # Toggle output with a tiny current as a sanity test (adjust to your safe dummy load!)
        print("Setting current to 0.050 A ...")
        print("Resp:", ol.set_current(0.050))
        print("Lamp ON:", ol.lamp_on())
        time.sleep(0.5)
        print("Reading current:", ol.read_current())
        print("Lamp OFF:", ol.lamp_off())

    except OL16AError as e:
        print("[OL16AError]", e)
    except Exception as e:
        print("[Unexpected error]", e)
    finally:
        ol.close()