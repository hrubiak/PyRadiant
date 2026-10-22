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

import os
from datetime import datetime

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
import pyqtgraph as pg
import numpy as np

from .CustomWidgets import HorizontalSpacerItem

from .. import resources_path


colors = {
    'data_pen': '#FFFFFF',
    'data_brush': '#FFFFFF',
    'fit_pen': '#C90048',
    'downstream': '#FFD700',
    'upstream': '#FF6F61',
    'combined': '#66FFFF'
}

export_colors = {
    'downstream': '#235CDB',
    'combined': '#DE5757',
}


class _FlexAxisItem(pg.DateAxisItem):
    """Bottom axis that toggles between date/time and plain-integer formatting.

    In Index mode the axis still inherits DateAxisItem's drawing but overrides
    tick labels and spacing so a 0..N-1 integer axis looks right.
    """

    _use_date: bool = False  # start in Index mode to match the historical default

    def set_use_date(self, enabled: bool) -> None:
        if self._use_date == enabled:
            return
        self._use_date = enabled
        self.picture = None  # invalidate cached tick drawing
        self.update()

    def tickStrings(self, values, scale, spacing):
        if self._use_date:
            return super().tickStrings(values, scale, spacing)
        return [str(int(v)) for v in values]

    def tickSpacing(self, minVal, maxVal, size):
        if self._use_date:
            return super().tickSpacing(minVal, maxVal, size)
        return pg.AxisItem.tickSpacing(self, minVal, maxVal, size)


def _dt_to_epoch(dt) -> float:
    """Naive-local datetime → unix epoch. NaN on bad input."""
    if dt is None:
        return float('nan')
    try:
        return float(dt.timestamp())
    except (ValueError, OverflowError, TypeError, AttributeError):
        return float('nan')


