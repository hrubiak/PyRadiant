# -*- coding: utf8 -*-
# PyRadiant - GUI program for analysis of thermal spectra during
# laser heated diamond anvil cell experiments
# Copyright (C) 2024 Ross Hrubiak (hrubiak@anl.gov)
# High Pressure Collaborative Access Team, Argonne National Laboratory
#
# T-log v2 controller — replaces DataLogController.

import os
import sys
import time
from datetime import date

from PyQt6 import QtCore

from ..model.TemperatureModel import TemperatureModel
from ..model.TemperatureModelConfiguration import TemperatureModelConfiguration
from ..model.tlog import TLogStore, TLogWriter, TLogJsonWriter, TLogRecord, WriterState
from ..widget.DataHistoryWidget import dataHistoryWidget


CONFIG_BACKUP_DIRNAME = "config_backup"
CONFIG_BACKUP_DEBOUNCE_MS = 10_000
# Log any backup write whose wall-clock exceeds this. Expected normal is
# tens of ms for a small .trs on local disk; a kinetics config with
# embedded cal stacks can take hundreds of ms. Anything above a second
# on the main thread will be felt as a UI hiccup.
CONFIG_BACKUP_SLOW_SEC = 0.25


class TLogController(QtCore.QObject):
    """Thin glue: store + writer + widget + model events.

    Data flow:
        config.tlog_record_ready(rec)  →  store.append(rec)
                                       →  writer.enqueue(rec)  [fire-and-forget]
        store.records_changed          →  _refresh_plots()

        config.data_folder_changed(f)  →  writer.close()
                                       →  store.clear()
                                       →  writer.open(f)
                                       →  store.bulk_load(writer.read_all())

        model.configuration_selected   →  rebind to new current config
                                       →  sync folder

    Cursor interaction (both tabs):
        widget cursor dragged  →  _on_cursor_record_selected(rec)
                               →  cfg.load_data_image + set_img_frame_number_to
        cfg.data_changed_signal→  _on_current_record_changed()
                               →  widget.set_cursor_to_file_frame
        Loop prevention: programmatic cursor moves block the InfiniteLine
        signals (see historyPlotWidget.set_cursor_to_file_frame).

    Nothing here blocks on disk. All exceptions in writer are caught and
    surfaced as WriterState transitions; the store keeps updating so the
    plot never stops responding to live fits.
    """

    def __init__(self, model: TemperatureModel,
                 widget: dataHistoryWidget):
        super().__init__()
        self.model = model
        self.widget = widget
        self.store = TLogStore()
        self.writer = TLogWriter()
        self.json_writer = TLogJsonWriter()

        self._bound_config: TemperatureModelConfiguration | None = None

        # Per-folder config-backup state. A config "counts as used" in a
        # folder once it has emitted at least one tlog record while that
        # folder was active. We debounce writes per (folder, cfg_idx) and
        # hard-flush on folder change and shutdown.
        self._cfg_used: dict[str, set[int]] = {}
        self._cfg_debounce: dict[tuple[str, int], QtCore.QTimer] = {}
        self._cfg_session_date: dict[tuple[str, int], str] = {}
        # Backup write counters — reported in stderr summaries so a long
        # beamline session can tell at a glance whether the mirror is
        # healthy. Reset per controller lifetime (which is per app run).
        self._cfg_backup_writes_ok: int = 0
        self._cfg_backup_writes_failed: int = 0
        self._cfg_backup_total_sec: float = 0.0
        self._cfg_backup_max_sec: float = 0.0

        # Static plot titles
        self.widget.setWindowTitle('Temperature Log')
        self.widget.temperatures_plot_widget.update_time_lapse_ds_temperature_txt('Downstream')
        self.widget.temperatures_plot_widget.update_time_lapse_us_temperature_txt('Upstream')

        # Widget → controller
        self.widget.clear_data_log_file_btn.clicked.connect(self._on_clear_clicked)
        self.widget.temperatures_plot_widget.cursor_record_changed.connect(
            self._on_cursor_record_selected)

        # Store → widget
        self.store.records_changed.connect(self._refresh_plots)

        # Writer → widget status strip (fires from the drain thread; route
        # through a queued slot so we touch Qt on the main thread only).
        self.writer.state_changed.connect(self._on_writer_state_changed_bg)

        # Model → controller (configuration switching)
        self.model.configuration_selected.connect(self._on_configuration_selected)

        self._bind(self.model.current_configuration)

    # -------- Configuration binding -----------------------------------------
    def _bind(self, cfg: TemperatureModelConfiguration) -> None:
        if self._bound_config is cfg:
            return
        if self._bound_config is not None:
            for sig, slot in (
                (self._bound_config.data_folder_changed, self._on_folder_changed),
                (self._bound_config.tlog_record_ready, self._on_record_ready),
                (self._bound_config.data_changed_signal, self._on_current_record_changed),
            ):
                try:
                    sig.disconnect(slot)
                except (ValueError, RuntimeError, TypeError):
                    pass
        self._bound_config = cfg
        cfg.data_folder_changed.connect(self._on_folder_changed)
        cfg.tlog_record_ready.connect(self._on_record_ready)
        cfg.data_changed_signal.connect(self._on_current_record_changed)
        self._switch_to_folder(cfg.get_data_folder())

    def _on_configuration_selected(self, _ind) -> None:
        self._bind(self.model.current_configuration)

    # -------- Folder switching ----------------------------------------------
    def _switch_to_folder(self, folder) -> None:
        """The one and only folder-switch path. Any caller that changes the
        viewed folder must reach us via config.data_folder_changed — never
        by touching store/writer directly."""
        self._flush_all_config_backups()
        self.writer.close()
        self.json_writer.close()
        self.store.clear()
        self.store.set_folder(folder)
        if not folder:
            self._update_status_strip("")
            return

        self.writer.open(folder)
        self.json_writer.open(folder)
        existing = self.json_writer.read_all()
        if not existing:
            existing = self.writer.read_all()
        if existing:
            self.store.bulk_load(existing)
        self._update_status_strip(folder)

    def _on_folder_changed(self, folder) -> None:
        self._switch_to_folder(folder)

    # -------- Record ingress ------------------------------------------------
    def _on_record_ready(self, record) -> None:
        self.store.append(record)
        self.writer.enqueue(record)
        self.json_writer.enqueue(record)
        self._schedule_config_backup()

    # -------- Widget actions ------------------------------------------------
    def _on_clear_clicked(self) -> None:
        self.store.clear()
        self.writer.truncate()
        self.json_writer.truncate()

    # -------- Cursor interaction -------------------------------------------
    def _on_cursor_record_selected(self, record) -> None:
        """User dragged the cursor to `record` → load it in the main window.

        Caveat: in kinetics/sync mode the main window drives per-side frames
        separately; set_img_frame_number_to() only sets a single current
        frame. For non-kinetics modes this works as expected; kinetics users
        continue to use the per-side controls."""
        cfg = self._bound_config
        if cfg is None or record is None:
            return
        rec_file = getattr(record, 'file', '') or ''
        if not rec_file:
            return
        rec_path = getattr(record, 'path', '') or ''
        folder = rec_path or cfg.get_data_folder() or ''
        if not folder:
            return
        abs_path = os.path.join(folder, rec_file)
        current = getattr(cfg, 'filename', None) or ''
        try:
            if os.path.normpath(current) != os.path.normpath(abs_path):
                cfg.load_data_image(abs_path)
            target_frame = int(getattr(record, 'frame', 0))
            if int(getattr(cfg, 'current_frame', -1)) != target_frame:
                cfg.set_img_frame_number_to(target_frame)
        except Exception:
            pass

    def _on_current_record_changed(self, *_args) -> None:
        """cfg.data_changed_signal fired — move the cursor on both tabs to
        the currently-loaded (file, frame)."""
        cfg = self._bound_config
        if cfg is None:
            return
        fname = getattr(cfg, 'filename', None) or ''
        if not fname:
            return
        base = os.path.basename(fname)
        frame = int(getattr(cfg, 'current_frame', 0))
        self.widget.temperatures_plot_widget.set_cursor_to_file_frame(base, frame)

    # -------- Plot refresh --------------------------------------------------
    def _refresh_plots(self) -> None:
        order = self.store.folder_order()
        by_file = self.store.by_file()
        by_ff = self.store.by_file_frame()

        per_file = [
            by_file[f] if f in by_file else TLogRecord(file=f)
            for f in order
        ]

        # Per-frame: every (file, frame) that was recorded, in folder order
        # then frame. Files not yet fit get a single placeholder so they
        # still show up as a dropout column.
        per_frame: list = []
        for f in order:
            frames = sorted(k[1] for k in by_ff.keys() if k[0] == f)
            if not frames:
                per_frame.append(by_file[f] if f in by_file else TLogRecord(file=f))
                continue
            for fr in frames:
                per_frame.append(by_ff[(f, fr)])

        self.widget.temperatures_plot_widget.set_records(per_file, per_frame)

        # Re-sync the cursor to the current main-window file+frame after a
        # repaint (the record list changed, so prior cursor row may no longer
        # correspond to the right file).
        self._on_current_record_changed()

    # -------- Status strip --------------------------------------------------
    def _update_status_strip(self, folder: str) -> None:
        state = self.writer.state
        n = len(self.store)
        if not folder:
            self.widget.load_data_log_file_lbl.setText("(no folder selected)")
            return
        state_str = {
            WriterState.OK: "writing OK",
            WriterState.DEGRADED: "write degraded (see console)",
            WriterState.DISABLED: "read-only (no write access)",
        }.get(state, str(state.value))
        path = self.writer.path or f"{folder}/T_log.txt"
        self.widget.load_data_log_file_lbl.setText(
            f"{path}  ·  {n} records  ·  {state_str}"
        )

    def _on_writer_state_changed_bg(self, _new_state) -> None:
        QtCore.QTimer.singleShot(0, self._refresh_status_strip_main)

    def _refresh_status_strip_main(self) -> None:
        self._update_status_strip(self.store.folder or "")

    # -------- Config backup -------------------------------------------------
    def _current_cfg_idx(self) -> int | None:
        cfg = self._bound_config
        if cfg is None:
            return None
        try:
            return self.model.configurations.index(cfg)
        except ValueError:
            return None

    def _schedule_config_backup(self) -> None:
        """Mark the current (folder, cfg) as used and arm a debounced write.

        Never auto-loads; this is write-only. Silently no-ops if there is no
        folder set yet or the current config isn't in the model (shouldn't
        happen, but keep it safe)."""
        cfg = self._bound_config
        if cfg is None:
            return
        folder = cfg.get_data_folder()
        if not folder:
            return
        idx = self._current_cfg_idx()
        if idx is None:
            return
        self._cfg_used.setdefault(folder, set()).add(idx)
        key = (folder, idx)
        if key not in self._cfg_session_date:
            self._cfg_session_date[key] = date.today().isoformat()
        timer = self._cfg_debounce.get(key)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._write_config_backup(*k))
            self._cfg_debounce[key] = timer
        timer.start(CONFIG_BACKUP_DEBOUNCE_MS)

    def _flush_all_config_backups(self) -> None:
        """Stop pending debounce timers and write any (folder, cfg) that
        was marked used. Called on folder switch and shutdown so the mirror
        is up-to-date at the two points the user cares about."""
        pending = list(self._cfg_debounce.items())
        for key, timer in pending:
            if timer.isActive():
                timer.stop()
            self._write_config_backup(*key)
        # Drop timers for folders we're leaving; cfg_used state is cheap to
        # keep around and lets a return visit skip the first debounce.
        for key, timer in pending:
            timer.deleteLater()
            self._cfg_debounce.pop(key, None)

    def _write_config_backup(self, folder: str, idx: int) -> None:
        if not folder:
            return
        try:
            cfg = self.model.configurations[idx]
        except IndexError:
            return
        if folder not in self._cfg_used or idx not in self._cfg_used[folder]:
            return
        datestr = self._cfg_session_date.get((folder, idx), date.today().isoformat())
        subdir = os.path.join(folder, CONFIG_BACKUP_DIRNAME)
        fname = f"cfg{idx + 1}_{datestr}.trs"
        final_path = os.path.join(subdir, fname)
        tmp_path = final_path + ".tmp"
        # save_setting() mutates cfg.setting_filename, _setting_working_dir,
        # and dirty as a side effect — the UI combobox later indexes into
        # the data folder expecting to find setting_filename there, so a
        # backup write must not leak these. Snapshot and restore around it.
        prev_setting_filename = cfg.setting_filename
        prev_working_dir = cfg._setting_working_dir
        prev_dirty = cfg.dirty
        t0 = time.monotonic()
        try:
            os.makedirs(subdir, exist_ok=True)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            cfg.save_setting(tmp_path)
            os.replace(tmp_path, final_path)
            dt = time.monotonic() - t0
            self._cfg_backup_writes_ok += 1
            self._cfg_backup_total_sec += dt
            if dt > self._cfg_backup_max_sec:
                self._cfg_backup_max_sec = dt
            # Log slow writes every time (not rate-limited) — they are what
            # the user is debugging. Fast writes stay silent to avoid spam.
            if dt > CONFIG_BACKUP_SLOW_SEC:
                try:
                    size_kb = os.path.getsize(final_path) / 1024.0
                except OSError:
                    size_kb = -1.0
                print(
                    f"[config_backup] SLOW write cfg{idx + 1} "
                    f"({dt*1000:.0f}ms, {size_kb:.0f}kB) → {final_path}",
                    file=sys.stderr,
                )
        except Exception as exc:
            dt = time.monotonic() - t0
            self._cfg_backup_writes_failed += 1
            # Log every failure in full — the user explicitly asked for
            # these not to be rate-limited. Full traceback-free one-liner
            # keeps stderr readable while still carrying the type + path.
            print(
                f"[config_backup] FAIL cfg{idx + 1} after {dt*1000:.0f}ms "
                f"→ {final_path}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
        finally:
            cfg.setting_filename = prev_setting_filename
            cfg._setting_working_dir = prev_working_dir
            cfg.dirty = prev_dirty

    # -------- Shutdown ------------------------------------------------------
    def shutdown(self) -> None:
        self._flush_all_config_backups()
        n_ok = self._cfg_backup_writes_ok
        n_fail = self._cfg_backup_writes_failed
        if n_ok or n_fail:
            avg_ms = (self._cfg_backup_total_sec / n_ok * 1000.0) if n_ok else 0.0
            print(
                f"[config_backup] session summary: {n_ok} ok, {n_fail} failed, "
                f"avg {avg_ms:.0f}ms, max {self._cfg_backup_max_sec*1000:.0f}ms",
                file=sys.stderr,
            )
        self.writer.close()
        self.json_writer.close()

    def show_widget(self) -> None:
        self.widget.raise_widget()
