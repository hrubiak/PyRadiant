# -*- coding: utf8 -*-
# PyRadiant - T-log v2: atomic-rewrite JSON writer.
#
# Primary external artifact. One `T_history.json` per data folder, sitting
# next to the SPE files, so an external program can glob the folder and
# find the temperature history for correlation with pressure / XRD / etc.
#
# Key properties:
#   * Latest fit only — one entry per (file, frame). Re-fits overwrite.
#   * Atomic rewrite on every update (tmp + fsync + os.replace). A crash
#     mid-write leaves either the complete previous state or the complete
#     new state on disk, never a half-written file.
#   * Background drain thread; the Qt main thread never blocks on I/O.
#   * Naive-local timestamps (ISO-8601 strings), with the folder-wide
#     timezone label stored at the top of the file.
#   * NaN serialises as JSON null (strict-valid JSON).

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime
from math import isfinite
from typing import Dict, List, Optional, Tuple

from ..helper.signal import Signal
from .record import TLogRecord
from .writer import WriterState   # reuse the enum — same semantics


T_HISTORY_BASENAME = "T_history.json"
SCHEMA_VERSION = 1


def _local_timezone_label() -> str:
    """Best-effort naming of the current local timezone.

    Tries IANA name via datetime.astimezone() → zoneinfo → time.tzname.
    Returns a prose string like 'local, America/Chicago' or 'local, CST'.
    The label is informational — correlation consumers should treat the
    timestamp strings themselves as naive local wall-clock.
    """
    try:
        tz = datetime.now().astimezone().tzinfo
        if tz is not None:
            name = getattr(tz, "key", None) or str(tz)
            if name and name != "None":
                return f"local, {name}"
    except (ValueError, OSError):
        pass
    try:
        return f"local, {time.tzname[0]}"
    except (AttributeError, IndexError):
        return "local"


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat(timespec="microseconds")
    except (TypeError, ValueError):
        return None


