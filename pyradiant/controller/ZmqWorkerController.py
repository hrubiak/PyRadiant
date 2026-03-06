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

# Schema advertised to the coordinator via the "schema" query on the health port.
# The "name" keys must exactly match the keys in the "values" dict of every
# result message pushed back to the coordinator.
SPECTRORADIOMETRY_SCHEMA = [
    {"name": "ds_temperature",       "label": "DS Temperature",       "unit": "K"},
    {"name": "ds_temperature_error", "label": "DS Temperature Error", "unit": "K"},
    {"name": "us_temperature",       "label": "US Temperature",       "unit": "K"},
    {"name": "us_temperature_error", "label": "US Temperature Error", "unit": "K"},
    {"name": "ds_fringe_frequency",  "label": "DS Fringe Frequency",  "unit": "cm"},
    {"name": "ds_fringe_nd_um",      "label": "DS n·d",               "unit": "μm"},
    {"name": "us_fringe_frequency",  "label": "US Fringe Frequency",  "unit": "cm"},
    {"name": "us_fringe_nd_um",      "label": "US n·d",               "unit": "μm"},
    {"name": "exposure_time",        "label": "Exposure Time",        "unit": "s"},
    {"name": "gain",                 "label": "Gain",                 "unit": ""},
]

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
        receiver.set(zmq.LINGER, 0)
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
                ready = dict(poller.poll(500))   # 500 ms timeout — lets stop() work promptly
                if receiver in ready:
                    raw = receiver.recv()
                    msg = json.loads(raw)
                    self.job_received.emit(msg)
                if health is not None and health in ready:
                    try:
                        req = health.recv_json()
                    except Exception:
                        req = {}
                    msg_type = req.get("type")
                    if msg_type == "schema":
                        health.send_json({
                            "type":    "schema_reply",
                            "columns": SPECTRORADIOMETRY_SCHEMA,
                        })
                    elif msg_type == "ping" or msg_type is None:
                        health.send_json({
                            "type":   "pong",
                            "status": "ready",
                            "worker": self.worker_name,
                        })
                    else:
                        health.send_json({
                            "type":    "error",
                            "message": f"unknown type: {msg_type}",
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
        self._input_dir = None
        self._output_dir = None
        self._worker_name = "spectroradiometry"
        self._temperature_controller = None
        self._last_dispatched_job = None     # job dict of the last successfully sent result
        self._last_dispatched_values = None  # values dict — used to skip no-change re-dispatches

        self._connect_widget_signals()

    @property
    def temperature_controller(self):
        return self._temperature_controller

    @temperature_controller.setter
    def temperature_controller(self, controller):
        if self._temperature_controller is not None:
            m = self._temperature_controller.model
            m.data_changed_signal.disconnect(self._on_user_recalculation)
            m.ds_calculations_changed.disconnect(self._on_user_recalculation)
            m.us_calculations_changed.disconnect(self._on_user_recalculation)
        self._temperature_controller = controller
        if controller is not None:
            m = controller.model
            m.data_changed_signal.connect(self._on_user_recalculation)
            m.ds_calculations_changed.connect(self._on_user_recalculation)
            m.us_calculations_changed.connect(self._on_user_recalculation)

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
        from config import (load_config, get_worker_port, get_results_port,
                            get_worker_health_port, get_worker_input_directory,
                            get_worker_output_directory)
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
        self._input_dir    = get_worker_input_directory(worker_name, self._config) if worker_name else None
        self._output_dir   = get_worker_output_directory(worker_name, self._config) if worker_name else None

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
        self._thread.job_received.connect(self._on_message_received)
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
    # Job handling
    # ------------------------------------------------------------------

    def _on_message_received(self, msg: dict):
        """Entry point for all incoming ZMQ messages. Routes by type."""
        try:
            self.widget.zmq_gb.last_job_txt.setPlainText(json.dumps(msg, indent=2))
        except Exception:
            self.widget.zmq_gb.last_job_txt.setPlainText(repr(msg))

        msg_type = msg.get("type")
        _handlers = {
            "job": self._handle_job,
        }
        handler = _handlers.get(msg_type)
        if handler is not None:
            self.widget.zmq_gb.status_lbl.setText(f"{msg_type} message received")
            handler(msg)
        else:
            self.widget.zmq_gb.status_lbl.setText(f"Unknown message type: {msg_type!r}")

    def _collect_and_dispatch(self, msg: dict):
        """Collect fit results from the current configuration and dispatch them."""
        conf = self._temperature_controller.model.current_configuration
        result = {
            "type":      "result",
            "worker":    self._worker_name,
            "row":       msg.get("row"),
            "timestamp": msg.get("timestamp"),
            "filename":  msg.get("filename", ""),
            "status":    "ok",
            "message":   "",
            "values":    self.collect_temperature_values(conf),
        }
        self._dispatch_result(result)
        self._last_dispatched_job = msg
        self._last_dispatched_values = result["values"]

    def _on_user_recalculation(self):
        """Called when any model signal fires (data_changed, ds/us_calculations_changed).

        Dispatches an updated result if a ZMQ job context exists for the current
        file and the computed values have actually changed since the last dispatch.
        Skipping unchanged values prevents duplicate sends when ds and us signals
        both fire for a single user action.
        """
        if self._last_dispatched_job is None:
            return
        if self._thread is None or not self._thread.isRunning():
            return
        import os
        conf = self._temperature_controller.model.current_configuration
        if os.path.basename(conf.filename or "") != self._last_dispatched_job.get("filename", ""):
            return
        new_values = self.collect_temperature_values(conf)
        if new_values == self._last_dispatched_values:
            return
        self._collect_and_dispatch(self._last_dispatched_job)

    def _dispatch_result(self, result: dict):
        if self._thread is not None:
            self._thread.send_result(result)
        try:
            txt = json.dumps(result, indent=2)
        except Exception as exc:
            txt = f"[json error] {exc}\n{result!r}"
        self.widget.zmq_gb.last_result_txt.setPlainText(txt)
        self.widget.zmq_gb.status_lbl.setText(f"Result dispatched — status: {result.get('status', '?')}")
        self.job_finished.emit(result)

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
        self._dispatch_result(result)

    @staticmethod
    def _safe_float(v):
        """Return v as a JSON-safe float, or None if it is NaN, Inf, or falsy."""
        import math
        try:
            f = float(v)
            return None if (math.isnan(f) or math.isinf(f) or f == 0) else f
        except (TypeError, ValueError):
            return None

    @staticmethod
    def collect_temperature_values(conf):
        """Return a JSON-safe values dict from a TemperatureModelConfiguration.

        This is the canonical way to extract fit results from the model for any
        outgoing ZMQ message (worker results and epicsLogger triggers alike).
        """
        sf = ZmqWorkerController._safe_float
        data_file = conf.data_img_file
        gain = getattr(data_file, 'EMIccd_gain', None) or getattr(data_file, 'gain', None)
        return {
            "ds_temperature":       sf(conf.ds_temperature),
            "ds_temperature_error": sf(conf.ds_temperature_error),
            "us_temperature":       sf(conf.us_temperature),
            "us_temperature_error": sf(conf.us_temperature_error),
            "ds_fringe_frequency":  sf(conf.ds_fringe_frequency),
            "ds_fringe_nd_um":      sf(conf.ds_fringe_nd_um),
            "us_fringe_frequency":  sf(conf.us_fringe_frequency),
            "us_fringe_nd_um":      sf(conf.us_fringe_nd_um),
            "exposure_time":        sf(getattr(data_file, 'exposure_time', None)),
            "gain":                 gain,
        }

    def _handle_job(self, msg: dict):
        """Handle a job message: resolve file from data["CCD_FileName"] + input_directory,
        load, fit, dispatch result."""
        import os

        if self._temperature_controller is None:
            self._dispatch_error(msg, "temperature_controller not connected")
            return

        data = msg.get("data", {})
        ccd_path = data.get("CCD_FileName", "")
        input_dir = msg.get("input_directory") or self._input_dir or ""

        # Strip surrounding quotes that a user may have pasted (e.g. '/path' or "/path")
        input_dir = input_dir.strip().strip("'\"")

        # Normalise Windows backslashes before splitting
        basename = os.path.basename(ccd_path.replace("\\", "/"))

        if not basename:
            self._dispatch_error(msg, "no filename in CCD_FileName")
            return
        if not input_dir:
            self._dispatch_error(msg, "no input_directory configured")
            return

        candidate = os.path.join(input_dir, basename)

        # If the path has no extension try common spectroradiometry formats
        if not os.path.splitext(basename)[1]:
            for ext in (".spe", ".SPE", ".h5", ".hdf5"):
                test = candidate + ext
                if os.path.exists(test):
                    candidate = test
                    break

        if not os.path.exists(candidate):
            self._dispatch_error(msg, f"file not found — {basename}")
            return

        self._last_dispatched_job = None   # prevent stale signal from triggering during load
        self.widget.zmq_gb.status_lbl.setText(f"Loading {os.path.basename(candidate)}…")
        self.job_started.emit(msg)
        try:
            self._temperature_controller.load_data_file(filenames=[candidate])
        except Exception as exc:
            self._dispatch_error(msg, str(exc))
            return
        # Pipeline is synchronous — fitting is done by the time load_data_file returns
        job = {
            "filename":  os.path.basename(candidate),
            "row":       msg.get("row"),
            "timestamp": msg.get("timestamp"),
        }
        self._collect_and_dispatch(job)

    def cleanup(self):
        """Stop the listener thread cleanly (call on app close)."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(3000)
