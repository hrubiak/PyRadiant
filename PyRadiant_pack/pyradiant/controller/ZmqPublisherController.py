"""
ZMQ publisher controller for PyRadiant — epicsLogger integration.

PyRadiant connects to epicsLogger's PULL socket and sends a trigger message
(with fitted temperature data) whenever a new result is ready.  It also
optionally binds a REP health-check socket that epicsLogger can ping.

Socket topology::

    PyRadiant  PUSH  connect ──> epicsLogger  PULL  bind  tcp://<host>:<port>
    PyRadiant  REP   bind    <── epicsLogger  REQ   connect  tcp://*:<health_port>

Trigger message format (PyRadiant → epicsLogger)::

    {
        "type": "trigger",
        "data": {
            "ds_temperature_K":    1850.3,
            "ds_temperature_err_K":   5.2,
            "us_temperature_K":    1860.1,
            "us_temperature_err_K":   4.8,
            "filename":            "20250108_Pt_00048.spe"
        }
    }
"""

from datetime import datetime

from PyQt6 import QtCore

try:
    import zmq
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False


# Fields included in every trigger data payload.
# Keep in sync with _send_temperature_trigger() in TemperatureController.
CAPABILITIES_FIELDS = [
    {"key": "ds_temperature",       "label": "DS Temperature (K)",        "type": "float"},
    {"key": "ds_temperature_error", "label": "DS Temperature Error (K)",  "type": "float"},
    {"key": "us_temperature",       "label": "US Temperature (K)",        "type": "float"},
    {"key": "us_temperature_error", "label": "US Temperature Error (K)",  "type": "float"},
    {"key": "error_metric",         "label": "T Error Metric",             "type": "str"},
    {"key": "ds_fringe_frequency",  "label": "DS Fringe Frequency (cm)",  "type": "float"},
    {"key": "ds_fringe_nd_um",      "label": "DS n·d (μm)",               "type": "float"},
    {"key": "us_fringe_frequency",  "label": "US Fringe Frequency (cm)",  "type": "float"},
    {"key": "us_fringe_nd_um",      "label": "US n·d (μm)",               "type": "float"},
    {"key": "exposure_time",        "label": "Exposure Time (s)",         "type": "float"},
    {"key": "gain",                 "label": "Gain",                      "type": "float"},
    {"key": "filename",             "label": "Spectroradiometry Filename", "type": "str"},
    {"key": "filepath",             "label": "Spectroradiometry Path",    "type": "str"},
    {"key": "frame_count",          "label": "Frame Count",               "type": "int"},
    {"key": "frame_index",          "label": "Frame Index",               "type": "int"},
    {"key": "frame_range_start",    "label": "Frame Range Start",         "type": "int"},
    {"key": "frame_range_end",      "label": "Frame Range End",           "type": "int"},
    {"key": "aggregation",          "label": "Aggregation",               "type": "str"},
]


class ZmqHealthServerThread(QtCore.QThread):
    """Binds a REP socket so epicsLogger can perform health checks and
    fetch capability metadata."""

    ping_received = QtCore.pyqtSignal()   # emitted on the GUI thread each time a ping is answered

    def __init__(self, health_port, worker_name="spectroradiometry", parent=None):
        super().__init__(parent)
        self.health_port = health_port
        self.worker_name = worker_name
        self._stop_flag = False

    def run(self):
        if not ZMQ_AVAILABLE:
            return
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.setsockopt(zmq.RCVTIMEO, 500)
        sock.setsockopt(zmq.LINGER, 0)
        sock.bind(f"tcp://*:{self.health_port}")
        while not self._stop_flag:
            try:
                msg = sock.recv_json()
                msg_type = msg.get("type")
                if msg_type == "ping":
                    sock.send_json({
                        "type":   "pong",
                        "status": "ready",
                        "worker": self.worker_name,
                    })
                    self.ping_received.emit()
                elif msg_type == "capabilities":
                    sock.send_json({
                        "status":      "ready",
                        "worker_name": self.worker_name,
                        "version":     "1.0",
                        "fields":      CAPABILITIES_FIELDS,
                    })
                else:
                    sock.send_json({"status": "error", "message": "unknown type"})
            except zmq.Again:
                pass
            except zmq.ZMQError:
                break
        sock.close(linger=0)
        ctx.term()

    def stop(self):
        self._stop_flag = True
        self.wait(2000)