def _finite_or_none(v) -> Optional[float]:
    """JSON spec rejects NaN/Inf. Collapse to null — unambiguous for readers."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if isfinite(f) else None


def _record_to_dict(r: TLogRecord) -> dict:
    """Canonical per-fit JSON object. Field order is preserved so the file
    diff is stable across writes (Python dicts are insertion-ordered)."""
    return {
        "file":                     r.file,
        "frame":                    int(r.frame),
        "exposure_start_local":     _iso_or_none(r.exposure_start_local),
        "exposure_start_source":    r.exposure_start_source,
        "fit_timestamp_local":      _iso_or_none(r.fit_timestamp_local),
        "T_DS":                     _finite_or_none(r.T_DS),
        "T_DS_err":                 _finite_or_none(r.T_DS_error),
        "T_US":                     _finite_or_none(r.T_US),
        "T_US_err":                 _finite_or_none(r.T_US_error),
        "scaling_DS":               _finite_or_none(r.scaling_DS),
        "scaling_US":               _finite_or_none(r.scaling_US),
        "counts_DS":                _finite_or_none(r.counts_DS),
        "counts_US":                _finite_or_none(r.counts_US),
        "fringe_freq_DS":           _finite_or_none(r.ds_fringe_frequency),
        "fringe_freq_US":           _finite_or_none(r.us_fringe_frequency),
        "fringe_nd_DS":             _finite_or_none(r.ds_fringe_nd_um),
        "fringe_nd_US":             _finite_or_none(r.us_fringe_nd_um),
        "exposure":                 _finite_or_none(r.exposure_time_sec),
        "gain":                     _finite_or_none(r.gain),
        "detector":                 r.detector,
        "mode":                     r.mode,
    }


def _dict_to_record(d: dict) -> Optional[TLogRecord]:
    """Reverse of _record_to_dict. Returns None on any failure so a loader
    can skip bad entries without failing the whole file. Keeps NaN out of
    the dataclass by substituting it where JSON null came through."""
    try:
        def _nan(v):
            return float("nan") if v is None else float(v)

        def _dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except (TypeError, ValueError):
                return None

        fit_ts = _dt(d.get("fit_timestamp_local")) or datetime.now()
        exp_ts = _dt(d.get("exposure_start_local"))
        # Store a timestamp usable as a folder-order fallback key — prefer
        # exposure start (physics time) over fit time; fall back to now().
        order_ts = exp_ts or fit_ts

        return TLogRecord(
            file=str(d.get("file", "")),
            frame=int(d.get("frame", 0) or 0),
            path="",                       # not persisted; derived from folder
            T_DS=_nan(d.get("T_DS")),
            T_US=_nan(d.get("T_US")),
            T_DS_error=_nan(d.get("T_DS_err")),
            T_US_error=_nan(d.get("T_US_err")),
            detector=str(d.get("detector", "unspecified")),
            exposure_time_sec=float(d.get("exposure") or 0.0),
            gain=float(d.get("gain") or 1.0),
            scaling_DS=_nan(d.get("scaling_DS")),
            scaling_US=_nan(d.get("scaling_US")),
            counts_DS=_nan(d.get("counts_DS")),
            counts_US=_nan(d.get("counts_US")),
            ds_fringe_frequency=_nan(d.get("fringe_freq_DS")),
            us_fringe_frequency=_nan(d.get("fringe_freq_US")),
            ds_fringe_nd_um=_nan(d.get("fringe_nd_DS")),
            us_fringe_nd_um=_nan(d.get("fringe_nd_US")),
            mode=str(d.get("mode", "dual")),
            exposure_start_local=exp_ts,
            exposure_start_source=str(d.get("exposure_start_source", "unknown")),
            fit_timestamp_local=fit_ts,
            timestamp=order_ts,
        )
    except Exception:
        return None


def _atomic_write_json(path: str, payload: dict) -> None:
    """Write `payload` as JSON to `path` atomically.

    tmp file is in the same directory so os.replace is a true rename on
    the same filesystem (os.replace across filesystems degrades to copy
    which is NOT atomic). fsync both the file and (on POSIX) the directory
    so a power loss can't leave the rename unflushed.
    """
    folder = os.path.dirname(path) or "."
    data = json.dumps(payload, indent=2, allow_nan=False)
    fd, tmp = tempfile.mkstemp(prefix=".T_history.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # Durability-on-metadata: fsync the directory entry so the rename
        # itself survives power loss. Skipped on Windows (no directory
        # fsync concept) and treated as best-effort elsewhere.
        if os.name != "nt":
            try:
                dir_fd = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    except Exception:
        # Clean up the tmp if we never got to replace().
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


class TLogJsonWriter:
    """Background-thread writer for T_history.json.

    Companion to TLogWriter (TSV append). Same open/close/enqueue API,
    same WriterState enum, same signals — the controller treats them as
    interchangeable sinks and wires both.

    Semantics divergence from TLogWriter:
      * `latest-only`: records are keyed by (file, frame); re-fits replace.
      * `rewrite-on-update`: every drained record (or burst of records)
        triggers one atomic rewrite of the whole file. The tmp+fsync+rename
        sequence guarantees no partial-write corruption.
    """

    QUEUE_MAX = 4096
    STDERR_MIN_INTERVAL_SEC = 5.0
    REOPEN_BACKOFF_START_SEC = 0.5
    REOPEN_BACKOFF_MAX_SEC = 4.0

    def __init__(self, filename: str = T_HISTORY_BASENAME):
        self._filename = filename
        self._folder: Optional[str] = None
        self._path: Optional[str] = None

        self._records: Dict[Tuple[str, int], TLogRecord] = {}
        self._records_lock = threading.Lock()

        self._q: "queue.Queue[Optional[TLogRecord]]" = queue.Queue(
            maxsize=self.QUEUE_MAX
        )
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._state = WriterState.DISABLED
        self._state_lock = threading.Lock()

        self.writes_ok = 0
        self.writes_failed = 0
        self.drops_queue_full = 0
        self._last_stderr_ts: dict[str, float] = {}
        self._reopen_backoff = self.REOPEN_BACKOFF_START_SEC

        self.state_changed = Signal()
        self.write_failed = Signal()

    # -------- Public API — call from main thread ----------------------------
    @property
    def state(self) -> WriterState:
        with self._state_lock:
            return self._state

    @property
    def folder(self) -> Optional[str]:
        return self._folder

    @property
    def path(self) -> Optional[str]:
        return self._path

    def open(self, folder: Optional[str]) -> bool:
        """Open (or reopen) the writer for `folder`.

        Loads existing T_history.json contents if the file is present and
        parseable, so re-fits keep accumulating rather than wiping prior
        work. Returns True on success (writable folder), False otherwise.
        """
        if folder == self._folder and self.state == WriterState.OK:
            return True

        self.close()

        if (not folder
                or not os.path.isdir(folder)
                or not os.access(folder, os.W_OK)):
            self._folder = folder
            self._path = None
            self._set_state(WriterState.DISABLED)
            return False

        self._folder = folder
        self._path = os.path.join(folder, self._filename)
        # Preload existing contents so re-fits merge in rather than wipe.
        with self._records_lock:
            self._records = self._load_existing() or {}
        self._set_state(WriterState.OK)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._drain_loop, name="TLogJsonWriter", daemon=True
        )
        self._thread.start()
        return True

    def close(self) -> None:
        if self._thread is not None:
            self._stop.set()
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout=2.0)
            self._thread = None
        self._path = None
        self._folder = None
        with self._records_lock:
            self._records = {}
        self._set_state(WriterState.DISABLED)
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def enqueue(self, record: TLogRecord) -> None:
        """Add a record to the write queue. Never raises, never blocks."""
        if self.state == WriterState.DISABLED:
            return
        try:
            self._q.put_nowait(record)
        except queue.Full:
            self.drops_queue_full += 1
            self._stderr_rate_limited(
                "queue_full",
                f"TLogJsonWriter: queue full, dropped 1 record "
                f"(total drops={self.drops_queue_full})",
            )

    def truncate(self) -> None:
        """Clear the in-memory latest-only map and rewrite an empty file."""
        with self._records_lock:
            self._records = {}
        self.enqueue(_FLUSH_SENTINEL)  # type: ignore[arg-type]

    def read_all(self) -> List[TLogRecord]:
        """Return every stored record. Order: by exposure_start_local then
        (file, frame). Runs on the calling thread; the drain thread does
        not read this map concurrently without the lock."""
        with self._records_lock:
            items = list(self._records.values())
        items.sort(
            key=lambda r: (
                r.exposure_start_local or r.fit_timestamp_local or datetime.min,
                r.file,
                r.frame,
            )
        )
        return items

    # -------- Drain thread --------------------------------------------------
    def _drain_loop(self) -> None:
        dirty = False
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                if dirty:
                    if self._flush():
                        dirty = False
                continue

            if item is None:
                break

            if item is _FLUSH_SENTINEL:
                self._flush()
                dirty = False
                continue

            # Merge the record into the latest-only map.
            with self._records_lock:
                self._records[(item.file, int(item.frame))] = item
            dirty = True

            # Coalesce bursts: drain anything else queued right now so one
            # atomic rewrite captures the whole burst.
            while True:
                try:
                    extra = self._q.get_nowait()
                except queue.Empty:
                    break
                if extra is None:
                    self._stop.set()
                    break
                if extra is _FLUSH_SENTINEL:
                    continue  # handled by trailing flush below
                with self._records_lock:
                    self._records[(extra.file, int(extra.frame))] = extra

            if self._flush():
                dirty = False

        if dirty:
            self._flush()

    def _flush(self) -> bool:
        """Atomically rewrite the JSON file from the current in-memory map.
        Returns True on success, False on failure (state → DEGRADED)."""
        if not self._path:
            return False
        with self._records_lock:
            records = list(self._records.values())

        records.sort(
            key=lambda r: (
                r.exposure_start_local or r.fit_timestamp_local or datetime.min,
                r.file,
                r.frame,
            )
        )

        payload = {
            "schema_version":     SCHEMA_VERSION,
            "timezone":           _local_timezone_label(),
            "folder":             self._folder,
            "last_updated_local": datetime.now().isoformat(timespec="microseconds"),
            "fits":               [_record_to_dict(r) for r in records],
        }

        try:
            _atomic_write_json(self._path, payload)
            self.writes_ok += 1
            if self.state == WriterState.DEGRADED:
                self._set_state(WriterState.OK)
            self._reopen_backoff = self.REOPEN_BACKOFF_START_SEC
            return True
        except OSError as exc:
            self.writes_failed += 1
            self._set_state(WriterState.DEGRADED)
            self.write_failed.emit(f"write failed: {exc}")
            self._stderr_rate_limited(
                "write", f"TLogJsonWriter: write failed: {exc}"
            )
            # Backoff before the next retry attempt. The next enqueue'd
            # record (or a subsequent flush) will drive it.
            time.sleep(self._reopen_backoff)
            self._reopen_backoff = min(
                self._reopen_backoff * 2.0, self.REOPEN_BACKOFF_MAX_SEC
            )
            return False

    # -------- Loader --------------------------------------------------------
    def _load_existing(self) -> Dict[Tuple[str, int], TLogRecord]:
        """Parse T_history.json if present. Returns {} on any error so a
        corrupt file (shouldn't happen with atomic writes, but) doesn't
        prevent recovery — the next flush overwrites it."""
        if not self._path or not os.path.isfile(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self._stderr_rate_limited(
                "load", f"TLogJsonWriter: load({self._path!r}) failed: {exc}"
            )
            return {}

        fits = payload.get("fits") if isinstance(payload, dict) else None
        if not isinstance(fits, list):
            return {}
        out: Dict[Tuple[str, int], TLogRecord] = {}
        for d in fits:
            if not isinstance(d, dict):
                continue
            rec = _dict_to_record(d)
            if rec is None or not rec.file:
                continue
            out[(rec.file, int(rec.frame))] = rec
        return out

    # -------- Utilities -----------------------------------------------------
    def _set_state(self, new_state: WriterState) -> None:
        with self._state_lock:
            if self._state == new_state:
                return
            self._state = new_state
        self.state_changed.emit(new_state)

    def _stderr_rate_limited(self, key: str, msg: str) -> None:
        now = time.monotonic()
        last = self._last_stderr_ts.get(key, 0.0)
        if now - last < self.STDERR_MIN_INTERVAL_SEC:
            return
        self._last_stderr_ts[key] = now
        print(msg, file=sys.stderr)


class _FlushSentinel:
    __slots__ = ()


_FLUSH_SENTINEL = _FlushSentinel()
