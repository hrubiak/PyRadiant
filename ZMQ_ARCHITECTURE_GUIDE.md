# ZMQ Architecture Guide — PyRadiant Reference Implementation

This document describes how ZMQ is implemented in PyRadiant and serves as a
blueprint for implementing the same architecture in any other PyQt6 worker app
in the T-view coordinator ecosystem.

PyRadiant plays **two distinct ZMQ roles** simultaneously:

| Role | Direction | Purpose |
|---|---|---|
| **Worker** (PULL) | Coordinator → PyRadiant | Receives job requests, loads/fits a file, returns results |
| **Publisher** (PUSH) | PyRadiant → epicsLogger | Sends trigger messages when a new fit is ready for logging |

These are fully independent and configured separately. This guide covers both.

---

## Part 1 — Worker (coordinator integration)

### 1.1 Socket topology

```
Coordinator  PUSH  bind  ──────────>  Worker  PULL  connect   tcp://127.0.0.1:<port>
Coordinator  PULL  bind  <──────────  Worker  PUSH  connect   tcp://127.0.0.1:<results_port>
Coordinator  REQ   connect  <───────  Worker  REP   bind      tcp://*:<health_port>
```

- The **coordinator binds** all its sockets; the **worker connects** (except the REP health socket, which the worker binds).
- One PUSH→PULL pair per worker. Each worker gets its own port in `config.yaml`.
- The health REP socket is optional but strongly recommended.

### 1.2 config.yaml structure

```yaml
ports:
  coordinator_results: 5554    # shared results port — all workers push results here

workers:
  my_worker_name:
    port: 5561                 # coordinator PUSH → worker PULL
    health_port: 5562          # worker REP  ← coordinator REQ (health/schema)
    script: run_myapp.py       # used by coordinator to identify this worker
    input_directory: /data/raw/my_worker
    output_directory: /data/processed/my_worker
```

### 1.3 Coordinator message types

#### Incoming: `"job"` message (coordinator → worker)

```json
{
    "type": "job",
    "timestamp": "11/28/2025, 18:23:22",
    "row": 309,
    "input_directory": "'/Volumes/data/raw/spectroradiometry'",
    "output_directory": "",
    "data": {
        "CCD_FileName": "\\\\server\\share\\path\\to\\20251128-Pt-dac_00064"
    }
}
```

Key points:
- `input_directory` may be wrapped in single or double quotes (pasted by user) — always strip with `.strip().strip("'\"")`
- `data` contains the original file path under some key. **Do not hard-code the key name** — use the `_find_path_value()` helper (see §1.6) to scan all values for a path-like string.
- The original path is typically a Windows UNC path. Strip it to basename and join with `input_directory` to get the local path.
- The path often has **no extension** — try `.spe`, `.SPE`, `.h5`, `.hdf5` in order.

#### Incoming: `"cursor"` message (epicsLogViewer → worker)

Same structure as `"job"` but `"type": "cursor"`. Signals the user moved the cursor in the log viewer — load the corresponding file and display it, but do not necessarily send a new result back.

#### Outgoing: `"result"` message (worker → coordinator)

```json
{
    "type": "result",
    "worker": "spectroradiometry",
    "row": 309,
    "timestamp": "11/28/2025, 18:23:22",
    "filename": "20251128-Pt-dac_00064.spe",
    "status": "ok",
    "message": "",
    "values": {
        "ds_temperature": 1821.4,
        "ds_temperature_error": 3.2,
        "us_temperature": 1834.1,
        "us_temperature_error": 4.5,
        "exposure_time": 0.5,
        "gain": 10
    }
}
```

- `"status"` is `"ok"` or `"error"`. On error, `"message"` contains the reason.
- `"values"` contains only JSON-safe numbers — NaN and Inf must be replaced with `null`.
- `"row"` and `"timestamp"` are echoed from the incoming job so the coordinator can match results.

#### Health socket: `"schema"` and `"ping"` queries

The coordinator REQ socket sends:

| Request | Response |
|---|---|
| `{"type": "ping"}` | `{"type": "pong", "status": "ready", "worker": "<name>"}` |
| `{"type": "schema"}` | `{"type": "schema_reply", "columns": [...]}` |

The `"schema"` response advertises what fields the worker puts in `"values"`:

```json
{
    "type": "schema_reply",
    "columns": [
        {"name": "ds_temperature",       "label": "DS Temperature",       "unit": "K"},
        {"name": "ds_temperature_error", "label": "DS Temperature Error", "unit": "K"},
        {"name": "exposure_time",        "label": "Exposure Time",        "unit": "s"}
    ]
}
```

`"name"` keys **must exactly match** the keys in `"values"` of every result message.

### 1.4 Module layout

```
my_app/
├── config.py                        # shared config helpers (same file across all workers)
├── config.yaml                      # shared config (same file across all workers)
└── my_app/controller/
    └── ZmqWorkerController.py       # ZmqListenerThread + ZmqWorkerController
```

### 1.5 ZmqListenerThread

Background `QThread` that owns all sockets. Uses `zmq.Poller` with 500 ms timeout so `stop()` works promptly. **Never process jobs inside `run()`** — always emit a signal.

```python
import json
from PyQt6 import QtCore

try:
    import zmq
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False

# Declare your schema once here — used by the health socket schema reply.
MY_SCHEMA = [
    {"name": "result_value_1", "label": "Human Label 1", "unit": "K"},
    {"name": "result_value_2", "label": "Human Label 2", "unit": ""},
    # ... one entry per key in the "values" dict of your result messages
]


class ZmqListenerThread(QtCore.QThread):

    job_received   = QtCore.pyqtSignal(dict)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, recv_port, results_port, health_port=None,
                 worker_name="my_worker", parent=None):
        super().__init__(parent)
        self.recv_port    = recv_port
        self.results_port = results_port
        self.health_port  = health_port
        self.worker_name  = worker_name
        self._stop_flag   = False
        self._results_socket = None
        self._context        = None

    def run(self):
        if not ZMQ_AVAILABLE:
            self.error_occurred.emit("pyzmq is not installed")
            return

        self._context = zmq.Context()

        receiver = self._context.socket(zmq.PULL)
        receiver.set(zmq.LINGER, 0)       # don't block on close
        receiver.connect(f"tcp://127.0.0.1:{self.recv_port}")

        self._results_socket = self._context.socket(zmq.PUSH)
        self._results_socket.set(zmq.LINGER, 0)
        self._results_socket.connect(f"tcp://127.0.0.1:{self.results_port}")

        health = None
        if self.health_port is not None:
            health = self._context.socket(zmq.REP)
            health.set(zmq.LINGER, 0)
            try:
                health.bind(f"tcp://*:{self.health_port}")
            except zmq.ZMQError as exc:
                # Port already in use — report and abort cleanly
                health.close()
                receiver.close()
                self._results_socket.close()
                self._context.term()
                self._context = None
                self._results_socket = None
                self.error_occurred.emit(f"Cannot bind health port {self.health_port}: {exc}")
                return

        poller = zmq.Poller()
        poller.register(receiver, zmq.POLLIN)
        if health is not None:
            poller.register(health, zmq.POLLIN)

        while not self._stop_flag:
            try:
                ready = dict(poller.poll(500))    # 500 ms — allows clean stop()

                if receiver in ready:
                    raw = receiver.recv()
                    msg = json.loads(raw)
                    self.job_received.emit(msg)   # ← processed on GUI thread

                if health is not None and health in ready:
                    try:
                        req = health.recv_json()
                    except Exception:
                        req = {}
                    req_type = req.get("type")
                    if req_type == "schema":
                        health.send_json({
                            "type":    "schema_reply",
                            "columns": MY_SCHEMA,
                        })
                    elif req_type == "ping" or req_type is None:
                        health.send_json({
                            "type":   "pong",
                            "status": "ready",
                            "worker": self.worker_name,
                        })
                    else:
                        health.send_json({
                            "type":    "error",
                            "message": f"unknown type: {req_type}",
                        })

            except zmq.ZMQError as exc:
                if not self._stop_flag:
                    self.error_occurred.emit(str(exc))
                break
            except Exception as exc:
                self.error_occurred.emit(str(exc))

        receiver.close()
        if health is not None:
            health.close()
        if self._results_socket:
            self._results_socket.close()
        self._context.term()
        self._results_socket = None
        self._context = None

    def send_result(self, result: dict):
        """Send result back to coordinator. Best-effort — call from GUI thread."""
        if self._results_socket is not None:
            try:
                self._results_socket.send(json.dumps(result).encode(), zmq.NOBLOCK)
            except Exception:
                pass

    def stop(self):
        self._stop_flag = True
```

