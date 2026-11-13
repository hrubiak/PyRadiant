# ZMQ health server dies on a non-JSON message

**Applies to:** a thread that binds a ZMQ `REP` socket and calls `sock.recv_json()` in a loop that only catches zmq exceptions. This is the typical ping/capabilities health server.
**Found in:** PyRadiant `ZmqHealthServerThread.run` (`pyradiant/controller/ZmqPublisherController.py`), fixed 2026-11-11.

## Symptom

While the app was idle, the health thread raised this (exact output from the VS Code debugger):

```
Exception has occurred: JSONDecodeError       (note: full exception trace is shown but execution is paused at: run)
Expecting value: line 1 column 1 (char 0)
StopIteration: 0

During handling of the above exception, another exception occurred:

  File "/Users/hrubiak/GitHub/PyRadiant/pyradiant/controller/ZmqPublisherController.py", line 84, in run (Current frame)
    msg = sock.recv_json()
          ^^^^^^^^^^^^^^^^
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

The `StopIteration: 0` is internal to Python's JSON scanner: it couldn't parse a value at character 0. That happens for an empty payload or one whose first character isn't valid JSON.

Outside a debugger nothing visible happens. The thread is gone, and health pings stop getting answers.

## Cause

```python
while not self._stop_flag:
    try:
        msg = sock.recv_json()
        msg_type = msg.get("type")
        ...                          # send_json(reply)
    except zmq.Again:
        pass
    except zmq.ZMQError:
        break
```

`recv_json()` is `recv()` followed by `json.loads()`. If a peer sends an empty frame or any non-JSON text (e.g. `ping`), `json.loads` raises `JSONDecodeError`. That isn't a `zmq.ZMQError`, so it escapes `run()` and the thread exits. Valid JSON that isn't an object (e.g. `"ping"`) fails the same way, one line later at `msg.get`.

The sender wasn't identified. On libzmq 4.3.5, raw non-ZMQ TCP traffic (HTTP, random bytes) is not delivered to a `REP` socket, so a port scanner is unlikely. The likely source is a ZMQ client sending a plain-text or empty ping, or a program misconfigured to this port.

**Why you can't just catch the exception and move on:** `REP` must `send` once after every `recv` before it can receive again. If the loop swallows the error without replying, the next `recv()` raises `zmq.ZMQError` (`EFSM`), and the `break` ends the thread anyway. With a `Poller`, the socket instead goes permanently silent. Malformed input must still get a reply.

## Fix

Receive raw bytes, decode them yourself, and let anything that isn't a JSON object fall through to the existing error reply:

```python
import json
...
while not self._stop_flag:
    try:
        raw = sock.recv()
        try:
            msg = json.loads(raw)            # ValueError covers JSONDecodeError and UnicodeDecodeError
        except ValueError:
            msg = None
        msg_type = msg.get("type") if isinstance(msg, dict) else None
        if msg_type == "ping":
            sock.send_json({"type": "pong", "status": "ready", "worker": self.worker_name})
        elif msg_type == "capabilities":
            sock.send_json({...})
        else:
            sock.send_json({"status": "error", "message": "unknown type"})   # garbage lands here
    except zmq.Again:
        pass
    except zmq.ZMQError:
        break
```

The happy path is unchanged.

## Verify

Start the server on a spare port. Send each probe from a fresh `REQ` socket with a receive timeout (a `REQ` that misses a reply is stuck, so don't reuse one):

```python
import json, zmq

def probe(port, payload):
    req = zmq.Context.instance().socket(zmq.REQ)
    req.setsockopt(zmq.RCVTIMEO, 2000)
    req.setsockopt(zmq.LINGER, 0)
    req.connect(f"tcp://127.0.0.1:{port}")
    try:
        req.send(payload)
        return req.recv()
    except zmq.Again:
        return None                          # no reply: thread died or REP is stuck
    finally:
        req.close()

for p in [b"\x16\x03\x01garbage", b"", b'"ping"', b"[1, 2]"]:
    assert probe(PORT, p) is not None, f"no reply to {p!r}"

# The real check: a valid ping still works after the garbage.
assert json.loads(probe(PORT, b'{"type": "ping"}'))["status"] == "ready"
```

Result in PyRadiant after the fix: each malformed probe got `{"status": "error", "message": "unknown type"}`, the real ping got `pong`, and the thread stayed alive.