class ZmqPublisherController(QtCore.QObject):
    """Manages a PUSH socket to epicsLogger and an optional health REP thread.

    Call :meth:`send_trigger` whenever a temperature fit result is ready.
    The controller is wired to the *epicslogger_gb* panel in the sidebar.
    """

    status_changed = QtCore.pyqtSignal(str)   # "idle" | "connected" | "error"
    trigger_sent   = QtCore.pyqtSignal(dict)

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self.widget = widget
        self._context = None
        self._push_socket = None
        self._health_thread = None
        self._host = "127.0.0.1"
        self._port = None
        self._health_port = None
        self._worker_name = "spectroradiometry"
        self._config_path = ""

        self._connect_widget_signals()

    # ------------------------------------------------------------------
    # Widget wiring
    # ------------------------------------------------------------------

    def _connect_widget_signals(self):
        w = self.widget.epicslogger_gb
        w.load_config_btn.clicked.connect(self.load_config_clicked)
        w.connect_btn.clicked.connect(self.toggle_connection)
        w.publish_temperatures_cb.toggled.connect(self._publish_toggled)

    def _publish_toggled(self, checked):
        gb = self.widget.epicslogger_gb
        if checked and self._push_socket is not None:
            # Show active if socket is up; publishing works regardless of ping confirmation
            gb.publish_indicator.set_active()
        else:
            gb.publish_indicator.set_inactive()



    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def load_config_clicked(self):
        import os as _os
        from ..widget.Widgets import open_file_dialog
        start_dir = _os.path.dirname(self._config_path) if self._config_path else ""
        path = open_file_dialog(
            self.widget,
            caption="Load worker config",
            directory=start_dir,
            filter="YAML files (*.yaml *.yml);;All files (*)",
        )
        if path:
            self.load_config(path)

    def load_config(self, path):
        import os
        from ..config import load_config, get_epicslogger_connection
        config = load_config(path)
        info = get_epicslogger_connection(self._worker_name, config)

        gb = self.widget.epicslogger_gb
        if info is None:
            gb.config_lbl.setText(
                f"{os.path.basename(path)} — no '{self._worker_name}' entry in zmq_listeners"
            )
            return

        self._config_path = path
        self._host        = info.get("host", "127.0.0.1")
        self._port        = info.get("port")
        self._health_port = info.get("health_port")

        gb.config_lbl.setText(os.path.basename(path))
        gb.config_lbl.setToolTip(
            f"{path}\n"
            f"host={self._host}  port={self._port}  health_port={self._health_port}"
        )
        gb.host_lbl.setText(self._host)
        gb.port_lbl.setText(str(self._port) if self._port else "—")
        gb.health_port_lbl.setText(str(self._health_port) if self._health_port else "—")
        gb.connect_btn.setEnabled(self._port is not None)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def toggle_connection(self):
        if self._push_socket is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        if not ZMQ_AVAILABLE:
            gb = self.widget.epicslogger_gb
            gb.status_lbl.setText("pyzmq not installed")
            gb.status_indicator.set_error()
            return
        if self._port is None:
            return

        self._context = zmq.Context()
        self._push_socket = self._context.socket(zmq.PUSH)
        self._push_socket.setsockopt(zmq.LINGER, 0)
        self._push_socket.setsockopt(zmq.SNDHWM, 20)
        self._push_socket.connect(f"tcp://{self._host}:{self._port}")

        if self._health_port is not None:
            self._health_thread = ZmqHealthServerThread(self._health_port, self._worker_name)
            self._health_thread.ping_received.connect(self._on_ping_received)
            self._health_thread.start()

        gb = self.widget.epicslogger_gb
        gb.connect_btn.setText("Disconnect")
        gb.status_lbl.setText("Ready — awaiting ping")
        gb.status_indicator.set_ready()
        self.status_changed.emit("ready")

    def _on_ping_received(self):
        """Called (on the GUI thread) when epicsLogger successfully pings the health port."""
        gb = self.widget.epicslogger_gb
        gb.status_lbl.setText("Connected")
        gb.status_indicator.set_active()
        if gb.publish_temperatures_cb.isChecked():
            gb.publish_indicator.set_active()
        self.status_changed.emit("connected")

    def _disconnect(self):
        if self._push_socket is not None:
            self._push_socket.close()
            self._push_socket = None
        if self._context is not None:
            self._context.term()
            self._context = None
        if self._health_thread is not None:
            self._health_thread.stop()
            self._health_thread = None

        gb = self.widget.epicslogger_gb
        gb.connect_btn.setText("Connect")
        gb.status_lbl.setText("Idle")
        gb.status_indicator.set_inactive()
        gb.publish_indicator.set_inactive()
        gb.last_trigger_lbl.setText("—")
        self.status_changed.emit("idle")

    # ------------------------------------------------------------------
    # Trigger sending
    # ------------------------------------------------------------------

    def send_trigger(self, data: dict = None):
        """Send a trigger message to epicsLogger.  No-op if not connected."""
        if self._push_socket is None:
            return
        msg = {"type": "trigger"}
        if data:
            msg["data"] = data
        try:
            self._push_socket.send_json(msg, zmq.NOBLOCK)
            ts = datetime.now().strftime('%H:%M:%S')
            gb = self.widget.epicslogger_gb
            gb.last_trigger_lbl.setText(ts)
            import json as _json
            try:
                gb.last_payload_txt.setPlainText(_json.dumps(msg, indent=2, default=str))
            except Exception:
                gb.last_payload_txt.setPlainText(repr(msg))
            # A successful send is enough evidence to promote the status from
            # "awaiting ping" to "connected" — health-ping is optional and
            # user-initiated on the epicsLogger side, so we shouldn't force
            # users to click Check Health just to see the indicator go green.
            gb.status_lbl.setText("Connected")
            gb.status_indicator.set_active()
            if gb.publish_temperatures_cb.isChecked():
                gb.publish_indicator.set_active()
            self.status_changed.emit("connected")
            self.trigger_sent.emit(msg)
        except Exception:
            pass  # best-effort; logger may not be bound yet

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self):
        """Stop threads and close sockets cleanly on app exit."""
        self._disconnect()