### 1.6 ZmqWorkerController

High-level `QObject` that owns the thread, loads config, wires widget signals, and dispatches messages.

```python
import json
from PyQt6 import QtCore


class ZmqWorkerController(QtCore.QObject):

    status_changed = QtCore.pyqtSignal(str)   # "idle" | "listening" | "error"
    job_started    = QtCore.pyqtSignal(dict)
    job_finished   = QtCore.pyqtSignal(dict)

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self.widget        = widget
        self._thread       = None
        self._config       = {}
        self._recv_port    = None
        self._results_port = None
        self._health_port  = None
        self._input_dir    = None
        self._output_dir   = None
        self._worker_name  = "my_worker"
        self._last_dispatched_job    = None   # used to suppress stale re-dispatches
        self._last_dispatched_values = None

        self._connect_widget_signals()

    # ── Config ────────────────────────────────────────────────────────

    def load_config(self, path):
        import sys, os
        _root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from config import (load_config, get_worker_port, get_results_port,
                            get_worker_health_port, get_worker_input_directory,
                            get_worker_output_directory)
        self._config = load_config(path)

        MY_WORKER_NAME = "my_worker"       # ← key in config.yaml workers section
        MY_SCRIPT_NAME = "run_myapp.py"    # ← entry-point filename

        workers = self._config.get("workers", {})
        if MY_WORKER_NAME in workers:
            worker_name = MY_WORKER_NAME
        else:
            worker_name = None
            for name, info in workers.items():
                if os.path.basename(info.get("script", "")) == MY_SCRIPT_NAME:
                    worker_name = name
                    break

        self._worker_name  = worker_name or MY_WORKER_NAME
        self._recv_port    = get_worker_port(worker_name, self._config)
        self._results_port = get_results_port(self._config)
        self._health_port  = get_worker_health_port(worker_name, self._config)
        self._input_dir    = get_worker_input_directory(worker_name, self._config)
        self._output_dir   = get_worker_output_directory(worker_name, self._config)

        # Update UI labels here ...

    # ── Thread management ─────────────────────────────────────────────

    def toggle_listening(self):
        if self._thread is not None and self._thread.isRunning():
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self):
        self._thread = ZmqListenerThread(
            self._recv_port, self._results_port,
            health_port=self._health_port,
            worker_name=self._worker_name,
        )
        self._thread.job_received.connect(self._on_message_received)
        self._thread.error_occurred.connect(self._handle_error)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _stop_listening(self):
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(3000)
        self._on_thread_finished()

    def _on_thread_finished(self):
        self._thread = None

    def _handle_error(self, msg):
        # update UI status label / indicator
        pass

    # ── Message routing ───────────────────────────────────────────────

    def _on_message_received(self, msg: dict):
        """Entry point for all incoming messages — routes by type."""
        msg_type = msg.get("type")
        handlers = {
            "job":    self._handle_job,
            "cursor": self._handle_cursor,
        }
        handler = handlers.get(msg_type)
        if handler:
            handler(msg)

    # ── Job handler ───────────────────────────────────────────────────

    def _handle_job(self, msg: dict):
        import os

        data      = msg.get("data", {})
        ccd_path  = self._find_path_value(data)
        input_dir = msg.get("input_directory") or self._input_dir or ""
        input_dir = input_dir.strip().strip("'\"")   # strip user-pasted quotes

        basename  = os.path.basename(ccd_path.replace("\\", "/"))  # normalise Win paths

        if not basename or not input_dir:
            self._dispatch_error(msg, "missing filename or input_directory")
            return

        candidate = os.path.join(input_dir, basename)

        # Try common extensions if none present
        if not os.path.splitext(basename)[1]:
            for ext in (".spe", ".SPE", ".h5", ".hdf5"):
                if os.path.exists(candidate + ext):
                    candidate += ext
                    break

        if not os.path.exists(candidate):
            self._dispatch_error(msg, f"file not found — {basename}")
            return

        self._last_dispatched_job = None   # prevent stale model signal from re-dispatching

        # ← Replace this with your app's file-loading + processing call
        # self.my_pipeline.load_and_fit(candidate)
        # pipeline is synchronous; results are in the model by the time it returns

        job = {
            "filename":  os.path.basename(candidate),
            "row":       msg.get("row"),
            "timestamp": msg.get("timestamp"),
        }
        self._collect_and_dispatch(job)

    def _handle_cursor(self, msg: dict):
        """Cursor: load and display the file, but do not send a result back."""
        import os
        data      = msg.get("data", {})
        ccd_path  = self._find_path_value(data)
        input_dir = msg.get("input_directory") or self._input_dir or ""
        input_dir = input_dir.strip().strip("'\"")
        basename  = os.path.basename(ccd_path.replace("\\", "/"))

        if not basename or not input_dir:
            return

        candidate = os.path.join(input_dir, basename)
        if not os.path.splitext(basename)[1]:
            for ext in (".spe", ".SPE", ".h5", ".hdf5"):
                if os.path.exists(candidate + ext):
                    candidate += ext
                    break

        if not os.path.exists(candidate):
            return

        # ← Replace with your app's file-loading call (no result dispatch)
        # self.my_pipeline.load(candidate)

    # ── Result dispatch ───────────────────────────────────────────────

    def _collect_and_dispatch(self, msg: dict):
        """Collect results from the model and send back to coordinator."""
        values = self._collect_values()    # ← implement: read fitted values from model
        result = {
            "type":      "result",
            "worker":    self._worker_name,
            "row":       msg.get("row"),
            "timestamp": msg.get("timestamp"),
            "filename":  msg.get("filename", ""),
            "status":    "ok",
            "message":   "",
            "values":    values,
        }
        if self._thread is not None:
            self._thread.send_result(result)
        self._last_dispatched_job    = msg
        self._last_dispatched_values = values

    def _dispatch_error(self, msg: dict, message: str):
        result = {
            "type":      "result",
            "worker":    self._worker_name,
            "row":       msg.get("row"),
            "timestamp": msg.get("timestamp"),
            "filename":  msg.get("filename", ""),
            "status":    "error",
            "message":   message,
            "values":    {},
        }
        if self._thread is not None:
            self._thread.send_result(result)

    def _collect_values(self) -> dict:
        """Return a JSON-safe dict of result values from the model.
        NaN and Inf must be replaced with None (JSON null).
        """
        import math

        def sf(v):
            try:
                f = float(v)
                return None if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return None

        # ← Replace with reads from your model
        return {
            "result_value_1": sf(self.model.value1),
            "result_value_2": sf(self.model.value2),
        }

    # ── Re-dispatch on manual recalculation ───────────────────────────

    def _on_user_recalculation(self):
        """Connect this to your model's 'calculation changed' signal.

        Sends an updated result if the user changes fit settings after a job
        was already dispatched.  Guards against:
        - No active job context (_last_dispatched_job is None)
        - Thread not running
        - Different file loaded than the one that triggered the job
        - Values unchanged (avoids duplicate sends when multiple signals fire)
        """
        import os
        if self._last_dispatched_job is None:
            return
        if self._thread is None or not self._thread.isRunning():
            return
        current_file = os.path.basename(self.model.current_filename or "")
        if current_file != self._last_dispatched_job.get("filename", ""):
            return
        new_values = self._collect_values()
        if new_values == self._last_dispatched_values:
            return
        self._collect_and_dispatch(self._last_dispatched_job)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _find_path_value(data: dict) -> str:
        """Find a file path in the data dict without assuming a specific key name.

        Checks well-known key names first, then falls back to scanning all
        string values for anything containing a path separator.
        """
        for key in ("CCD_FileName", "filename", "file_path", "path", "file"):
            val = data.get(key, "")
            if isinstance(val, str) and val:
                return val
        for val in data.values():
            if isinstance(val, str) and ("\\" in val or "/" in val):
                return val
        return ""

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self):
        """Call from the app's closeEvent."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(3000)
```

