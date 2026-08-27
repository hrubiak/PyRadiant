# -*- coding: utf8 -*-
# PyRadiant - T-log v2: non-blocking file writer.

from __future__ import annotations

import csv
import os
import queue
import sys
import threading
import time
from enum import Enum
from typing import Iterable, List, Optional

from ..helper.signal import Signal
from .record import TLogRecord, LEGACY_TSV_HEADER, _LEGACY_TO_FIELD


T_LOG_BASENAME = "T_log.txt"


class WriterState(str, Enum):
    """Coarse writer status for the UI status strip.

    ok        — writer is open for the current folder, drain thread alive,
                no recent failures.
    disabled  — no writable log file (folder not writable, or none set).
                In-memory recording continues; disk is skipped.
    degraded  — writer opened once but a recent write failed. Will retry
                on the next enqueue with exponential backoff.
    """

    OK = "ok"
    DISABLED = "disabled"
    DEGRADED = "degraded"


class TLogWriter:
    """Background-thread writer for T_log.txt.

    Contract:
      * enqueue() NEVER raises and NEVER blocks the caller. If the queue is
        full (extremely unlikely at experiment rates), the record is dropped
        and a counter is incremented.
      * All I/O — open, write, flush, close, read — is caught. Failures set
        state to DEGRADED / DISABLED and are logged to stderr (rate-limited)
        but never propagate to the compute path.
      * The Qt main thread only ever reads .state, .folder, .path, and the
        stats counters. Everything else is done on the drain thread.

    Not a QObject — it uses the codebase's custom Signal so the writer can
    be used from non-Qt contexts (tests) without a QApplication.
    """

    # Bounded so a stuck disk can never eat unbounded memory. At the typical
    # 1-record-per-frame experiment rate, this is minutes of headroom.
    QUEUE_MAX = 4096

    # Rate-limit stderr spam when the disk is unhappy. One line per N seconds
    # per failure kind; totals still incremented every time.
    STDERR_MIN_INTERVAL_SEC = 5.0

    # Exponential backoff for reopening a degraded log. Capped so we never
    # sleep the drain thread for more than a few seconds; recovery is fast
    # once the disk comes back.
    REOPEN_BACKOFF_START_SEC = 0.5
    REOPEN_BACKOFF_MAX_SEC = 4.0

    def __init__(self, filename: str = T_LOG_BASENAME):
        self._filename = filename

        self._folder: Optional[str] = None
        self._path: Optional[str] = None
        self._fh = None  # file object, drain thread only

        self._q: "queue.Queue[Optional[TLogRecord]]" = queue.Queue(
            maxsize=self.QUEUE_MAX
        )
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._state = WriterState.DISABLED
        self._state_lock = threading.Lock()

        # Stats — read by the widget's status strip. Simple atomic ints;
        # writes are drain-thread only, reads are main-thread. Python ints
        # are atomic under CPython, so no lock needed for observation.
        self.writes_ok = 0
        self.writes_failed = 0
        self.drops_queue_full = 0
        self._last_stderr_ts: dict[str, float] = {}
        self._reopen_backoff = self.REOPEN_BACKOFF_START_SEC

        # Public signals (fire from the drain thread — subscribers that
        # touch Qt widgets should route through Qt's queued connections
        # or a QTimer.singleShot(0, ...)).
        self.state_changed = Signal()  # emits WriterState
        self.write_failed = Signal()   # emits str (short reason)

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

        Returns True if the file is writable and the drain thread is alive.
        False (state=DISABLED) if `folder` is falsy, doesn't exist, or is
        not writable. Never raises. Safe to call repeatedly.
        """
        # Idempotent: same folder + still healthy = no-op.
        if folder == self._folder and self.state == WriterState.OK:
            return True

        self.close()

        if not folder or not os.path.isdir(folder) or not os.access(folder, os.W_OK):
            self._folder = folder
            self._path = None
            self._set_state(WriterState.DISABLED)
            return False

        self._folder = folder
        self._path = os.path.join(folder, self._filename)
        # Transition to OK synchronously — the writable check just passed.
        # If the drain thread later fails to actually open the file (rare
        # race — folder disappeared between check and open), it will demote
        # to DEGRADED. Enqueues queued during the drain-thread startup
        # window are safe because state is already OK.
        self._set_state(WriterState.OK)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._drain_loop, name="TLogWriter", daemon=True
        )
        self._thread.start()
        return True

    def close(self) -> None:
        """Stop the drain thread and close the file. Never raises."""
        if self._thread is not None:
            self._stop.set()
            # Wake the drain loop out of queue.get()
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout=2.0)
            self._thread = None
        self._drain_close_file()
        self._path = None
        self._folder = None
        self._set_state(WriterState.DISABLED)
        # Drain leftovers in the queue so a subsequent open() doesn't
        # replay stale records into a new log.
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def enqueue(self, record: TLogRecord) -> None:
        """Add a record to the write queue. Never raises, never blocks.

        If the writer is DISABLED (no folder / not writable), the record
        is silently dropped from a disk-persistence standpoint — the
        in-memory store is where display data lives.
        """
        if self.state == WriterState.DISABLED:
            return
        try:
            self._q.put_nowait(record)
        except queue.Full:
            self.drops_queue_full += 1
            self._stderr_rate_limited(
                "queue_full",
                f"TLogWriter: queue full, dropped 1 record (total drops={self.drops_queue_full})",
            )

    def truncate(self) -> None:
        """Wipe the current log file and rewrite the header. Used by the
        'Clear log' button. Never raises."""
        # Push a sentinel that the drain loop interprets as 'truncate'.
        # Simpler than reaching across threads for the file handle.
        self.enqueue(_TRUNCATE_SENTINEL)  # type: ignore[arg-type]

    def read_all(self) -> List[TLogRecord]:
        """Read every record from the current log file, oldest first.
        Returns [] if no log, unreadable, or malformed. Never raises.

        This runs on the CALLING thread (the controller uses it on folder
        switch, before starting live writes for the new folder). The drain
        thread does not touch it, so no lock is needed.
        """
        if not self._path or not os.path.isfile(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                header = f.readline()
                if not header:
                    return []
                # Prefer the header found in the file; fall back to legacy
                # if the file has been written by an old version.
                fieldnames = header.strip().lstrip("#").strip().split("\t")
                # DictReader expects the # File label — recompute using
                # the file's actual header, minus the leading "# ".
                reader = csv.DictReader(
                    f, fieldnames=header.strip().split("\t"), delimiter="\t"
                )
                out: List[TLogRecord] = []
                for row in reader:
                    if not row:
                        continue
                    rec = TLogRecord.from_legacy_tsv_row(row)
                    if rec is not None:
                        out.append(rec)
                return out
        except OSError as exc:
            self._stderr_rate_limited(
                "read_all", f"TLogWriter: read_all failed: {exc}"
            )
            return []

    # -------- Drain thread --------------------------------------------------
    def _drain_loop(self) -> None:
        """Background loop: open file, drain queue, handle failures.

        If the initial open fails, stay in the loop — subsequent records
        will trigger a backoff reopen via _drain_write_record.
        """
        self._drain_open_file()

        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is None:
                # Shutdown sentinel.
                break

            if item is _TRUNCATE_SENTINEL:
                self._drain_truncate()
                continue

            self._drain_write_record(item)

        self._drain_close_file()

    def _drain_open_file(self) -> bool:
        try:
            # Append mode — resume without wiping existing records.
            self._fh = open(self._path, "a", encoding="utf-8")
            # Only write the header if the file is empty (new/truncated).
            try:
                if os.path.getsize(self._path) == 0:
                    self._fh.write(LEGACY_TSV_HEADER)
                    self._fh.flush()
            except OSError:
                pass
            self._set_state(WriterState.OK)
            self._reopen_backoff = self.REOPEN_BACKOFF_START_SEC
            return True
        except OSError as exc:
            self._set_state(WriterState.DEGRADED)
            self.write_failed.emit(f"open failed: {exc}")
            self._stderr_rate_limited(
                "open", f"TLogWriter: open({self._path!r}) failed: {exc}"
            )
            self._fh = None
            return False

    def _drain_write_record(self, record: TLogRecord) -> None:
        if self._fh is None:
            if not self._reopen_with_backoff():
                self.writes_failed += 1
                return

        try:
            self._fh.write(record.to_tsv_row() + "\n")
            self._fh.flush()
            self.writes_ok += 1
            if self.state == WriterState.DEGRADED:
                self._set_state(WriterState.OK)
        except OSError as exc:
            self.writes_failed += 1
            self._set_state(WriterState.DEGRADED)
            self.write_failed.emit(f"write failed: {exc}")
            self._stderr_rate_limited(
                "write", f"TLogWriter: write failed: {exc}"
            )
            self._drain_close_file()

    def _drain_truncate(self) -> None:
        if self._fh is None:
            if not self._reopen_with_backoff():
                return
        try:
            self._fh.seek(0)
            self._fh.truncate(0)
            self._fh.write(LEGACY_TSV_HEADER)
            self._fh.flush()
        except OSError as exc:
            self._set_state(WriterState.DEGRADED)
            self.write_failed.emit(f"truncate failed: {exc}")
            self._stderr_rate_limited(
                "truncate", f"TLogWriter: truncate failed: {exc}"
            )
            self._drain_close_file()

    def _drain_close_file(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def _reopen_with_backoff(self) -> bool:
        """Try to reopen the file after a failure, sleeping the drain thread
        briefly. Returns True on success."""
        if self._stop.is_set():
            return False
        time.sleep(self._reopen_backoff)
        self._reopen_backoff = min(
            self._reopen_backoff * 2.0, self.REOPEN_BACKOFF_MAX_SEC
        )
        return self._drain_open_file()

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


# Module-level sentinel for the truncate command; not a TLogRecord so we
# can identity-compare in the drain loop without a type check per item.
class _TruncateSentinel:
    __slots__ = ()


_TRUNCATE_SENTINEL = _TruncateSentinel()
