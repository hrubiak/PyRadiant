"""
ZMQ worker controller for PyRadiant.

PyRadiant acts as a PULL worker in the T-view ecosystem.  The coordinator
pushes jobs (JSON dicts) to this worker's port; processed results are
pushed back to the coordinator's results port.

Expected incoming message format::

    {
        "folder":    "/data/raw/run17",
        "filename":  "20250108_Pt_00048.spe",
        "calibration": { ... }   # optional; omit to use currently loaded calibration
    }

Result message format (sent back to coordinator)::

    {
        "filename":        "20250108_Pt_00048.spe",
        "ds_temperature":  1821.4,
        "ds_error":        3.2,
        "us_temperature":  1834.1,
        "us_error":        4.5,
        "status":          "ok"   # or "error"
        "message":         ""     # error description if status == "error"
    }
"""

import json

from PyQt6 import QtCore

try:
    import zmq
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False


class ZmqListenerThread(QtCore.QThread):
    """Background thread that polls a PULL socket for incoming jobs.

    Signals
    -------
    job_received(dict)
        Emitted on the GUI thread whenever a complete job message arrives.
    error_occurred(str)
        Emitted when the socket raises an unexpected exception.
    """

    job_received = QtCore.pyqtSignal(dict)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, recv_port, results_port, health_port=None, worker_name="spectroradiometry", parent=None):
        super().__init__(parent)
        self.recv_port = recv_port
        self.results_port = results_port
        self.health_port = health_port
        self.worker_name = worker_name
        self._stop_flag = False
        self._results_socket = None
        self._context = None

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
                ready = dict(poller.poll(500))   # 500 ms timeout — lets stop() work promptly
                if receiver in ready:
                    raw = receiver.recv()
                    msg = json.loads(raw)
                    self.job_received.emit(msg)
                if health is not None and health in ready:
                    health.recv()   # consume the ping (content ignored)
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
        """Send a result dict back to the coordinator (called from the GUI thread)."""
        if self._results_socket is not None:
            try:
                self._results_socket.send(json.dumps(result).encode(), zmq.NOBLOCK)
            except Exception:
                pass  # best-effort; coordinator may not be listening yet

    def stop(self):
        self._stop_flag = True


class ZmqWorkerController(QtCore.QObject):
    """High-level controller that manages the listener thread and job dispatch.

    Connect *temperature_controller* after construction so that incoming jobs
    can trigger data loading and fitting via the existing pyradiant pipeline.
    """

    status_changed = QtCore.pyqtSignal(str)   # "idle" | "listening" | "error"
    job_started = QtCore.pyqtSignal(dict)
    job_finished = QtCore.pyqtSignal(dict)

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self.widget = widget
        self._thread = None
        self._config = {}
        self._recv_port = None
        self._results_port = None
        self._health_port = None
        self._worker_name = "spectroradiometry"
        self.temperature_controller = None   # set by TemperatureController after init

        self._connect_widget_signals()

    # ------------------------------------------------------------------
    # Widget signal wiring
    # ------------------------------------------------------------------

    def _connect_widget_signals(self):
        w = self.widget.zmq_gb
        w.load_config_btn.clicked.connect(self.load_config_clicked)
        w.listen_btn.clicked.connect(self.toggle_listening)

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def load_config_clicked(self):
        from ..widget.Widgets import open_file_dialog
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
        from config import load_config, get_worker_port, get_results_port, get_worker_health_port
        self._config = load_config(path)
        self._config["_path"] = path

        # Identify this worker's entry by canonical name first,
        # then fall back to any entry whose script is run_pyradiant.py
        workers = self._config.get("workers", {})
        if "spectroradiometry" in workers:
            worker_name = "spectroradiometry"
        else:
            worker_name = None
            for name, info in workers.items():
                if os.path.basename(info.get("script", "")) == "run_pyradiant.py":
                    worker_name = name
                    break

        self._worker_name  = worker_name or "spectroradiometry"
        self._recv_port    = get_worker_port(worker_name, self._config) if worker_name else None
        self._results_port = get_results_port(self._config)
        self._health_port  = get_worker_health_port(worker_name, self._config) if worker_name else None

        self.widget.zmq_gb.config_lbl.setText(os.path.basename(path))
        self.widget.zmq_gb.worker_name_lbl.setText(self._worker_name)
        self.widget.zmq_gb.port_lbl.setText(str(self._recv_port) if self._recv_port else "—")
        self.widget.zmq_gb.results_port_lbl.setText(
            str(self._results_port) if self._results_port else "—"
        )
        self.widget.zmq_gb.health_port_lbl.setText(
            str(self._health_port) if self._health_port else "—"
        )
        self.widget.zmq_gb.listen_btn.setEnabled(
            self._recv_port is not None and self._results_port is not None
        )

    # ------------------------------------------------------------------
    # Worker thread management
    # ------------------------------------------------------------------

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

        self._thread = ZmqListenerThread(self._recv_port, self._results_port,
                                         health_port=self._health_port,
                                         worker_name=self._worker_name)
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

    # ------------------------------------------------------------------
    # Job handling (placeholder — extend here)
    # ------------------------------------------------------------------

    def _handle_job(self, msg: dict):
        """Process an incoming job from the coordinator.

        Currently a placeholder: logs the received message and echoes a
        result back.  Replace the body of this method to integrate with
        the pyradiant data-loading and fitting pipeline.
        """
        folder   = msg.get("folder", "")
        filename = msg.get("filename", "")
        self.widget.zmq_gb.last_job_lbl.setText(f"{folder}/{filename}")
        self.job_started.emit(msg)

        # --- placeholder: load file and fit via temperature_controller ---
        # TODO: call self.temperature_controller.load_data(os.path.join(folder, filename))
        #       then read fitted temperatures from the model and build result dict
        result = {
            "filename":       filename,
            "ds_temperature": 0.0,
            "ds_error":       0.0,
            "us_temperature": 0.0,
            "us_error":       0.0,
            "status":         "ok",
            "message":        "placeholder — processing not yet implemented",
        }

        if self._thread is not None:
            self._thread.send_result(result)

        self.job_finished.emit(result)

    def cleanup(self):
        """Stop the listener thread cleanly (call on app close)."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(3000)
