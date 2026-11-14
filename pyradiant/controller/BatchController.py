# -*- coding: utf8 -*-
# PyRadiant - GUI program for analysis of thermal spectra during
# laser heated diamond anvil cell experiments
# Copyright (C) 2024 Ross Hrubiak (hrubiak@anl.gov)
# High Pressure Collaborative Access Team, Argonne National Laboratory
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Batch reprocessing controller: "New batch…" in the Temperature Log window
# → freeze a configuration's settings → run every frame of every file in the
# current data folder on a worker thread → show the result in its own tab.

import os
import time

from PyQt6 import QtCore, QtWidgets

from ..model.TemperatureModel import TemperatureModel
from ..model.batch import BatchResult, BatchRunner, snapshot_settings
from ..model.tlog import TLogStore
from ..widget.DataHistoryWidget import dataHistoryWidget, BatchTab, NewBatchDialog
from .TLogController import TLogController


class _Batch:
    """A running or finished batch and the tab that shows it."""

    def __init__(self, result: BatchResult, tab: BatchTab, runner: BatchRunner):
        self.result = result
        self.tab = tab
        self.runner = runner
        self.t_start = time.monotonic()
        # (file, frame) → (ds SideSpectra, us SideSpectra), for the heatmap.
        self.spectra: dict = {}


