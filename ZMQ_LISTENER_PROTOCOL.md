# epicsLogger — ZMQ Worker Listener Protocol

This document describes how an external worker application (e.g. the
spectroradiometry processing app) connects to **epicsLogger** via ZMQ to
trigger a log record and optionally supply reduced data values for inclusion
in the log.

---

## Socket topology

```
  Worker app (you)                  epicsLogger
  ─────────────────                 ──────────────────────────────
  zmq.PUSH  connect ──────────────> zmq.PULL  bind  tcp://*:<port>
  zmq.REP   bind    <────────────── zmq.REQ   connect  (health check only)
```

- **epicsLogger binds**; the worker **connects**.  The logger owns the ports —
  the worker only needs to know the IP/hostname of the machine running
  epicsLogger and the port numbers from the shared config file.
- One PUSH→PULL pair per worker.  If multiple workers exist, each gets its
  own port.
- The REQ/REP health-check pair is optional but recommended.

---

## Configuration

The shared `config.yaml` (or equivalent) contains a `zmq_listeners` section:

```yaml
zmq_listeners:
  spectroradiometry:
    port: 6001          # PULL port epicsLogger binds — worker connects here
    enabled: true
    health_port: 6003   # REP port YOUR app binds — logger pings this
```

Load it in epicsLogger via **Config → ZMQ Listeners → Browse** and click
**Apply**.  The logger will immediately bind the PULL socket and start
listening.

---

## Trigger message (worker → logger)

Send a JSON object over the PUSH socket whenever new data is ready to log.

### Minimal trigger (no data payload)

```json
{"type": "trigger"}
```

epicsLogger will read all configured EPICS PVs and write a log record.  The
**Trigger** column will contain `"spectroradiometry"` (the worker name from
the config).

### Trigger with reduced data payload

```json
{
    "type": "trigger",
    "data": {
        "temperature_K":   1850.3,
        "emissivity":       0.912,
        "peak_wavelength": 850.1,
        "intensity":      4231.7
    }
}
```

All key/value pairs inside `"data"` are appended to the log record alongside
the EPICS PV values.  Keys become column names (using the labels declared in the
capabilities response); values are coerced to strings.

> **Column registration:** epicsLogger pre-declares payload columns when the log
> file is opened, using the field list fetched via the `"capabilities"` endpoint
> (see below).  Make sure to fetch capabilities and click **Apply** in *Configure
> Loggers* before opening a log file so all columns are present from the start.

---

## Python implementation (worker side)

```python
import zmq

EPICLOGGER_HOST = '127.0.0.1'   # or the IP of the machine running epicsLogger
TRIGGER_PORT    = 6001

ctx  = zmq.Context()
sock = ctx.socket(zmq.PUSH)
sock.connect(f'tcp://{EPICLOGGER_HOST}:{TRIGGER_PORT}')

def send_trigger(data: dict | None = None):
    """Call this whenever new spectroradiometry results are ready."""
    msg = {'type': 'trigger'}
    if data:
        msg['data'] = data
    sock.send_json(msg)

# Example usage inside your PyQt processing loop / signal handler:
def on_analysis_complete(results):
    send_trigger({
        'temperature_K':   results.temperature,
        'emissivity':       results.emissivity,
        'peak_wavelength': results.peak_nm,
    })
```

For a PyQt application, create the socket once (e.g. in `__init__`) and call
`send_trigger()` from whatever signal or callback fires when new results are
available.

---

## Health & capabilities protocol (worker side)

If `health_port` is configured, your app must bind a **REP** socket on that
port and respond to two message types:

| Request `type` | Purpose |
|---|---|
| `"ping"` | Liveness check — epicsLogger clicks **Check Health** |
| `"capabilities"` | Field discovery — epicsLogger auto-populates the ZMQ Triggers editor |

### Capabilities response format

