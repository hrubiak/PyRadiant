# -*- coding: utf8 -*-
# PyRadiant - T-log v2: in-memory record store.

from __future__ import annotations

import os
from collections import deque
from dataclasses import replace
from datetime import datetime
from typing import Iterable, List, Optional

from ..helper.signal import Signal
from .record import TLogRecord


# File extensions the "Total" tab treats as data files in the folder scan.
# Matches what TemperatureModelConfiguration._load_raw_data accepts.
_DATA_EXTS = ('.spe', '.SPE', '.h5')


class TLogStore:
    """In-memory records for the currently viewed data folder.

    Views:
      * `latest` — bounded deque of records in arrival order (drives the
        "Latest" tab plot). Bound is `max_latest`, default 200.
      * `by_file` — dict keyed by file basename, one record per file (the
        most recent fit for that file).
      * `folder_order` — ordered list of file basenames representing the
        whole folder, sorted by filesystem time (earliest first). Drives
        the "Total" tab's x-axis so revisiting a file never reorders the
        plot. Files not yet fit appear as NaN gaps.

    All mutations emit `records_changed`. No I/O apart from a single
    `os.listdir` + `os.stat` sweep on `set_folder()`. The store is not
    thread-safe by design — call it from the Qt main thread.
    """

    DEFAULT_MAX_LATEST = 200

    def __init__(self, max_latest: int = DEFAULT_MAX_LATEST):
        self._latest: deque[TLogRecord] = deque(maxlen=int(max_latest))
        self._by_file: dict[str, TLogRecord] = {}
        self._by_file_frame: dict[tuple[str, int], TLogRecord] = {}
        self._folder_order: list[str] = []
        self._folder: Optional[str] = None

        self.records_changed = Signal()

    # -------- Folder tracking -----------------------------------------------
    @property
    def folder(self) -> Optional[str]:
        return self._folder

    def set_folder(self, folder: Optional[str]) -> None:
        """Record which folder the current in-memory records belong to.
        Scans the folder (if any) to seed `_folder_order` + placeholder
        records by filesystem time so the Total tab shows every file in
        file-creation order, even before anything has been fit.

        Emits records_changed if the folder scan loaded any files, so the
        Total tab repaints even when there is no T_log.txt yet to bulk_load.
        """
        self._folder = folder
        self._folder_order = []
        if not folder:
            return
        try:
            entries = os.listdir(folder)
        except OSError:
            return

        # Sort by earliest filesystem timestamp (ctime/mtime/atime) — same
        # rule the legacy StaticRecordManager used. Falls back to inf on
        # stat failure so broken entries sink to the end.
        timed: list[tuple[str, float]] = []
        for name in entries:
            # Skip dotfiles — on exFAT/FAT volumes macOS writes AppleDouble
            # sidecars (`._foo.spe`) alongside each real file, which would
            # otherwise double the folder count.
            if name.startswith('.'):
                continue
            if not name.endswith(_DATA_EXTS):
                continue
            full = os.path.join(folder, name)
            try:
                st = os.stat(full)
                t = min(st.st_ctime, st.st_mtime, st.st_atime)
            except OSError:
                t = float('inf')
            timed.append((name, t))
        timed.sort(key=lambda p: p[1])

        for name, t in timed:
            self._folder_order.append(name)
            try:
                ts = datetime.fromtimestamp(t)
            except (OSError, OverflowError, ValueError):
                ts = datetime.now()
            # Seed only if we don't already have a (fit) record for this
            # file. Placeholders carry NaN temperatures so plots gap them.
            if name not in self._by_file:
                self._by_file[name] = TLogRecord(file=name, timestamp=ts)
        if self._folder_order:
            self.records_changed.emit()

    def folder_order(self) -> List[str]:
        """Snapshot of the folder's file ordering (filesystem-time)."""
        return list(self._folder_order)

    # -------- Mutations -----------------------------------------------------
    def append(self, record: TLogRecord) -> None:
        self._latest.append(record)
        if record.file:
            existing = self._by_file.get(record.file)
            if existing is not None:
                # Preserve the original timestamp so re-fits of a file
                # don't move its point around on the Total tab.
                record = replace(record, timestamp=existing.timestamp)
            else:
                # New file arrived mid-session (e.g. via EPICS/AD watcher)
                # — append it to the folder order so it shows up on Total.
                self._folder_order.append(record.file)
            self._by_file[record.file] = record
            self._by_file_frame[(record.file, int(record.frame))] = record
        self.records_changed.emit()

    def bulk_load(self, records: Iterable[TLogRecord]) -> None:
        """Merge `records` into the store. Replaces the latest-deque but
        preserves any folder-order seeding done by set_folder() so files
        that exist on disk but aren't referenced in the log still show up
        as placeholders on the Total tab. Emits once at the end."""
        self._latest.clear()
        for r in records:
            self._latest.append(r)
            if r.file:
                existing = self._by_file.get(r.file)
                if existing is not None:
                    # Keep the folder-scan timestamp if we have one — the
                    # fit timestamp isn't recorded in T_log.txt so the
                    # record's own timestamp is just "now" and would reorder.
                    r = replace(r, timestamp=existing.timestamp)
                else:
                    # Log references a file not currently in the folder
                    # (deleted/renamed/moved). Keep it at the end.
                    self._folder_order.append(r.file)
                self._by_file[r.file] = r
                self._by_file_frame[(r.file, int(r.frame))] = r
        self.records_changed.emit()

    def clear(self) -> None:
        if (not self._latest and not self._by_file
                and not self._by_file_frame and not self._folder_order):
            # Skip the emit if nothing actually changed.
            return
        self._latest.clear()
        self._by_file.clear()
        self._by_file_frame.clear()
        self._folder_order = []
        self.records_changed.emit()

    # -------- Reads ---------------------------------------------------------
    def latest(self) -> List[TLogRecord]:
        """Snapshot of the bounded deque, oldest first."""
        return list(self._latest)

    def by_file(self) -> dict[str, TLogRecord]:
        """Snapshot of the per-file view."""
        return dict(self._by_file)

    def by_file_frame(self) -> dict[tuple[str, int], TLogRecord]:
        """Snapshot of the per-(file, frame) view — every fit frame kept."""
        return dict(self._by_file_frame)

    def frames_for_file(self, file: str) -> list[int]:
        """Sorted list of frame indices with recorded fits for `file`."""
        return sorted(
            frame for (f, frame) in self._by_file_frame.keys() if f == file
        )

    def snapshot(self) -> List[TLogRecord]:
        """Alias for latest() — the primary consumer for the widget."""
        return self.latest()

    def __len__(self) -> int:
        return len(self._latest)

    def is_empty(self) -> bool:
        return not self._latest and not self._by_file
