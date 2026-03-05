# ZMQ Worker Architecture — Implementation Guide

Implement the ZMQ worker pattern exactly as follows. The app acts as a **PULL worker** in a coordinator ecosystem. It receives job messages, processes them, and pushes results back. An optional REP health socket lets the coordinator ping the worker.

---

## Socket topology

```
Coordinator  PUSH  bind    ──>  Worker  PULL  connect   tcp://127.0.0.1:<port>
Coordinator  PULL  bind    <──  Worker  PUSH  connect   tcp://127.0.0.1:<results_port>
Coordinator  REQ   connect <──  Worker  REP   bind      tcp://*:<health_port>
```

The coordinator **binds**; the worker **connects** (except the health REP, which the worker binds).

---

## 1. config.yaml structure

The shared config file must have this layout. Each worker gets its own entry under `workers:`. The coordinator results port lives under `ports:`.

```yaml
ports:
  coordinator_results: 5554

workers:
  my_worker_name:
    port: 5561              # PULL port — worker connects here to receive jobs
    health_port: 5562       # REP port  — worker binds here for health pings
    script: run_myapp.py    # used by coordinator to identify this worker
    input_directory: /data/raw/my_worker
    output_directory: /data/processed/my_worker
```

---

## 2. config.py helper module

Place this file at the project root (same level as `config.yaml`). It is imported by the controller at runtime.

```python
import os
import yaml

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
```

---

## 3. ZmqListenerThread (background QThread)

This thread polls the PULL socket and the REP health socket simultaneously using `zmq.Poller` with a 500 ms timeout so `stop()` works promptly without blocking. Job messages are emitted as a PyQt signal so all processing happens on the GUI thread — **never** process jobs inside `run()`.

```python
import json
from PyQt6 import QtCore

try:
    import zmq
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False


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
        receiver.connect(f"tcp://127.0.0.1:{self.recv_port}")

        self._results_socket = self._context.socket(zmq.PUSH)
        self._results_socket.connect(f"tcp://127.0.0.1:{self.results_port}")

        health = None
        if self.health_port is not None:
            health = self._context.socket(zmq.REP)
            health.bind(f"tcp://*:{self.health_port}")

        poller = zmq.Poller()
        poller.register(receiver, zmq.POLLIN)
        if health is not None:
            poller.register(health, zmq.POLLIN)

        while not self._stop_flag:
            try:
                ready = dict(poller.poll(500))
                if receiver in ready:
                    raw = receiver.recv()
                    msg = json.loads(raw)
                    self.job_received.emit(msg)   # handled on GUI thread
                if health is not None and health in ready:
                    health.recv()   # content ignored
                    health.send_json({
                        "type":   "pong",
                        "status": "ready",
                        "worker": self.worker_name,
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
        """Send result back to coordinator. Call from GUI thread."""
        if self._results_socket is not None:
            try:
                self._results_socket.send(json.dumps(result).encode(), zmq.NOBLOCK)
            except Exception:
                pass   # best-effort

    def stop(self):
        self._stop_flag = True
```

---

## 4. ZmqWorkerController (QObject)