```json
{
    "status": "ready",
    "worker_name": "spectroradiometry",
    "version": "1.0",
    "fields": [
        {"key": "temperature_K",   "label": "Temperature (K)",     "type": "float"},
        {"key": "emissivity",      "label": "Emissivity",          "type": "float"},
        {"key": "peak_wavelength", "label": "Peak Wavelength (nm)","type": "float"},
        {"key": "intensity",       "label": "Intensity",           "type": "float"}
    ]
}
```

- `key` — the dict key your app uses inside the `"data"` payload (required)
- `label` — the column header shown in the log file (optional; defaults to `key`)
- `type` — data type hint shown in the UI: `"float"`, `"int"`, `"str"` (optional)

epicsLogger uses this response to auto-populate the **Payload Fields** table in
the *Configure Loggers → ZMQ Triggers* editor.  The user can then rename labels
and uncheck fields they do not want logged — without needing to type anything
manually.

### HealthServer implementation (worker side)

```python
import zmq
from PyQt6.QtCore import QThread

# Declare your fields once so the capabilities reply is consistent
FIELDS = [
    {'key': 'temperature_K',   'label': 'Temperature (K)',      'type': 'float'},
    {'key': 'emissivity',      'label': 'Emissivity',           'type': 'float'},
    {'key': 'peak_wavelength', 'label': 'Peak Wavelength (nm)', 'type': 'float'},
    {'key': 'intensity',       'label': 'Intensity',            'type': 'float'},
]

class HealthServer(QThread):
    def __init__(self, port: int, worker_name: str, parent=None):
        super().__init__(parent)
        self._port = port
        self._name = worker_name
        self._running = False

    def run(self):
        ctx  = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.setsockopt(zmq.RCVTIMEO, 500)
        sock.setsockopt(zmq.LINGER, 0)
        sock.bind(f'tcp://*:{self._port}')
        self._running = True
        while self._running:
            try:
                msg = sock.recv_json()
                msg_type = msg.get('type')
                if msg_type == 'ping':
                    sock.send_json({
                        'status': 'ready',
                        'worker': self._name,
                    })
                elif msg_type == 'capabilities':
                    sock.send_json({
                        'status':      'ready',
                        'worker_name': self._name,
                        'version':     '1.0',
                        'fields':      FIELDS,
                    })
                else:
                    sock.send_json({'status': 'error', 'message': 'unknown type'})
            except zmq.Again:
                pass
        sock.close(linger=0)
        ctx.term()

    def stop(self):
        self._running = False
        self.wait(2000)
```

Start this thread in your app's `__init__` and stop it on `aboutToQuit`.

> **Tip:** Keep `FIELDS` in sync with the keys you actually put in the `"data"`
> payload.  epicsLogger will only log fields that exist in both the capabilities
> list *and* the trigger message.

---

## Sequence diagram

```
  Worker                         epicsLogger
  ──────                         ───────────
  analysis complete
  sock.send_json({"type":"trigger","data":{...}})
                    ──────────────────────────>
                                 recv_json() returns
                                 payload stored in zmqTriggerModel.last_payload
                                 triggered_signal emitted
                                 epicsLoggerModel.triggered() called
                                 EPICS PVs read
                                 payload["data"] merged into record
                                 log record written to file
                                 table row added in UI
```

---

## Checklist

- [ ] Create/update the shared `config.yaml` with the `zmq_listeners` section
- [ ] In your app `__init__`: create the PUSH socket and connect it
- [ ] Define `FIELDS` and start a `HealthServer` thread on `health_port`
- [ ] Load the config in epicsLogger via *ZMQ Listeners → Browse → Apply*
- [ ] In *Configure Loggers → ZMQ Triggers*: click **Fetch Fields from Worker** to auto-populate payload columns
- [ ] Check/uncheck fields and optionally rename labels, then click **Apply**
- [ ] Open a log file — all declared columns will be present from row 1
- [ ] Call `send_trigger(data={...})` on each new result
- [ ] Verify the **Trigger** column shows your worker name on each record