class BatchController(QtCore.QObject):
    """Owns every batch tab.

    Results stay in memory and never touch T_history.json / T_log.txt.
    Labels are shared with the live log (keyed by file, frame), so a batch
    tab shows the same labelled points. Clicking a record in a batch tab
    loads that spectrum in the main window, like the Live tab does.
    """

    def __init__(self, model: TemperatureModel, widget: dataHistoryWidget,
                 tlog_controller: TLogController):
        super().__init__()
        self.model = model
        self.widget = widget
        self.tlog = tlog_controller
        self._batches: list[_Batch] = []

        self.widget.new_batch_btn.clicked.connect(self._on_new_batch_clicked)
        self.widget.batch_tab_close_requested.connect(self._on_tab_close_requested)
        # Labels changed (or a live fit arrived) → batch tabs redraw labels.
        # Bound method: Signal holds weak references.
        self.tlog.store.records_changed.connect(self._refresh_all)

    # -------- Start -----------------------------------------------------------
    def _on_new_batch_clicked(self) -> None:
        folder = self.tlog.store.folder
        if not folder or not os.path.isdir(folder):
            QtWidgets.QMessageBox.information(
                self.widget, 'New batch',
                'Load a data file first — a batch runs over that file\'s folder.')
            return
        files = self._folder_files(folder)
        if not files:
            QtWidgets.QMessageBox.information(
                self.widget, 'New batch', f'No data files found in\n{folder}')
            return

        names = [self._config_label(i) for i in range(len(self.model.configurations))]
        dlg = NewBatchDialog(names, self.model.configuration_ind, folder,
                             len(files), self.widget)
        dlg.name_le.setPlaceholderText(self._default_name(dlg.config_index()))
        dlg.config_cb.currentIndexChanged.connect(
            lambda i: dlg.name_le.setPlaceholderText(self._default_name(i)))
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        idx = dlg.config_index()
        name = dlg.name() or self._default_name(idx)
        cfg = self.model.configurations[idx]

        try:
            settings = snapshot_settings(cfg)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self.widget, 'New batch', f'Could not snapshot the settings:\n{exc}')
            return

        result = BatchResult(name=name, folder=folder,
                             settings_label=names[idx], files=files)
        tab = self.widget.add_batch_tab(name, getattr(cfg, 'mode', 'dual'))
        runner = BatchRunner(settings, folder, files, parent=self)
        batch = _Batch(result, tab, runner)
        self._batches.append(batch)

        tab.plot.enable_heatmap().set_spectra(batch.spectra)
        tab.cancel_btn.clicked.connect(runner.cancel)
        tab.plot.cursor_record_changed.connect(self.tlog._on_cursor_record_selected)
        runner.file_done.connect(
            lambda f, recs, ds, us, b=batch: self._on_file_done(b, f, recs, ds, us))
        runner.file_failed.connect(
            lambda f, err, b=batch: self._on_file_failed(b, f, err))
        runner.progress.connect(
            lambda done, total, b=batch: self._on_progress(b, done, total))
        runner.finished_ok.connect(lambda b=batch: self._on_finished(b))

        tab.set_running(True)
        tab.set_progress(0, len(files))
        self._update_info(batch)
        runner.start()

    def _folder_files(self, folder: str) -> list[str]:
        # Same file set and order (filesystem time) as the Live tab.
        store = TLogStore()
        store.set_folder(folder)
        return store.folder_order()

    def _config_label(self, idx: int) -> str:
        cfg = self.model.configurations[idx]
        fname = getattr(cfg, 'setting_filename', None)
        label = f'Config {idx + 1}'
        if fname:
            label += f' ({os.path.basename(fname)})'
        if getattr(cfg, 'dirty', False):
            label += ' — unsaved changes'
        return label

    def _default_name(self, idx: int) -> str:
        return f'Config {idx + 1} · {time.strftime("%H:%M")}'

    # -------- Progress ------------------------------------------------------
    # Signals queued before a tab was closed can still arrive afterwards;
    # every slot ignores batches that are no longer open.
    def _on_file_done(self, batch: _Batch, file, records, ds, us) -> None:
        if batch not in self._batches:
            return
        r = batch.result
        r.records.extend(records)
        r.ds_spectra.extend(ds)
        r.us_spectra.extend(us)
        for rec, d, u in zip(records, ds, us):
            batch.spectra[(rec.file, int(rec.frame))] = (d, u)
        self._refresh(batch)

    def _on_file_failed(self, batch: _Batch, file, err) -> None:
        if batch not in self._batches:
            return
        batch.result.failed[file] = err
        self._update_info(batch)

    def _on_progress(self, batch: _Batch, done, total) -> None:
        if batch not in self._batches:
            return
        batch.result.files_done = done
        batch.tab.set_progress(done, total)
        self._update_info(batch)

    def _on_finished(self, batch: _Batch) -> None:
        if batch not in self._batches:
            return
        batch.result.finished = True
        batch.result.cancelled = batch.runner.is_cancelled()
        batch.tab.set_running(False)
        self._update_info(batch)

    def _update_info(self, batch: _Batch) -> None:
        r = batch.result
        parts = [r.settings_label, f'{len(r.records)} records']
        if r.finished:
            state = 'cancelled' if r.cancelled else 'done'
            parts.append(f'{state} in {time.monotonic() - batch.t_start:.1f} s')
        if r.failed:
            parts.append(f'{len(r.failed)} file(s) failed')
        batch.tab.set_info('  ·  '.join(parts))
        if r.failed:
            batch.tab.info_lbl.setToolTip(
                '\n'.join(f'{f}: {e}' for f, e in list(r.failed.items())[:30]))

    # -------- Plot ----------------------------------------------------------
    def _refresh(self, batch: _Batch) -> None:
        records = batch.result.records
        # Per file: the first frame of each file; per frame: everything.
        per_file, seen = [], set()
        for rec in records:
            if rec.file not in seen:
                seen.add(rec.file)
                per_file.append(rec)
        batch.tab.plot.set_records(per_file, records, labels=self.tlog.store.labels())

    def _refresh_all(self) -> None:
        for batch in self._batches:
            if batch.result.records:
                self._refresh(batch)

    # -------- Close / shutdown ---------------------------------------------
    def _on_tab_close_requested(self, tab) -> None:
        for batch in list(self._batches):
            if batch.tab is tab:
                self._stop(batch)
                self._batches.remove(batch)
                self.widget.remove_batch_tab(tab)
                return

    def _stop(self, batch: _Batch) -> None:
        if batch.runner.isRunning():
            batch.runner.cancel()
            batch.runner.wait(5000)

    def shutdown(self) -> None:
        for batch in self._batches:
            self._stop(batch)
