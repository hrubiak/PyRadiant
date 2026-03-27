#!/usr/bin/env python3
# EPICS IOC for Optronic Laboratories OL-16A (asyncio + pythonSoftIOC)
# Requires: pythonSoftIOC (softioc, builder), and your working ol16a_driver (OL16A/OL16AConfig)

import asyncio
import math
import os
import time
from typing import Optional
from softioc import softioc, builder

# ---- backend driver you already have ----
from ol16a_driver import OL16A, OL16AConfig

# Debug flag (1=verbose, 0=quiet). Override with OL16A_DEBUG=0/1
DEBUG = 1 #int(os.environ.get("OL16A_DEBUG", "1"))

def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


class OL16AIOC:
    """
    Async wrapper around OL16A using asyncio + thread offload + a single asyncio.Lock
    to serialize access across PV callbacks and the poller.
    """

    def __init__(self, prefix: str = "OL16A:"):
        dprint("[IOC] __init__ starting")
        self.prefix = prefix
        self.dev: Optional[OL16A] = None
        self.lock = asyncio.Lock()
        self.poll_task: Optional[asyncio.Task] = None
        self.poll_ms = 350

        # defaults (editable before connect)
        self._port = os.environ.get("OL16A_PORT", "/dev/tty.usbserial-1440")
        self._addr = int(os.environ.get("OL16A_ADDR", "1"))

        dprint(f"[IOC] prefix={self.prefix} port={self._port} addr={self._addr}")
        builder.SetDeviceName(self.prefix)

        # --- PVs ---
        dprint("[IOC] Defining PVs")

        # config / connection
        self.pv_port = builder.stringOut(self._p("PORT"), initial_value=self._port,
                                         on_update=self._on_set_port, always_update=True)
        self.pv_addr = builder.longOut(self._p("ADDR"), initial_value=self._addr,
                                       on_update=self._on_set_addr, always_update=True)
        self.pv_connect = builder.boolOut(self._p("CONNECT"),
                                          on_update=self._on_connect_cmd, ZNAM="DISCONNECT", ONAM="CONNECT")
        self.pv_connected = builder.boolIn(self._p("CONNECTED_RBV"), initial_value=False)

        # status / heartbeat
        self.pv_state   = builder.stringIn(self._p("STATE_RBV"), initial_value="DISCONNECTED")
        self.pv_info    = builder.stringIn(self._p("INFO_RBV"), initial_value="")
        self.pv_conn_ts = builder.stringIn(self._p("CONN_TS_RBV"), initial_value="-")
        self.pv_uptime  = builder.longIn(self._p("UPTIME_S_RBV"), initial_value=0)
        self.pv_hb      = builder.longIn(self._p("HEARTBEAT"), initial_value=0)

        # polling interval
        self.pv_poll_ms = builder.longOut(self._p("POLL_MS"), initial_value=self.poll_ms,
                                          on_update=self._on_set_poll_ms, always_update=True)

        # slot select
        self.pv_slot_set = builder.longOut(self._p("SLOT_SET"), initial_value=1,
                                           on_update=self._on_set_slot, always_update=True)
        self.pv_slot_rbv = builder.longIn(self._p("SLOT_RBV"), initial_value=0)

        # output on/off
        self.pv_out_cmd = builder.boolOut(self._p("OUTPUT_CMD"),
                                          on_update=self._on_output_cmd, ZNAM="OFF", ONAM="ON")
        self.pv_out_rbv = builder.boolIn(self._p("OUTPUT_RBV"), initial_value=False)

        # targets (setpoints) and target readbacks
        self.pv_i_set = builder.aOut(self._p("I_SET"), initial_value=0.200, on_update=self._on_set_current,
                                     always_update=True, EGU="A", LOPR=0.0, HOPR=20.0, PREC=6)
        self.pv_i_rbv = builder.aIn(self._p("I_RBV"), initial_value=float("nan"), EGU="A", PREC=6)

        self.pv_v_set = builder.aOut(self._p("V_SET"), initial_value=1.000, on_update=self._on_set_voltage,
                                     always_update=True, EGU="V", LOPR=0.0, HOPR=50.0, PREC=6)
        self.pv_v_rbv = builder.aIn(self._p("V_RBV"), initial_value=float("nan"), EGU="V", PREC=6)

        # measured present values (only meaningful when OUTPUT=ON)
        self.pv_imeas = builder.aIn(self._p("IMEAS_RBV"), initial_value=float("nan"), EGU="A", PREC=6)
        self.pv_vmeas = builder.aIn(self._p("VMEAS_RBV"), initial_value=float("nan"), EGU="V", PREC=6)

        # status bits/flags
        self.pv_stat_hex = builder.stringIn(self._p("STATUS_HEX_RBV"), initial_value="00")
        self.pv_flags    = builder.stringIn(self._p("FLAGS_RBV"), initial_value="OK/IDLE")
        self.pv_busy     = builder.boolIn(self._p("BUSY_RBV"), initial_value=False)

        # misc last error
        self.pv_last_err = builder.stringIn(self._p("LAST_ERROR_RBV"), initial_value="")

        self._t0 = time.time()
        self._hb_task: Optional[asyncio.Task] = None

        dprint("[IOC] Loading EPICS database…")
        builder.LoadDatabase()
        dprint("[IOC] Database loaded OK")

    # ------------- helpers -------------
    def _p(self, name: str) -> str:
        return f"{self.prefix}{name}"

    async def _dev_call(self, func, *args, **kwargs):
        """Serialize access and run blocking backend calls in a thread."""
        dprint(f"[IOC] _dev_call -> {getattr(func, '__name__', func)} args={args} kwargs={kwargs}")
        async with self.lock:
            try:
                result = await asyncio.to_thread(func, *args, **kwargs)
                dprint(f"[IOC] _dev_call <- OK {getattr(func, '__name__', func)}")
                return result
            except Exception as e:
                dprint(f"[IOC] _dev_call <- EXC {e}")
                raise

    def _note_error(self, msg: str):
        dprint(f"[IOC] ERROR: {msg}")
        self.pv_last_err.set(str(msg))

    # ------------- PV callbacks -------------
    def _on_set_port(self, v: str):
        dprint(f"[IOC] _on_set_port: {v}")
        self._port = v

    def _on_set_addr(self, v: int):
        dprint(f"[IOC] _on_set_addr: {v}")
        try:
            self._addr = int(v)
        except Exception:
            self._note_error(f"Bad ADDR value {v}")

    def _on_set_poll_ms(self, v: int):
        dprint(f"[IOC] _on_set_poll_ms: {v}")
        try:
            ms = int(max(100, min(5000, v)))
            self.poll_ms = ms
        except Exception:
            pass  # ignore

    async def _connect(self):
        if self.dev is not None:
            dprint("[IOC] _connect: already connected")
            return
        cfg = OL16AConfig(port=self._port, address=self._addr, debug=False)
        dprint(f"[IOC] _connect: creating OL16A(port={self._port}, addr={self._addr})")
        try:
            self.dev = await asyncio.to_thread(OL16A, cfg)
            dprint("[IOC] _connect: OL16A object created")
            # read initial target & slot
            t = await self._dev_call(self.dev.read_target)
            dprint(f"[IOC] _connect: initial target {t}")
            self.pv_slot_rbv.set(int(t.get("mode", 0)))
            # set connection PVs
            self.pv_connected.set(True)
            self.pv_state.set("CONNECTED")
            self.pv_info.set(f"PORT={self._port} ADDR={self._addr}")
            self.pv_conn_ts.set(time.strftime("%Y-%m-%d %H:%M:%S"))
            self._note_error("")
            # start tasks
            self._start_heartbeat()
            self._start_poller()
            dprint("[IOC] _connect: success")
        except Exception as e:
            self.dev = None
            self.pv_connected.set(False)
            self._note_error(f"Connect failed: {e}")
            dprint(f"[IOC] _connect: FAILED {e}")

    async def _disconnect(self):
        dprint("[IOC] _disconnect: requested")
        if self.dev is None:
            dprint("[IOC] _disconnect: already disconnected")
            return
        self._stop_poller()
        try:
            await self._dev_call(self.dev.close)
        except Exception:
            pass
        self.dev = None
        # reflect disconnect in PVs
        self.pv_connected.set(False)
        self.pv_state.set("DISCONNECTED")
        self.pv_info.set("")
        self.pv_conn_ts.set("-")
        # clear measured PVs to NaN on disconnect
        for pv in (self.pv_imeas, self.pv_vmeas, self.pv_i_rbv, self.pv_v_rbv):
            pv.set(float("nan"))
        self.pv_out_rbv.set(False)
        self.pv_flags.set("DISCONNECTED")
        self.pv_stat_hex.set("--")
        self.pv_busy.set(False)
        self._stop_heartbeat()
        dprint("[IOC] _disconnect: done")

    def _start_heartbeat(self):
        dprint("[IOC] _start_heartbeat")
        if self._hb_task is None:
            self._hb_task = asyncio.create_task(self._hb_loop())

    def _stop_heartbeat(self):
        dprint("[IOC] _stop_heartbeat")
        if self._hb_task:
            self._hb_task.cancel()
            self._hb_task = None

    async def _hb_loop(self):
        dprint("[IOC] hb loop started")
        while True:
            try:
                self.pv_hb.set((self.pv_hb.get() or 0) + 1)
                self.pv_uptime.set(int(time.time() - self._t0))
            except Exception:
                pass
            await asyncio.sleep(1.0)

    def _on_connect_cmd(self, val: int):
        dprint(f"[IOC] _on_connect_cmd: {val}")
        # 1 = connect, 0 = disconnect
        if val:
            asyncio.create_task(self._connect())
        else:
            asyncio.create_task(self._disconnect())

    def _on_set_slot(self, slot: int):
        dprint(f"[IOC] _on_set_slot: {slot}")
        if self.dev is None:
            self._note_error("Select slot while disconnected")
            return
        async def do():
            try:
                s = int(slot)
                s = max(1, min(9, s))
                await self._dev_call(self.dev.select_lamp, s)
                t = await self._dev_call(self.dev.read_target)
                self.pv_slot_rbv.set(int(t.get("mode", s)))
                await self._update_targets_from_target(t)
                dprint(f"[IOC] slot now {self.pv_slot_rbv.get()}")
            except Exception as e:
                self._note_error(f"Select slot failed: {e}")
        asyncio.create_task(do())

    def _on_output_cmd(self, on: int):
        dprint(f"[IOC] _on_output_cmd: {on}")
        if self.dev is None:
            self._note_error("Output cmd while disconnected")
            self.pv_out_cmd.set(0)  # revert
            return
        async def do():
            try:
                if on:
                    await self._dev_call(self.dev.lamp_on)
                else:
                    await self._dev_call(self.dev.lamp_off)
                st = await self._dev_call(self.dev.read_output_state)
                self.pv_out_rbv.set(bool(st.get("on", False)))
                dprint(f"[IOC] output state -> {self.pv_out_rbv.get()}")
            except Exception as e:
                self._note_error(f"Output cmd failed: {e}")
                try:
                    st = await self._dev_call(self.dev.read_output_state)
                    self.pv_out_rbv.set(bool(st.get("on", False)))
                except Exception:
                    pass
                self.pv_out_cmd.set(1 if self.pv_out_rbv.get() else 0)
        asyncio.create_task(do())

    def _on_set_current(self, amps: float):
        dprint(f"[IOC] _on_set_current: {amps}")
        if self.dev is None:
            self._note_error("Set current while disconnected")
            return
        async def do():
            try:
                t = await self._dev_call(self.dev.set_current, float(amps))
                await self._update_targets_from_target(t)
                dprint(f"[IOC] target after I set: {t}")
            except Exception as e:
                self._note_error(f"Set current failed: {e}")
        asyncio.create_task(do())

    def _on_set_voltage(self, volts: float):
        dprint(f"[IOC] _on_set_voltage: {volts}")
        if self.dev is None:
            self._note_error("Set voltage while disconnected")
            return
        async def do():
            try:
                t = await self._dev_call(self.dev.set_voltage, float(volts))
                await self._update_targets_from_target(t)
                dprint(f"[IOC] target after V set: {t}")
            except Exception as e:
                self._note_error(f"Set voltage failed: {e}")
        asyncio.create_task(do())

    async def _update_targets_from_target(self, t: dict):
        """Update I_RBV/V_RBV based on 't' readback."""
        try:
            unit = t.get("unit")
            val = float(t.get("value"))
            if unit == "A":
                self.pv_i_rbv.set(val)
            elif unit == "V":
                self.pv_v_rbv.set(val)
            ss = t.get("status", "00")
            flags = ", ".join(t.get("flags", [])) or "OK/IDLE"
            self.pv_stat_hex.set(ss)
            self.pv_flags.set(flags)
            self.pv_busy.set(("BUSY" in flags) or ("SEEKING_CURRENT" in flags))
            dprint(f"[IOC] update_targets: unit={unit} val={val} ss={ss} flags={flags}")
        except Exception as e:
            dprint(f"[IOC] update_targets: EXC {e}")

    # ------------- poller -------------
    def _start_poller(self):
        dprint("[IOC] _start_poller")
        if self.poll_task is None:
            self.poll_task = asyncio.create_task(self._poll_loop())

    def _stop_poller(self):
        dprint("[IOC] _stop_poller")
        if self.poll_task:
            self.poll_task.cancel()
            self.poll_task = None

    async def _poll_loop(self):
        dprint("[IOC] poll loop started")
        loop_count = 0
        while self.dev is not None:
            try:
                t = await self._dev_call(self.dev.read_target)
                st = await self._dev_call(self.dev.read_output_state)

                self.pv_slot_rbv.set(int(t.get("mode", 0)))
                await self._update_targets_from_target(t)
                self.pv_out_rbv.set(bool(st.get("on", False)))

                if st.get("on", False):
                    try:
                        i = await self._dev_call(self.dev.read_current)
                        self.pv_imeas.set(float(i.get("amps", math.nan)))
                    except Exception as e:
                        dprint(f"[IOC] poll: read_current EXC {e}")
                        self.pv_imeas.set(float("nan"))
                    try:
                        v = await self._dev_call(self.dev.read_voltage)
                        self.pv_vmeas.set(float(v.get("volts", math.nan)))
                    except Exception as e:
                        dprint(f"[IOC] poll: read_voltage EXC {e}")
                        self.pv_vmeas.set(float("nan"))
                else:
                    self.pv_imeas.set(float("nan"))
                    self.pv_vmeas.set(float("nan"))

                # print every ~10 cycles to avoid spam
                loop_count += 1
                if loop_count % 10 == 0:
                    dprint(f"[IOC] poll #{loop_count}: slot={self.pv_slot_rbv.get()} on={self.pv_out_rbv.get()} "
                           f"Irbv={self.pv_i_rbv.get()} Vrbv={self.pv_v_rbv.get()} "
                           f"Imeas={self.pv_imeas.get()} Vmeas={self.pv_vmeas.get()}")
            except Exception as e:
                self._note_error(f"Poll error: {e}")
            await asyncio.sleep(self.poll_ms / 1000.0)