class historyPlotWidget(QtWidgets.QWidget):
    """One tab of the Temperature Log window.

    Owns the plot + the control bar(s) above it. The controller feeds it a
    list of TLogRecord via set_records(); everything else (compress, time
    axis, cursor snap, Y cutoff) is handled here.

    Emits cursor_record_changed(TLogRecord) when the user drags the cursor
    to a new record. Programmatic cursor moves (set_cursor_to_file_frame)
    do not emit.
    """

    cursor_record_changed = pyqtSignal(object)

    def __init__(self, scale_label: str, tab_key: str = "default", *args, **kwargs):
        super().__init__()
        self._scale_label = scale_label
        self._tab_key = tab_key  # key under which cutoffs persist

        # Both groupings are kept in parallel. Per-file is 1 row/file;
        # per-frame is 1 row per (file, frame). Toggle picks which goes to
        # the renderer. _records/_timestamps/_ds/_us always reflect the
        # ACTIVE grouping (set by _apply_grouping).
        self._records_per_file: list = []
        self._records_per_frame: list = []
        self._records: list = []
        self._timestamps = np.array([], dtype=float)  # unix epoch; NaN if unknown
        self._ds = np.array([], dtype=float)
        self._us = np.array([], dtype=float)
        self._snap_xs = np.array([], dtype=float)
        self._snap_rows = np.array([], dtype=int)
        self._cursor_row: int = -1
        self._mode = 'dual'
        self._last_view_mode: tuple | None = None
        # Dashed vertical items delimiting file boundaries in per-frame mode.
        self._boundary_items: list = []

        # Burst-coalescing redraw. In kinetics-interleaved every strip emits
        # a tlog record → store.records_changed → set_records → _redraw.
        # A 32-strip file would redraw 32 times inside the same user action;
        # with the debouncer we draw once at the end of the burst instead.
        # 50ms is below perceivable latency for the user but comfortably
        # above a Qt event-loop tick, so it collapses a strip burst cleanly.
        self._redraw_timer = QtCore.QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(50)
        self._redraw_timer.timeout.connect(self._redraw)

        self._build_ui()
        self._wire_signals()
        self._load_cutoffs_from_qsettings()
        self._redraw()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        # ── Control bar ──────────────────────────────────────────────
        ctrl = QtWidgets.QWidget()
        ctrl_l = QtWidgets.QHBoxLayout(ctrl)
        ctrl_l.setContentsMargins(0, 0, 0, 0)
        ctrl_l.setSpacing(6)

        ctrl_l.addWidget(QtWidgets.QLabel('X:'))
        self._grp_x = QtWidgets.QButtonGroup(self)
        self._rb_index = QtWidgets.QRadioButton('Index'); self._rb_index.setChecked(True)
        self._rb_time = QtWidgets.QRadioButton('Time')
        for rb in (self._rb_index, self._rb_time):
            self._grp_x.addButton(rb)
            ctrl_l.addWidget(rb)

        # Grouping (hidden when every file is single-frame — nothing to pick).
        self._group_box = QtWidgets.QWidget()
        group_l = QtWidgets.QHBoxLayout(self._group_box)
        group_l.setContentsMargins(0, 0, 0, 0)
        group_l.setSpacing(6)
        group_l.addSpacing(6)
        group_l.addWidget(QtWidgets.QLabel('Group:'))
        self._grp_group = QtWidgets.QButtonGroup(self)
        self._rb_per_file = QtWidgets.QRadioButton('Per file'); self._rb_per_file.setChecked(True)
        self._rb_per_frame = QtWidgets.QRadioButton('Per frame')
        for rb in (self._rb_per_file, self._rb_per_frame):
            self._grp_group.addButton(rb)
            group_l.addWidget(rb)
        ctrl_l.addSpacing(12)
        ctrl_l.addWidget(self._group_box)
        self._group_box.setVisible(False)

        ctrl_l.addSpacing(12)
        ctrl_l.addWidget(QtWidgets.QLabel('Gaps:'))
        self._grp_gaps = QtWidgets.QButtonGroup(self)
        self._rb_keep = QtWidgets.QRadioButton('Keep'); self._rb_keep.setChecked(True)
        self._rb_compress = QtWidgets.QRadioButton('Compress')
        for rb in (self._rb_keep, self._rb_compress):
            self._grp_gaps.addButton(rb)
            ctrl_l.addWidget(rb)

        ctrl_l.addSpacing(12)
        ctrl_l.addWidget(QtWidgets.QLabel('Mouse:'))
        self._grp_mouse = QtWidgets.QButtonGroup(self)
        self._rb_zoom = QtWidgets.QRadioButton('Zoom'); self._rb_zoom.setChecked(True)
        self._rb_pan = QtWidgets.QRadioButton('Pan')
        for rb in (self._rb_zoom, self._rb_pan):
            self._grp_mouse.addButton(rb)
            ctrl_l.addWidget(rb)

        ctrl_l.addStretch()

        self._btn_prev = QtWidgets.QPushButton('◀')
        self._btn_next = QtWidgets.QPushButton('▶')
        self._btn_prev.setToolTip('Previous valid record')
        self._btn_next.setToolTip('Next valid record')
        self._btn_prev.setMaximumWidth(32)
        self._btn_next.setMaximumWidth(32)
        ctrl_l.addWidget(self._btn_prev)
        ctrl_l.addWidget(self._btn_next)

        root.addWidget(ctrl)

        # ── Cutoff bar ───────────────────────────────────────────────
        cutoff = QtWidgets.QWidget()
        cutoff_l = QtWidgets.QHBoxLayout(cutoff)
        cutoff_l.setContentsMargins(0, 0, 0, 0)
        cutoff_l.setSpacing(6)
        cutoff_l.addWidget(QtWidgets.QLabel('Cutoff:'))
        cutoff_l.addWidget(QtWidgets.QLabel('Min'))
        self._le_ymin = QtWidgets.QLineEdit()
        self._le_ymin.setPlaceholderText('−∞')
        self._le_ymin.setFixedWidth(80)
        cutoff_l.addWidget(self._le_ymin)
        cutoff_l.addWidget(QtWidgets.QLabel('Max'))
        self._le_ymax = QtWidgets.QLineEdit()
        self._le_ymax.setPlaceholderText('+∞')
        self._le_ymax.setFixedWidth(80)
        cutoff_l.addWidget(self._le_ymax)
        cutoff_l.addStretch()
        root.addWidget(cutoff)

        # ── Plot (same visual as before: top+right axes, header labels) ──
        self._plot_container = pg.GraphicsLayoutWidget()
        root.addWidget(self._plot_container, 1)

        self._pg_layout = pg.GraphicsLayout()
        self._pg_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_layout.layout.setVerticalSpacing(0)

        self._flex_axis = _FlexAxisItem(orientation='bottom')
        self._time_lapse_plot = pg.PlotItem(axisItems={'bottom': self._flex_axis})
        self._time_lapse_plot.showAxis('top', show=True)
        self._time_lapse_plot.showAxis('right', show=True)
        self._time_lapse_plot.getAxis('top').setStyle(showValues=False)
        self._time_lapse_plot.getAxis('right').setStyle(showValues=False)
        self._time_lapse_plot.getAxis('bottom').setStyle(showValues=True)
        self._time_lapse_plot.setLabel('left', self._scale_label)
        self._time_lapse_plot.showGrid(x=True, y=True, alpha=0.25)
        vb = self._time_lapse_plot.getViewBox()
        vb.setMouseMode(pg.ViewBox.RectMode)
        vb.setMenuEnabled(False)
        self._time_lapse_plot.setMenuEnabled(False)
        _orig_vb_click = vb.mouseClickEvent

        def _vb_click(ev, _orig=_orig_vb_click, _vb=vb):
            if ev.button() == QtCore.Qt.MouseButton.RightButton:
                _vb.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
                ev.accept()
                return
            _orig(ev)

        vb.mouseClickEvent = _vb_click

        self._pg_time_lapse_layout = pg.GraphicsLayout()
        self._pg_time_lapse_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_time_lapse_layout.setSpacing(0)

        self._time_lapse_ds_temperature_txt = pg.LabelItem()
        self._time_lapse_us_temperature_txt = pg.LabelItem()
        self._time_lapse_combined_temperature_txt = pg.LabelItem()
        self._pg_time_lapse_layout.addItem(self._time_lapse_ds_temperature_txt, 0, 0)
        self._pg_time_lapse_layout.addItem(self._time_lapse_combined_temperature_txt, 0, 1)
        self._pg_time_lapse_layout.addItem(self._time_lapse_us_temperature_txt, 0, 2)
        self._pg_time_lapse_layout.addItem(self._time_lapse_plot, 1, 0, 1, 3)

        self._pg_layout.addItem(self._pg_time_lapse_layout)
        self._plot_container.addItem(self._pg_layout)

        # Plot data items
        self._ds_item = pg.PlotDataItem(
            pen=pg.mkPen(QColor(colors['downstream']), width=1),
            symbolPen=pg.mkPen(QColor(colors['downstream']), width=1),
            symbolBrush=pg.mkBrush(QColor(colors['downstream'])),
            symbolSize=5, symbol='s',
        )
        self._us_item = pg.PlotDataItem(
            pen=pg.mkPen(QColor(colors['upstream']), width=1),
            symbolPen=pg.mkPen(QColor(colors['upstream']), width=1),
            symbolBrush=pg.mkBrush(QColor(colors['upstream'])),
            symbolSize=5, symbol='s',
        )
        self._time_lapse_plot.addItem(self._ds_item)
        self._time_lapse_plot.addItem(self._us_item)

        # Interactive cursor
        self._cursor_line = pg.InfiniteLine(
            angle=90, movable=True,
            pen=pg.mkPen(QColor(colors['fit_pen']), width=2),
            hoverPen=pg.mkPen('w', width=2),
        )
        self._cursor_line.setVisible(False)
        self._time_lapse_plot.addItem(self._cursor_line, ignoreBounds=True)

        # Hover overlay
        self._cursor_text = pg.TextItem(anchor=(0, 0))
        self._cursor_text.setZValue(20)
        self._cursor_text.setVisible(False)
        self._time_lapse_plot.addItem(self._cursor_text, ignoreBounds=True)

    def _wire_signals(self) -> None:
        for rb in (self._rb_time, self._rb_index, self._rb_keep, self._rb_compress):
            rb.clicked.connect(self._redraw)
        for rb in (self._rb_per_file, self._rb_per_frame):
            rb.clicked.connect(self._on_grouping_toggled)
        self._rb_zoom.clicked.connect(self._on_mouse_mode_toggled)
        self._rb_pan.clicked.connect(self._on_mouse_mode_toggled)
        self._le_ymin.editingFinished.connect(self._on_cutoff_edited)
        self._le_ymax.editingFinished.connect(self._on_cutoff_edited)
        self._cursor_line.sigPositionChanged.connect(self._on_cursor_moved)
        self._cursor_line.sigPositionChangeFinished.connect(self._on_cursor_released)
        self._time_lapse_plot.getViewBox().sigRangeChanged.connect(self._reposition_cursor_text)
        self._btn_prev.clicked.connect(self._on_prev_clicked)
        self._btn_next.clicked.connect(self._on_next_clicked)

    # -------- Legacy header-label API (used by the controller) --------------
    def update_time_lapse_ds_temperature_txt(self, txt):
        self._time_lapse_ds_temperature_txt.setText(
            txt, size='16pt', color=colors['downstream'], justify='left')

    def update_time_lapse_us_temperature_txt(self, txt):
        if self._mode == 'single':
            self._time_lapse_us_temperature_txt.setText('', size='16pt')
            return
        self._time_lapse_us_temperature_txt.setText(
            txt, size='16pt', color=colors['upstream'], justify='right')

    def set_mode(self, mode: str) -> None:
        """'dual' | 'single'. In single mode the US series stays blank."""
        self._mode = mode
        if mode == 'single':
            self._time_lapse_us_temperature_txt.setText('', size='16pt')
        self._redraw()

    # -------- New primary API -----------------------------------------------
    def set_records(self, per_file: list, per_frame: list | None = None) -> None:
        """Push new record lists. `per_file` is required (one record per file,
        legacy single-list callers still work); `per_frame` is optional and
        enables the Per-frame grouping. Repaints according to current controls."""
        self._records_per_file = list(per_file)
        self._records_per_frame = list(per_frame) if per_frame is not None else list(per_file)
        # Only expose the grouping toggle when per-frame actually differs from
        # per-file (i.e. at least one file has >1 fit frame). Otherwise the
        # choice is a no-op and just clutters the control bar.
        multi_frame = len(self._records_per_frame) > len(self._records_per_file)
        self._group_box.setVisible(multi_frame)
        if not multi_frame and self._rb_per_frame.isChecked():
            self._rb_per_file.setChecked(True)
        self._apply_grouping()

    def _on_grouping_toggled(self) -> None:
        # Grouping change invalidates the x-coordinate system, so force the
        # mode-change branch in _redraw to re-fit the view.
        self._last_view_mode = None
        self._apply_grouping()

    def _apply_grouping(self) -> None:
        """Pick the active record list based on the Per file/Per frame toggle
        and recompute cached arrays, then redraw."""
        if self._rb_per_frame.isChecked():
            self._records = self._records_per_frame
        else:
            self._records = self._records_per_file
        self._timestamps = np.array(
            [_dt_to_epoch(getattr(r, 'exposure_start_local', None)
                          or getattr(r, 'timestamp', None))
             for r in self._records],
            dtype=float,
        )
        self._ds = np.array(
            [self._nan_safe(getattr(r, 'T_DS', float('nan'))) for r in self._records],
            dtype=float,
        )
        if self._mode == 'single':
            self._us = np.full(len(self._records), float('nan'))
        else:
            self._us = np.array(
                [self._nan_safe(getattr(r, 'T_US', float('nan'))) for r in self._records],
                dtype=float,
            )
        # Debounced — see _redraw_timer in __init__. Burst of records from a
        # kinetics file collapses into one repaint instead of 32.
        self._redraw_timer.start()

    def set_cursor_to_file_frame(self, file: str, frame: int) -> None:
        """Programmatic cursor move. No emit.

        Prefers an exact (file, frame) match; falls back to any record with
        the matching file; otherwise does nothing.
        """
        if not self._records or len(self._snap_rows) == 0:
            return
        exact = -1
        file_only = -1
        for i, r in enumerate(self._records):
            if getattr(r, 'file', None) == file:
                if file_only < 0:
                    file_only = i
                if int(getattr(r, 'frame', -1)) == int(frame):
                    exact = i
                    break
        target = exact if exact >= 0 else file_only
        if target < 0:
            return
        # snap_rows is a subset of [0..n-1]; pick the nearest snap position
        idx = int(np.argmin(np.abs(self._snap_rows - target)))
        self._cursor_row = int(self._snap_rows[idx])
        self._cursor_line.blockSignals(True)
        try:
            self._cursor_line.setValue(float(self._snap_xs[idx]))
        finally:
            self._cursor_line.blockSignals(False)
        self._update_cursor_label()
        self._update_nav_buttons()

    # -------- Redraw --------------------------------------------------------
    def _redraw(self) -> None:
        n = len(self._records)
        use_time = self._rb_time.isChecked()
        compress = self._rb_compress.isChecked()

        # Compress-out is index-mode only; disable it in time mode.
        for rb in (self._rb_keep, self._rb_compress):
            rb.setEnabled(not use_time)

        self._flex_axis.set_use_date(use_time)
        self._time_lapse_plot.setLabel('bottom', 'Time' if use_time else 'Record index')

        y_lo, y_hi = self._cutoffs()

        ds_full = self._ds
        us_full = self._us if self._mode != 'single' else np.array([])

        # Treat T ≤ 0 as missing — unphysical for an incandescent sample, and
        # the legacy TSV log round-trips NaN as "0" (see _nan_safe_int_str),
        # so failed fits loaded from T_log.txt arrive here as zeros.
        finite_ds = np.isfinite(ds_full) & (ds_full > 0) & (ds_full >= y_lo) & (ds_full <= y_hi) if n else np.array([], dtype=bool)
        if self._mode == 'single' or us_full.size == 0:
            finite_us = np.zeros(n, dtype=bool)
        else:
            finite_us = np.isfinite(us_full) & (us_full > 0) & (us_full >= y_lo) & (us_full <= y_hi)

        any_finite = finite_ds | finite_us if n else np.array([], dtype=bool)
        valid_ts = np.isfinite(self._timestamps) if n else np.array([], dtype=bool)
        compressed_idx = None  # populated only in index+compress branch below

        if use_time:
            ds_mask = valid_ts & finite_ds
            us_mask = valid_ts & finite_us
            ds_x, ds_y = self._timestamps[ds_mask], ds_full[ds_mask]
            us_x = self._timestamps[us_mask]
            us_y = us_full[us_mask] if us_full.size else np.array([])
        elif compress:
            compressed_idx = np.full(n, -1, dtype=int)
            if n:
                compressed_idx[any_finite] = np.arange(int(any_finite.sum()))
            ds_x = compressed_idx[finite_ds].astype(float)
            ds_y = ds_full[finite_ds]
            us_x = compressed_idx[finite_us].astype(float)
            us_y = us_full[finite_us] if us_full.size else np.array([])
        else:
            # Keep mode: plot every row. NaN/missing are shown as y=0 so
            # unfit/failed rows are visible as drops to zero.
            x_all = np.arange(n, dtype=float) if n else np.array([])
            if n:
                ds_y = np.where(finite_ds, ds_full, 0.0)
                ds_x = x_all
                if us_full.size:
                    us_y = np.where(finite_us, us_full, 0.0)
                    us_x = x_all
                else:
                    us_x = np.array([])
                    us_y = np.array([])
            else:
                ds_x = ds_y = us_x = us_y = np.array([])

        self._ds_item.setData(ds_x, ds_y)
        self._us_item.setData(us_x, us_y)

        # File-boundary dashed verticals — only meaningful in per-frame mode.
        self._update_file_boundaries(
            n=n, use_time=use_time, compress=compress,
            any_finite=any_finite, valid_ts=valid_ts,
            compressed_idx=compressed_idx,
        )

        # Reset the view whenever the x-coordinate system changes (index↔time,
        # keep↔compress) or we transition empty→populated. Otherwise the view
        # box keeps old-scale ranges and the new data lands off-screen — most
        # visible on Index→Time where indices (0..n) and unix epoch (~1.7e9)
        # share no overlap. User zoom in the same mode is preserved.
        view_mode = ('time' if use_time else ('compress' if compress else 'keep'))
        last_mode, last_had_data = self._last_view_mode or (None, False)
        if n and (view_mode != last_mode or not last_had_data):
            if not use_time and not compress:
                self._time_lapse_plot.setXRange(0, n - 1, padding=0.05)
            else:
                self._time_lapse_plot.getViewBox().autoRange()
        self._last_view_mode = (view_mode, n > 0)

        # Snap data: which record rows are selectable, and at what x.
        if n == 0:
            self._snap_xs = np.array([], dtype=float)
            self._snap_rows = np.array([], dtype=int)
        elif use_time:
            snap_mask = valid_ts & any_finite
            self._snap_rows = np.where(snap_mask)[0]
            self._snap_xs = self._timestamps[snap_mask]
        elif compress:
            rows = np.where(any_finite)[0]
            self._snap_rows = rows
            self._snap_xs = compressed_idx[rows].astype(float)
        else:
            # Keep-position index mode: let the cursor land anywhere (even on
            # NaN placeholders) so dragging past unfit files still works.
            self._snap_rows = np.arange(n, dtype=int)
            self._snap_xs = np.arange(n, dtype=float)

        has_data = len(self._snap_xs) > 0
        self._cursor_line.setVisible(has_data)
        self._cursor_text.setVisible(has_data)
        if not has_data:
            self._cursor_row = -1
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
            return

        # Snap cursor to nearest valid position, preserving the target row
        # if possible.
        target_row = self._cursor_row if self._cursor_row >= 0 else int(self._snap_rows[-1])
        idx = int(np.argmin(np.abs(self._snap_rows - target_row)))
        self._cursor_row = int(self._snap_rows[idx])
        self._cursor_line.blockSignals(True)
        try:
            self._cursor_line.setValue(float(self._snap_xs[idx]))
        finally:
            self._cursor_line.blockSignals(False)
        self._update_nav_buttons()
        self._update_cursor_label()
        self._reposition_cursor_text()

    # -------- File-boundary verticals ---------------------------------------
    def _clear_file_boundaries(self) -> None:
        for item in self._boundary_items:
            self._time_lapse_plot.removeItem(item)
        self._boundary_items = []

    def _update_file_boundaries(self, *, n, use_time, compress,
                                any_finite, valid_ts, compressed_idx) -> None:
        """Draw thin dashed verticals between adjacent records belonging to
        different files. Only shown in per-frame mode (there's nothing to
        delimit when one row == one file)."""
        self._clear_file_boundaries()
        if n < 2 or not self._rb_per_frame.isChecked():
            return
        # Find rows i where records[i-1].file != records[i].file.
        files = [getattr(r, 'file', '') or '' for r in self._records]
        pen = pg.mkPen(QColor('#888888'), width=1, style=QtCore.Qt.PenStyle.DashLine)
        for i in range(1, n):
            if files[i] == files[i - 1]:
                continue
            # Translate the boundary (between row i-1 and i) to current x coord.
            if use_time:
                if not (valid_ts[i - 1] and valid_ts[i]):
                    continue
                x = 0.5 * (self._timestamps[i - 1] + self._timestamps[i])
            elif compress:
                if compressed_idx is None:
                    continue
                # In compress mode we only draw a boundary when both adjacent
                # files have a visible row — a dangling line past the last
                # drawn point is noise, not information.
                if not (any_finite[i - 1] and any_finite[i]):
                    continue
                x = 0.5 * (compressed_idx[i - 1] + compressed_idx[i])
            else:
                x = i - 0.5
            line = pg.InfiniteLine(angle=90, movable=False, pen=pen)
            line.setValue(float(x))
            self._time_lapse_plot.addItem(line, ignoreBounds=True)
            self._boundary_items.append(line)

    # -------- Cursor interaction --------------------------------------------
    def _on_cursor_moved(self):
        if len(self._snap_xs) == 0:
            return
        x = self._cursor_line.getXPos()
        idx = int(np.argmin(np.abs(self._snap_xs - x)))
        self._cursor_row = int(self._snap_rows[idx])
        self._update_cursor_label()

    def _on_cursor_released(self):
        if len(self._snap_xs) == 0 or self._cursor_row < 0:
            return
        idx = int(np.argmin(np.abs(self._snap_rows - self._cursor_row)))
        self._cursor_line.blockSignals(True)
        try:
            self._cursor_line.setValue(float(self._snap_xs[idx]))
        finally:
            self._cursor_line.blockSignals(False)
        self._update_nav_buttons()
        rec = self._records[self._cursor_row] if 0 <= self._cursor_row < len(self._records) else None
        if rec is not None:
            self.cursor_record_changed.emit(rec)

    def _on_prev_clicked(self):
        self._step_cursor(-1)

    def _on_next_clicked(self):
        self._step_cursor(+1)

    def _step_cursor(self, delta: int) -> None:
        if len(self._snap_rows) == 0:
            return
        idx = int(np.argmin(np.abs(self._snap_rows - self._cursor_row)))
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._snap_rows):
            return
        self._cursor_row = int(self._snap_rows[new_idx])
        self._cursor_line.blockSignals(True)
        try:
            self._cursor_line.setValue(float(self._snap_xs[new_idx]))
        finally:
            self._cursor_line.blockSignals(False)
        self._update_nav_buttons()
        self._update_cursor_label()
        rec = self._records[self._cursor_row] if 0 <= self._cursor_row < len(self._records) else None
        if rec is not None:
            self.cursor_record_changed.emit(rec)

    def _update_nav_buttons(self):
        n = len(self._snap_rows)
        if n == 0:
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
            return
        idx = int(np.argmin(np.abs(self._snap_rows - self._cursor_row)))
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < n - 1)

    def _update_cursor_label(self):
        row = self._cursor_row
        if not (0 <= row < len(self._records)):
            self._cursor_text.setHtml('')
            return
        rec = self._records[row]
        ds = getattr(rec, 'T_DS', float('nan'))
        us = getattr(rec, 'T_US', float('nan'))
        parts = [f'<span style="color:{colors["downstream"]};">DS: {self._fmt(ds)}</span>']
        if self._mode != 'single':
            parts.append(f'<span style="color:{colors["upstream"]};">US: {self._fmt(us)}</span>')
        fname = getattr(rec, 'file', '') or ''
        frame = int(getattr(rec, 'frame', 0))
        parts.append(f'<span style="color:#CCCCCC;">{fname} [f{frame}]</span>')
        self._cursor_text.setHtml('<br>'.join(parts))

    def _reposition_cursor_text(self, *_):
        if not self._cursor_text.isVisible():
            return
        xr, yr = self._time_lapse_plot.getViewBox().viewRange()
        self._cursor_text.setPos(xr[0], yr[1])

    # -------- Zoom / Pan ----------------------------------------------------
    def _on_mouse_mode_toggled(self):
        self._time_lapse_plot.getViewBox().setMouseMode(
            pg.ViewBox.RectMode if self._rb_zoom.isChecked() else pg.ViewBox.PanMode
        )

    # -------- Cutoffs -------------------------------------------------------
    def _on_cutoff_edited(self):
        self._save_cutoffs_to_qsettings()
        self._redraw()

    def _cutoffs(self) -> tuple[float, float]:
        def _parse(le: QtWidgets.QLineEdit, default: float) -> float:
            txt = le.text().strip()
            if not txt:
                return default
            try:
                return float(txt)
            except ValueError:
                return default
        return _parse(self._le_ymin, -np.inf), _parse(self._le_ymax, np.inf)

    def _qsettings(self) -> QSettings:
        return QSettings("PyRadiant", "PyRadiant")

    def _load_cutoffs_from_qsettings(self) -> None:
        s = self._qsettings()
        lo = s.value(f"tlog/plot_cutoffs/{self._tab_key}/min", "", type=str)
        hi = s.value(f"tlog/plot_cutoffs/{self._tab_key}/max", "", type=str)
        self._le_ymin.setText(lo)
        self._le_ymax.setText(hi)

    def _save_cutoffs_to_qsettings(self) -> None:
        s = self._qsettings()
        s.setValue(f"tlog/plot_cutoffs/{self._tab_key}/min", self._le_ymin.text().strip())
        s.setValue(f"tlog/plot_cutoffs/{self._tab_key}/max", self._le_ymax.text().strip())

    # -------- Helpers -------------------------------------------------------
    @staticmethod
    def _nan_safe(v) -> float:
        if v is None:
            return float('nan')
        try:
            return float(v)
        except (TypeError, ValueError):
            return float('nan')

    @staticmethod
    def _fmt(v) -> str:
        if v is None:
            return '—'
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return '—'
        if not np.isfinite(fv):
            return '—'
        return f'{fv:.0f}'


