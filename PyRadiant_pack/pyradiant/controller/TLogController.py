# -*- coding: utf8 -*-
# PyRadiant - GUI program for analysis of thermal spectra during
# laser heated diamond anvil cell experiments
# Copyright (C) 2024 Ross Hrubiak (hrubiak@anl.gov)
# High Pressure Collaborative Access Team, Argonne National Laboratory
#
# T-log v2 controller — replaces DataLogController.

import numpy as np
from PyQt6 import QtCore

from ..model.TemperatureModel import TemperatureModel
from ..model.TemperatureModelConfiguration import TemperatureModelConfiguration
from ..model.tlog import TLogStore, TLogWriter, WriterState
from ..widget.DataHistoryWidget import dataHistoryWidget


# Display gate — records outside this range plot as NaN (gap in the trace).
# Matches the legacy DataLogController limits.
MIN_TEMPERATURE = 100
MAX_TEMPERATURE = 12000


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

        self._bound_config: TemperatureModelConfiguration | None = None

        # Static plot titles — matches legacy behaviour so the header labels
        # stay populated even before the first record arrives.
        self.widget.setWindowTitle('Temperature Log')
        self.widget.temperatures_plot_widget.update_time_lapse_ds_temperature_txt('Downstream')
        self.widget.temperatures_plot_widget.update_time_lapse_us_temperature_txt('Upstream')

        # Widget → controller
        self.widget.clear_data_log_file_btn.clicked.connect(self._on_clear_clicked)

        # Store → widget
        self.store.records_changed.connect(self._refresh_plots)

        # Writer → widget status strip (fires from the drain thread; route
        # through a queued slot so we touch Qt on the main thread only).
        self.writer.state_changed.connect(self._on_writer_state_changed_bg)

        # Model → controller (configuration switching)
        self.model.configuration_selected.connect(self._on_configuration_selected)

        # Bind the initial configuration NOW so any folder already set on
        # the config (from a restored settings file) is honored immediately.
        self._bind(self.model.current_configuration)

    # -------- Configuration binding -----------------------------------------
    def _bind(self, cfg: TemperatureModelConfiguration) -> None:
        if self._bound_config is cfg:
            return
        if self._bound_config is not None:
            try:
                self._bound_config.data_folder_changed.disconnect(self._on_folder_changed)
            except (ValueError, RuntimeError):
                pass
            try:
                self._bound_config.tlog_record_ready.disconnect(self._on_record_ready)
            except (ValueError, RuntimeError):
                pass
        self._bound_config = cfg
        cfg.data_folder_changed.connect(self._on_folder_changed)
        cfg.tlog_record_ready.connect(self._on_record_ready)
        # Sync to whichever folder this config already thinks it's on.
        self._switch_to_folder(cfg.get_data_folder())

    def _on_configuration_selected(self, _ind) -> None:
        self._bind(self.model.current_configuration)

    # -------- Folder switching ----------------------------------------------
    def _switch_to_folder(self, folder) -> None:
        """The one and only folder-switch path. Any caller that changes the
        viewed folder must reach us via config.data_folder_changed — never
        by touching store/writer directly."""
        self.writer.close()
        self.store.clear()
        self.store.set_folder(folder)
        if not folder:
            self._update_status_strip("")
            return

        self.writer.open(folder)
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

    # -------- Widget actions ------------------------------------------------
    def _on_clear_clicked(self) -> None:
        self.store.clear()
        self.writer.truncate()

    # -------- Plot refresh --------------------------------------------------
    def _refresh_plots(self) -> None:
        """Repaint both tabs from the store. Called on any store mutation."""
        latest = self.store.latest()

        # "Latest" tab — indexed 0..N-1 in append order.
        n = len(latest)
        if n:
            x = np.arange(n)
            ds = np.array([self._gate(r.T_DS) for r in latest])
            us = np.array([self._gate(r.T_US) for r in latest])
        else:
            x = np.array([])
            ds = np.array([])
            us = np.array([])

        lw = self.widget.temperatures_plot_widget
        lw.plot_ds_time_lapse(x, ds)
        lw.plot_us_time_lapse(x, us)
        if n:
            lw.cursor_item.setPos(n - 1)

        # "Total" tab — one point per file, ordered by first-seen time.
        by_file = self.store.by_file()
        if by_file:
            ordered = sorted(by_file.values(), key=lambda r: r.timestamp)
            m = len(ordered)
            xs = np.arange(m)
            ds_s = np.array([self._gate(r.T_DS) for r in ordered])
            us_s = np.array([self._gate(r.T_US) for r in ordered])
            sw = self.widget.static_temperature_plot_widget
            sw.plot_ds_time_lapse(xs, ds_s)
            sw.plot_us_time_lapse(xs, us_s)
            # Cursor on the most recently appended file, if we can find it.
            if latest:
                current_file = latest[-1].file
                for i, r in enumerate(ordered):
                    if r.file == current_file:
                        sw.cursor_item.setPos(i)
                        break
        else:
            sw = self.widget.static_temperature_plot_widget
            sw.plot_ds_time_lapse(np.array([]), np.array([]))
            sw.plot_us_time_lapse(np.array([]), np.array([]))

    @staticmethod
    def _gate(t: float) -> float:
        """Return t if inside the plottable range, else NaN so PyQtGraph
        draws a gap instead of a spike to zero."""
        if t is None:
            return float('nan')
        try:
            tv = float(t)
        except (TypeError, ValueError):
            return float('nan')
        if tv != tv:  # NaN check without importing math
            return float('nan')
        if tv <= MIN_TEMPERATURE or tv >= MAX_TEMPERATURE:
            return float('nan')
        return tv

    # -------- Status strip --------------------------------------------------
    def _update_status_strip(self, folder: str) -> None:
        """Update the small status label above the plot tabs.

        Shows: '<folder>/T_log.txt  ·  N records  ·  <state>'
        or '(no folder selected)' if disabled with no folder.
        """
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
        """Fires on the drain thread. Bounce to the Qt main thread before
        touching widgets."""
        QtCore.QTimer.singleShot(0, self._refresh_status_strip_main)

    def _refresh_status_strip_main(self) -> None:
        self._update_status_strip(self.store.folder or "")

    # -------- Shutdown ------------------------------------------------------
    def shutdown(self) -> None:
        """Called from MainController.closeEvent — flush and close the
        writer thread before the QApplication quits."""
        self.writer.close()

    def show_widget(self) -> None:
        """Legacy API — the T Log button raises the window."""
        self.widget.raise_widget()
