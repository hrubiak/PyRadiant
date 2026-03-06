# Schema Query Protocol — Instructions for the Spectroradiometry Worker

## Overview

The `epicsLogViewer` coordinator can ask each worker to describe the result
columns it will produce.  This uses the **same `health_port` REP socket** that
already handles ping health checks.  The socket simply needs to respond to a
second message type: `"schema"`.

For the spectroradiometry worker the relevant ports from `config.yaml` are:

| Purpose | Port |
|---|---|
| Job dispatch (coordinator PUSH → worker PULL) | **5561** |
| Health / schema queries (coordinator REQ → worker REP) | **5562** |
| Results (worker PUSH → coordinator PULL) | **5554** |

---

## What to implement

### 1 — Extend the existing REP socket handler

Your REP socket loop already dispatches on `msg["type"]`.  Add a branch for
`"schema"` alongside the existing `"ping"` branch:

```python
import zmq

context = zmq.Context()
rep_sock = context.socket(zmq.REP)
rep_sock.bind("tcp://*:5562")   # health_port from config.yaml

while running:
    msg = rep_sock.recv_json()
    msg_type = msg.get("type")

    if msg_type == "ping":
        rep_sock.send_json({
            "type":   "pong",
            "status": "ready",
            "worker": "spectroradiometry",
        })

    elif msg_type == "schema":
        rep_sock.send_json({
            "type": "schema_reply",
            "columns": _build_schema(),
        })

    else:
        # Unknown type — must still reply to unblock the REQ socket
        rep_sock.send_json({"type": "error", "message": f"unknown type: {msg_type}"})
```

> **Important:** A ZMQ REP socket **must always send exactly one reply per
> received message**.  Even for unknown message types, send something back —
> otherwise the coordinator's REQ socket will block until its 2 s timeout.

---

### 2 — Define `_build_schema()`

Return a list of column descriptor dicts.  Each dict has three keys:

| Key | Type | Description |
|---|---|---|
| `name` | `str` | Key used in the **result payload** pushed to port 5554 |
| `label` | `str` | Human-readable column header shown in epicsLogViewer |
| `unit` | `str` | Optional unit string (empty string if not applicable) |

The `name` value **must exactly match** the key you use in the result dict you
push back to the coordinator.  The viewer maps result payloads to table columns
by this key.

Example:

```python
def _build_schema() -> list:
    return [
        {"name": "peak_wavelength",  "label": "Peak Wavelength",  "unit": "nm"},
        {"name": "peak_irradiance",  "label": "Peak Irradiance",  "unit": "W/m²/nm"},
        {"name": "integrated_power", "label": "Integrated Power", "unit": "W/m²"},
        {"name": "color_temp",       "label": "Colour Temperature", "unit": "K"},
    ]
```

Replace the column list with whatever your worker actually computes and returns.

---

### 3 — Result payload (reminder)

When you push a result back to the coordinator on port 5554 the `values` dict
must use the same keys you declared in the schema:

```python
push_sock.send_json({
    "type":      "result",
    "worker":    "spectroradiometry",
    "row":       job["row"],            # echo back the row index from the job
    "timestamp": job.get("timestamp"),  # echo back for correlation
    "values": {
        "peak_wavelength":  543.2,
        "peak_irradiance":  1.84e-3,
        "integrated_power": 0.217,
        "color_temp":       5600,
    },
})
```

---

## Protocol summary

```
coordinator (REQ)                     worker REP socket (port 5562)
      │                                         │
      │  {"type": "schema"}  ──────────────────>│
      │                                         │  build column list
      │  <──────────────────────────────────── │
      │  {"type": "schema_reply",               │
      │   "columns": [                          │
      │     {"name": "peak_wavelength",         │
      │      "label": "Peak Wavelength",        │
      │      "unit": "nm"},                     │
      │     ...                                 │
      │   ]}                                    │
```

The coordinator:
- Opens a **new** `zmq.REQ` socket for each query (never reused).
- Sets `RCVTIMEO = 2000 ms` and `LINGER = 0` before connecting.
- Connects to `tcp://127.0.0.1:5562`.

The worker:
- **Binds** the REP socket (never connects).
- Handles both `"ping"` and `"schema"` on the same socket.
- The schema can be a static list defined at startup — it does not need to be
  computed dynamically unless your column set changes at runtime.

---

## Checklist

- [ ] REP socket bound on port **5562**
- [ ] `"ping"` → `{"type": "pong", "status": "ready", "worker": "spectroradiometry"}`
- [ ] `"schema"` → `{"type": "schema_reply", "columns": [...]}`
- [ ] Every `columns` entry has `name`, `label`, `unit` keys
- [ ] `name` values match keys in your result `values` dict
- [ ] Unknown message types still receive a reply (no silent drops)
