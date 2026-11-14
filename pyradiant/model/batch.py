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
# Batch reprocessing: run every frame of every data file in a folder through
# the temperature pipeline with one frozen set of settings.
#
# Design summary:
#   snapshot_settings(cfg) — freeze a live configuration's settings (a .trs
#       written via save_setting_copy, plus the fit settings .trs files
#       don't store), on the main thread.
#   BatchRunner — QThread. Rebuilds a private configuration from the
#       snapshot and walks the files. The private configuration has no
#       listeners and never touches the live session, T_history.json or
#       T_log.txt.
#   BatchResult — in-memory results: one TLogRecord per frame plus the
#       DS/US spectra (raw, calibrated, fit) that produced it.

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from PyQt6 import QtCore

from .TemperatureModelConfiguration import TemperatureModelConfiguration
from .temperature_pipeline import pipeline, Stage
from .tlog import TLogRecord


@dataclass
class SideSpectra:
    """Spectra for one side of one record. Arrays are copies, so later
    pipeline runs can't change them. Empty arrays when the side has no data
    (single-sided mode, blank kinetics side, failed extraction)."""
    x: np.ndarray
    raw: np.ndarray          # ROI sum after background subtraction
    corrected: np.ndarray    # calibrated radiance — what the fit used
    fit_x: np.ndarray
    fit: np.ndarray


@dataclass
class BatchResult:
    name: str
    folder: str
    settings_label: str              # e.g. "Config 2 (Pt-run3.trs)"
    created_local: datetime = field(default_factory=datetime.now)
    files: list[str] = field(default_factory=list)       # planned, in order
    records: list[TLogRecord] = field(default_factory=list)
    ds_spectra: list[SideSpectra] = field(default_factory=list)  # parallel to records
    us_spectra: list[SideSpectra] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # file → error
    files_done: int = 0
    finished: bool = False
    cancelled: bool = False


@dataclass
class BatchSettings:
    """Frozen settings for a batch. `trs` is the saved settings file; the
    other fields are fit settings that .trs files don't store yet, so they
    are captured and re-applied separately."""
    trs: bytes
    fit_function: str            # 'plank' | 'wien'
    filters: dict                # side → (filter_oscillation, freq_min, freq_max)

    def apply_extras(self, cfg: TemperatureModelConfiguration) -> None:
        cfg.temperature_fit_function_str = self.fit_function
        for side, model in (('ds', cfg.ds_temperature_model),
                            ('us', cfg.us_temperature_model)):
            model.set_temperature_fit_function(self.fit_function)
            on, fmin, fmax = self.filters[side]
            model.filter_oscillation = on
            model.filter_freq_min = fmin
            model.filter_freq_max = fmax


def snapshot_settings(cfg: TemperatureModelConfiguration) -> BatchSettings:
    """Freeze `cfg`'s settings. Main thread only — reads live state."""
    fd, path = tempfile.mkstemp(prefix='pyradiant_batch_', suffix='.trs')
    os.close(fd)
    try:
        cfg.save_setting_copy(path)
        with open(path, 'rb') as f:
            trs = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    # Read the side models directly (cfg.us_filter_oscillation returns the
    # DS value).
    filters = {
        side: (bool(m.filter_oscillation), float(m.filter_freq_min), float(m.filter_freq_max))
        for side, m in (('ds', cfg.ds_temperature_model), ('us', cfg.us_temperature_model))
    }
    return BatchSettings(trs=trs, fit_function=cfg.temperature_fit_function_str,
                         filters=filters)


class _BatchConfiguration(TemperatureModelConfiguration):
    """A configuration that never announces anything. The base class
    schedules T-log emits with QTimer, which needs the main thread's event
    loop; a batch has no listeners anyway."""

    def data_changed_emit(self, frame):
        pass

    def ds_calculations_changed_emit(self):
        pass

    def us_calculations_changed_emit(self):
        pass