class dataHistoryWidget(QtWidgets.QWidget):
    file_dragged_in = QtCore.pyqtSignal(list)

    def __init__(self, *args, **kwargs):
        super().__init__()

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self.file_navigation_widget = QtWidgets.QWidget()
        self._file_navigation_widget_layout = QtWidgets.QHBoxLayout(self.file_navigation_widget)
        self._file_navigation_widget_layout.setSpacing(5)
        self.btn_lbl = QtWidgets.QLabel("Log file ")
        self.load_data_log_file_btn = QtWidgets.QPushButton('Load')
        self.load_data_log_file_lbl = QtWidgets.QLabel('')
        self._file_navigation_widget_layout.addWidget(self.btn_lbl)
        self._file_navigation_widget_layout.addWidget(self.load_data_log_file_lbl)
        self._file_navigation_widget_layout.addSpacerItem(HorizontalSpacerItem())
        self.clear_data_log_file_btn = QtWidgets.QPushButton('Clear Log')
        clear_data_icon = QIcon()
        clear_data_icon.addFile(os.path.join(resources_path, 'style', 'delete_forever.svg'))
        self.clear_data_log_file_btn.setIcon(clear_data_icon)

        self._file_navigation_widget_layout.addWidget(self.clear_data_log_file_btn)

        self._layout.addWidget(self.file_navigation_widget)

        self.temperatures_plot_widget = historyPlotWidget(scale_label="T (K)", tab_key="total")
        self._layout.addWidget(self.temperatures_plot_widget)

    def set_mode(self, mode):
        self.temperatures_plot_widget.set_mode(mode)

    def raise_widget(self):
        self.show()
        self.setWindowState(self.windowState() & ~QtCore.Qt.WindowState.WindowMinimized | QtCore.Qt.WindowState.WindowActive)
        self.activateWindow()
        self.raise_()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls:
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls:
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        if e.mimeData().hasUrls:
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
            fnames = list()
            for url in e.mimeData().urls():
                fname = str(url.toLocalFile())
                fnames.append(fname)
            self.file_dragged_in.emit(fnames)
        else:
            e.ignore()