This is the high-level controller. It owns the thread, loads config, wires widget signals, and dispatches messages by type. Adapt `_handle_generic_job` and `_handle_cursor` to your app's processing pipeline.

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

        self._connect_widget_signals()

    def _connect_widget_signals(self):
        w = self.widget.zmq_gb
        w.load_config_btn.clicked.connect(self._load_config_clicked)
        w.listen_btn.clicked.connect(self.toggle_listening)

    # ── Config ────────────────────────────────────────────────────────

    def _load_config_clicked(self):
        from ..widget.Widgets import open_file_dialog   # adjust import to your project
        path = open_file_dialog(
            self.widget,
            caption="Load worker config",
            directory="",
            filter="YAML files (*.yaml *.yml);;All files (*)",
        )
        if path:
            self.load_config(path)

    def load_config(self, path):
        import sys, os
        _root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from config import (load_config, get_worker_port, get_results_port,
                            get_worker_health_port, get_worker_input_directory,
                            get_worker_output_directory)
        self._config = load_config(path)
        self._config["_path"] = path

        # Find this worker's entry: look for canonical name, then by script filename
        workers = self._config.get("workers", {})
        MY_WORKER_NAME = "my_worker"         # ← set to your worker's key in config.yaml
        MY_SCRIPT_NAME = "run_myapp.py"      # ← set to your entry-point filename

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

        # Update UI labels (adjust widget attribute names to match your UI)
        w = self.widget.zmq_gb
        w.config_lbl.setText(os.path.basename(path))
        w.worker_name_lbl.setText(self._worker_name)
        w.port_lbl.setText(str(self._recv_port) if self._recv_port else "—")
        w.results_port_lbl.setText(str(self._results_port) if self._results_port else "—")
        w.health_port_lbl.setText(str(self._health_port) if self._health_port else "—")
        w.input_dir_lbl.setText(self._input_dir or "—")
        w.output_dir_lbl.setText(self._output_dir or "—")
        w.listen_btn.setEnabled(
            self._recv_port is not None and self._results_port is not None
        )

    # ── Thread management ─────────────────────────────────────────────

    def toggle_listening(self):
        if self._thread is not None and self._thread.isRunning():
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self):
        if not ZMQ_AVAILABLE:
            self.widget.zmq_gb.status_lbl.setText("pyzmq not installed")
            self.widget.zmq_gb.status_indicator.set_error()
            return
        self._thread = ZmqListenerThread(
            self._recv_port, self._results_port,
            health_port=self._health_port,
            worker_name=self._worker_name,
        )
        self._thread.job_received.connect(self._handle_job)
        self._thread.error_occurred.connect(self._handle_error)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        self.widget.zmq_gb.listen_btn.setText("Stop Listening")
        self.widget.zmq_gb.status_lbl.setText("Listening…")
        self.widget.zmq_gb.status_indicator.set_active()
        self.status_changed.emit("listening")

    def _stop_listening(self):
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(3000)
        self._on_thread_finished()

    def _on_thread_finished(self):
        self._thread = None
        self.widget.zmq_gb.listen_btn.setText("Start Listening")
        self.widget.zmq_gb.status_lbl.setText("Idle")
        self.widget.zmq_gb.status_indicator.set_inactive()
        self.status_changed.emit("idle")

    def _handle_error(self, msg):
        self.widget.zmq_gb.status_lbl.setText(f"Error: {msg}")
        self.widget.zmq_gb.status_indicator.set_error()
        self.status_changed.emit("error")

    # ── Job dispatch ──────────────────────────────────────────────────

    def _handle_job(self, msg: dict):
        """Dispatch by message type. All processing happens here on the GUI thread."""
        self.widget.zmq_gb.last_job_txt.setPlainText(json.dumps(msg, indent=2))
        msg_type = msg.get("type")
        if msg_type == "cursor":
            self._handle_cursor(msg)
        else:
            self._handle_generic_job(msg)

    def _handle_generic_job(self, msg: dict):
        """Process a standard job message. Replace body with your pipeline call."""
        self.job_started.emit(msg)
        # TODO: call your processing pipeline here and read results
        result = {
            "filename": msg.get("filename", ""),
            "status":   "ok",
            "message":  "placeholder",
        }
        if self._thread is not None:
            self._thread.send_result(result)
        self.job_finished.emit(result)

    def _handle_cursor(self, msg: dict):
        """Handle a cursor message from epicsLogViewer.

        Strips the filename from data["CCD_FileName"] (handles Windows
        backslash paths), then looks for the file in input_directory.
        Tries common extensions when the path has none.
        """
        import os

        data      = msg.get("data", {})
        ccd_path  = data.get("CCD_FileName", "")
        input_dir = msg.get("input_directory") or self._input_dir or ""

        # Accept paths pasted with surrounding quotes
        input_dir = input_dir.strip().strip("'\"")

        # Normalise Windows backslashes before splitting
        basename = os.path.basename(ccd_path.replace("\\", "/"))

        if not basename:
            self.widget.zmq_gb.status_lbl.setText("Cursor: no filename in CCD_FileName")
            return
        if not input_dir:
            self.widget.zmq_gb.status_lbl.setText("Cursor: no input_directory configured")
            return

        candidate = os.path.join(input_dir, basename)

        # Try common extensions when path has none
        if not os.path.splitext(basename)[1]:
            for ext in (".spe", ".SPE", ".h5", ".hdf5"):
                test = candidate + ext
                if os.path.exists(test):
                    candidate = test
                    break

        if not os.path.exists(candidate):
            self.widget.zmq_gb.status_lbl.setText(f"Cursor: not found — {basename}")
            return

        self.widget.zmq_gb.status_lbl.setText(f"Cursor: loading {os.path.basename(candidate)}")
        # TODO: call your app's file-loading pipeline with `candidate`

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self):
        """Call this from your app's closeEvent."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(3000)
```

---

## 5. Required UI widgets on `self.widget.zmq_gb`

The controller references these attributes. Create them in your widget however you like (labels, buttons, etc.):

| Attribute | Type | Purpose |
|---|---|---|
| `load_config_btn` | `QPushButton` | Opens file dialog to load config.yaml |
| `config_lbl` | `QLabel` | Shows loaded config filename |
| `worker_name_lbl` | `QLabel` | Shows resolved worker name |
| `port_lbl` | `QLabel` | Shows PULL port |
| `results_port_lbl` | `QLabel` | Shows results PUSH port |
| `health_port_lbl` | `QLabel` | Shows REP health port |
| `input_dir_lbl` | `QLabel` | Shows input_directory from config |
| `output_dir_lbl` | `QLabel` | Shows output_directory from config |
| `listen_btn` | `QPushButton` | Toggles listening on/off |
| `status_indicator` | `StatusIndicator` | Dot widget: `set_active()` / `set_inactive()` / `set_error()` |
| `status_lbl` | `QLabel` | Text status ("Idle", "Listening…", errors) |
| `last_job_txt` | `QPlainTextEdit` (read-only) | Displays last received message as pretty JSON |

### StatusIndicator widget

A small colored dot (10×10 px) that shows worker state. Minimum implementation:

```python
from PyQt6 import QtWidgets

class StatusIndicator(QtWidgets.QLabel):
    _STYLE = "background-color: {color}; border-radius: 5px;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self.set_inactive()

    def set_active(self):
        self.setStyleSheet(self._STYLE.format(color="#4DDECD"))   # teal
        self.setToolTip("Listening")

    def set_inactive(self):
        self.setStyleSheet(self._STYLE.format(color="#606060"))   # gray
        self.setToolTip("Idle")

    def set_error(self):
        self.setStyleSheet(self._STYLE.format(color="#E05050"))   # red
        self.setToolTip("Error")
```

---

## 6. Wiring into the main controller

```python
# In your main controller __init__:
self.zmq_worker_controller = ZmqWorkerController(self.widget)

# If the worker needs to call back into your app's pipeline:
self.zmq_worker_controller.some_reference = self

# In closeEvent / cleanup:
self.zmq_worker_controller.cleanup()
```

---

## Key design rules to preserve

1. **Never process messages inside `run()`** — emit a signal and handle on the GUI thread.
2. **Use `zmq.Poller` with a timeout** (500 ms works well) — never block indefinitely.
3. **PUSH sockets are always "connected" locally** — do not use them as a connection indicator.
4. **Health REP socket is bound by the worker** (not connected) — the coordinator connects to it.
5. **`send_result()` is best-effort** — wrap in try/except; coordinator may not be listening yet.
6. **Call `cleanup()` on app close** — stops the thread and terminates the ZMQ context cleanly.