def _side_spectra(model) -> SideSpectra:
    def _xy(spec):
        try:
            x, y = spec.data
            return np.array(x, dtype=float), np.array(y, dtype=float)
        except Exception:
            return np.array([]), np.array([])

    x, raw = _xy(model.data_spectrum)
    cx, corrected = _xy(model.corrected_spectrum)
    if cx.size != x.size:
        corrected = np.full(x.size, np.nan)
    fit_x, fit = _xy(model.fit_spectrum)
    return SideSpectra(x=x, raw=raw, corrected=corrected, fit_x=fit_x, fit=fit)


class BatchRunner(QtCore.QThread):
    """Run `files` through the pipeline with frozen settings.

    Signals fire from the worker thread; connect with the default
    AutoConnection so slots run on the main thread.
      file_done(str file, list records, list ds_spectra, list us_spectra)
      file_failed(str file, str error)
      progress(int files_done, int files_total)
      finished_ok()  — also emitted after cancel; check is_cancelled()
    """

    file_done = QtCore.pyqtSignal(str, list, list, list)
    file_failed = QtCore.pyqtSignal(str, str)
    progress = QtCore.pyqtSignal(int, int)
    finished_ok = QtCore.pyqtSignal()

    def __init__(self, settings: BatchSettings, folder: str, files: list[str], parent=None):
        super().__init__(parent)
        self._settings = settings
        self._folder = folder
        self._files = list(files)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        # numpy's error state is per thread. The model modules set
        # np.seterr(all='ignore') at import (main thread) alongside
        # warnings.simplefilter('error'); without the same setting here a
        # harmless log(0) on an empty frame would raise and fail the file.
        np.seterr(all='ignore')
        fd, path = tempfile.mkstemp(prefix='pyradiant_batch_', suffix='.trs')
        os.close(fd)
        try:
            with open(path, 'wb') as f:
                f.write(self._settings.trs)
            cfg = _BatchConfiguration()
            cfg.load_setting(path)
            self._settings.apply_extras(cfg)
        except Exception as exc:
            for name in self._files:
                self.file_failed.emit(name, f'settings: {exc}')
            self.finished_ok.emit()
            return
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        total = len(self._files)
        for i, name in enumerate(self._files):
            if self._cancel:
                break
            try:
                records, ds, us = self._run_file(cfg, os.path.join(self._folder, name))
                self.file_done.emit(name, records, ds, us)
            except Exception as exc:
                msg = f'{type(exc).__name__}: {exc}'
                print(f'[batch] {name}: {msg}', file=sys.stderr)
                self.file_failed.emit(name, msg)
            self.progress.emit(i + 1, total)
        self.finished_ok.emit()

    def _run_file(self, cfg, path):
        """Every record the T log would show for this file, in frame order."""
        records, ds, us = [], [], []

        def snap():
            records.append(TLogRecord.from_configuration(cfg, cfg.current_frame))
            ds.append(_side_spectra(cfg.ds_temperature_model))
            us.append(_side_spectra(cfg.us_temperature_model))

        cfg.current_frame = 0
        pipeline.run(cfg, Stage.LOAD, path)
        kinetics = cfg.kinetics_mode in ('kinetics-interleaved', 'kinetics')
        k_range = cfg.get_coincident_frame_range() if kinetics else None
        if k_range:
            # Kinetics: DS and US read different readout frames for the same
            # exposure — step through exposures, as the live session does.
            for k in range(k_range[0], k_range[1] + 1):
                if self._cancel:
                    break
                f_ds, f_us = cfg.coincident_to_readout(k)
                if f_ds is None and f_us is None:
                    continue
                cfg.set_img_frame_numbers(f_ds, f_us)
                snap()
        else:
            snap()
            for frame in range(1, int(cfg.data_img_file.num_frames)):
                if self._cancel:
                    break
                cfg.set_img_frame_number_to(frame)
                snap()
        return records, ds, us