# -------------------- asyncio softIOC startup --------------------
OL16A_AUTOCONNECT = bool(int(os.environ.get("OL16A_AUTOCONNECT", "1")))

def _print_ca_env():
    # helps diagnose addr list issues when starting the IOC
    keys = [
        "EPICS_CAS_INTF_ADDR_LIST", "EPICS_CAS_AUTO_BEACON_ADDR_LIST", "EPICS_CAS_BEACON_ADDR_LIST",
        "EPICS_CAS_SERVER_PORT", "EPICS_CA_AUTO_ADDR_LIST", "EPICS_CA_ADDR_LIST",
        "EPICS_CA_NAME_SERVERS", "EPICS_CA_SERVER_PORT",
    ]
    dprint("[IOC] CA env snapshot:")
    for k in keys:
        v = os.environ.get(k, "")
        dprint(f"   {k}={v!r}")


async def start_softioc(prefix: str = "OL16A:"):
    dprint("[IOC] start_softioc()")
    _print_ca_env()
    ioc = OL16AIOC(prefix=prefix)

    # Use asyncio loop as dispatcher (no cothread)
    loop = asyncio.get_running_loop()
    def asyncio_dispatcher(func, *args, **kwargs):
        dprint(f"[IOC] dispatcher: scheduling {getattr(func, '__name__', func)}")
        loop.call_soon(func, *args)

    dprint("[IOC] calling iocInit()")
    softioc.iocInit(dispatcher=asyncio_dispatcher)
    dprint("[IOC] iocInit complete")

    if OL16A_AUTOCONNECT:
        dprint("[IOC] Autoconnect requested (OL16A_AUTOCONNECT=1)")
        ioc.pv_connect.set(1)

    dprint("[IOC] entering interactive_ioc()")
    await softioc.interactive_ioc(globals())


if __name__ == "__main__":
    dprint("[IOC] __main__ starting")
    asyncio.run(start_softioc(prefix=os.environ.get("OL16A_PREFIX", "OL16A:")))