### 1.7 Required UI widgets (worker panel)

| Attribute on `widget.zmq_gb` | Type | Purpose |
|---|---|---|
| `load_config_btn` | `QPushButton` | Opens file dialog to pick config.yaml |
| `config_lbl` | `QLabel` | Loaded config filename |
| `worker_name_lbl` | `QLabel` | Resolved worker name |
| `port_lbl` | `QLabel` | PULL port number |
| `results_port_lbl` | `QLabel` | Results PUSH port number |
| `health_port_lbl` | `QLabel` | REP health port number |
| `input_dir_lbl` | `QLabel` | input_directory from config |
| `output_dir_lbl` | `QLabel` | output_directory from config |
| `listen_btn` | `QPushButton` | Toggles listening |
| `status_indicator` | `StatusIndicator` | Dot: `set_active()` / `set_inactive()` / `set_error()` |
| `status_lbl` | `QLabel` | Text status |
| `last_job_txt` | `QPlainTextEdit` (read-only) | Last received message as JSON |
| `last_result_txt` | `QPlainTextEdit` (read-only) | Last sent result as JSON |

---

## Part 2 — Publisher (epicsLogger integration)

### 2.1 Socket topology

```
PyRadiant  PUSH  connect ──>  epicsLogger  PULL  bind   tcp://<host>:<port>
PyRadiant  REP   bind    <──  epicsLogger  REQ   connect tcp://*:<health_port>
```

