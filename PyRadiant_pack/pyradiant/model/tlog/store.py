# -*- coding: utf8 -*-
# PyRadiant - T-log v2: in-memory record store.

from __future__ import annotations

from collections import deque
from typing import Iterable, List, Optional

from ..helper.signal import Signal
from .record import TLogRecord


class TLogStore:
    """In-memory records for the currently viewed data folder.

    Two views on the same source of truth:
      * `latest` — a bounded deque of the most recent records (drives the
        "Latest" tab plot). Bound is `max_latest`, default 200, matching the
        legacy DATALOG_LENGTH.
      * `by_file` — dict keyed by file basename, one record per file (the
        most recent one). Drives the "Total" tab and the cursor-highlight
        logic in the widget.

    All mutations emit `records_changed`. No I/O. The store is not
    thread-safe by design — call it from the Qt main thread. The writer's
    background thread never touches this object.
    """

    DEFAULT_MAX_LATEST = 200

    def __init__(self, max_latest: int = DEFAULT_MAX_LATEST):
        self._latest: deque[TLogRecord] = deque(maxlen=int(max_latest))
        self._by_file: dict[str, TLogRecord] = {}
        self._folder: Optional[str] = None

        # Fires after any mutation. Subscribers should call snapshot() /
        # latest() / by_file() to read state — the signal payload is
        # intentionally empty to keep the contract simple.
        self.records_changed = Signal()

    # -------- Folder tracking -----------------------------------------------
    @property
    def folder(self) -> Optional[str]:
        return self._folder

    def set_folder(self, folder: Optional[str]) -> None:
        """Record which folder the current in-memory records belong to.
        Does NOT clear on its own — the controller decides whether to
        clear() first and then load new records via bulk_load()."""
        self._folder = folder

    # -------- Mutations -----------------------------------------------------
    def append(self, record: TLogRecord) -> None:
        self._latest.append(record)
        if record.file:
            self._by_file[record.file] = record
        self.records_changed.emit()

    def bulk_load(self, records: Iterable[TLogRecord]) -> None:
        """Replace the current contents with `records`. Emits once at the
        end so a folder switch doesn't fire N intermediate updates."""
        self._latest.clear()
        self._by_file.clear()
        for r in records:
            self._latest.append(r)
            if r.file:
                self._by_file[r.file] = r
        self.records_changed.emit()

    def clear(self) -> None:
        if not self._latest and not self._by_file:
            # Skip the emit if nothing actually changed — avoids redundant
            # widget repaints during folder-switch teardown.
            return
        self._latest.clear()
        self._by_file.clear()
        self.records_changed.emit()

    # -------- Reads ---------------------------------------------------------
    def latest(self) -> List[TLogRecord]:
        """Snapshot of the bounded deque, oldest first."""
        return list(self._latest)

    def by_file(self) -> dict[str, TLogRecord]:
        """Snapshot of the per-file view."""
        return dict(self._by_file)

    def snapshot(self) -> List[TLogRecord]:
        """Alias for latest() — the primary consumer for the widget."""
        return self.latest()

    def __len__(self) -> int:
        return len(self._latest)

    def is_empty(self) -> bool:
        return not self._latest and not self._by_file