PyRadiant **connects** to epicsLogger's port (epicsLogger binds). The health REP socket is bound by PyRadiant so epicsLogger can query it.

### 2.2 config.yaml — zmq_listeners section

This is a separate section from `workers`. The port numbers here belong to epicsLogger (what it binds); PyRadiant reads them to know where to connect.

```yaml
zmq_listeners:
  spectroradiometry:
    host: 127.0.0.1
    port: 6001          # epicsLogger PULL — PyRadiant PUSH connects here
    health_port: 6003   # PyRadiant REP  — epicsLogger REQ connects here
    enabled: true
```

### 2.3 Message formats

#### Trigger (PyRadiant → epicsLogger)

```json
{
    "type": "trigger",
    "data": {
        "ds_temperature":       1850.3,
        "ds_temperature_error":    5.2,
        "filename": "20251128-Pt-dac_00064.spe"
    }
}
```

#### Health check (epicsLogger → PyRadiant health REP)

| Request | Response |
|---|---|
| `{"type": "ping"}` | `{"type": "pong", "status": "ready", "worker": "<name>"}` |
| `{"type": "capabilities"}` | `{"status": "ready", "worker_name": "<name>", "version": "1.0", "fields": [...]}` |

Capabilities fields describe what keys appear in trigger `data`:

```json
{
    "status": "ready",
    "worker_name": "spectroradiometry",
    "version": "1.0",
    "fields": [
        {"key": "ds_temperature",       "label": "DS Temperature (K)",       "type": "float"},
        {"key": "ds_temperature_error", "label": "DS Temperature Error (K)", "type": "float"},
        {"key": "filename",             "label": "Spectroradiometry Filename","type": "str"}
    ]
}
```

### 2.4 Connection status semantics

A ZMQ PUSH `connect()` always succeeds locally regardless of whether the remote is running. **Do not report "Connected" on connect.** Use a two-stage indicator:

- **Amber / "Ready"** — local PUSH socket created and connected, health thread started, but epicsLogger hasn't pinged yet
- **Teal / "Connected"** — epicsLogger has pinged the health REP, confirming it is actually running

```python
def _connect(self):
    # ... create PUSH socket, start health thread ...
    indicator.set_ready()     # amber — local only
    status_lbl.setText("Ready — awaiting ping")

def _on_ping_received(self):  # connected to health thread signal
    indicator.set_active()    # teal — remote confirmed
    status_lbl.setText("Connected")
```

### 2.5 Trigger call site

Only call `send_trigger()` when genuinely **new data** is available — e.g. when a new file is delivered by the auto-monitor or AD stream. **Do not call it** when the user navigates with next/previous buttons, because that would create duplicate log entries in epicsLogger.

---

## Part 3 — config.py helper module

Place at project root. Same file is used by all workers in the ecosystem.

```python
import os, yaml

_DEFAULT_SEARCH_PATHS = [
    "config.yaml",
    os.path.join(os.path.dirname(__file__), "config.yaml"),
    os.path.expanduser("~/.config/t-view/config.yaml"),
]

def load_config(path=None):
    if path is None:
        for candidate in _DEFAULT_SEARCH_PATHS:
            if os.path.exists(candidate):
                path = candidate
                break
    if path is None or not os.path.exists(path):
        return {}
    with open(path, "r") as fh:
        return yaml.safe_load(fh) or {}

def get_worker_port(worker_name, config):
    return config.get("workers", {}).get(worker_name, {}).get("port")

def get_results_port(config):
    return config.get("ports", {}).get("coordinator_results")

def get_worker_health_port(worker_name, config):
    return config.get("workers", {}).get(worker_name, {}).get("health_port")

def get_worker_input_directory(worker_name, config):
    return config.get("workers", {}).get(worker_name, {}).get("input_directory")

def get_worker_output_directory(worker_name, config):
    return config.get("workers", {}).get(worker_name, {}).get("output_directory")

def get_epicslogger_connection(worker_name, config):
    entry = config.get("zmq_listeners", {}).get(worker_name)
    if entry is None:
        return None
    return {
        "host":        entry.get("host", "127.0.0.1"),
        "port":        entry.get("port"),
        "health_port": entry.get("health_port"),
        "enabled":     entry.get("enabled", True),
    }
```

---

## Part 4 — Wiring in the main controller

```python
# In __init__:
self.zmq_worker_controller = ZmqWorkerController(self.widget)
self.zmq_worker_controller.temperature_controller = self   # if needed

# Connect model recalculation signals so manual changes re-dispatch results:
self.model.calculation_changed.connect(
    self.zmq_worker_controller._on_user_recalculation
)

# In closeEvent:
self.zmq_worker_controller.cleanup()
```

---

## Part 5 — Design rules (must not be violated)

1. **Never process messages inside `QThread.run()`** — emit a `pyqtSignal(dict)` and handle on the GUI thread. ZMQ and Qt object model must stay on separate threads.
2. **Always use `zmq.Poller` with a timeout** (500 ms) — never call blocking `recv()` without a timeout. This allows `stop()` to work promptly.
3. **Set `LINGER=0` on all sockets** before binding/connecting — prevents the app hanging on close when the remote is unreachable.
4. **Health REP is bound by the worker** (not connected) — the coordinator connects to it.
5. **PUSH sockets always appear connected locally** — use the health ping to confirm the remote is alive.
6. **`send_result()` is best-effort** (`NOBLOCK`) — wrap in try/except; the coordinator may not be bound yet when the first result fires.
7. **Replace NaN/Inf with `None`** before sending any JSON — JSON does not support these float values.
8. **Echo `row` and `timestamp` from the job** in every result — the coordinator uses them to match results to log rows.
9. **Guard `_on_user_recalculation`** against stale context: check that the currently loaded file matches the last dispatched job before re-dispatching.
10. **Call `cleanup()` from `closeEvent`** — stops the thread and terminates the ZMQ context cleanly.
