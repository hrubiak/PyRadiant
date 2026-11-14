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
# Spectra heatmap shown under a T-log plot: one column per record (same x
# as the T plot), wavelength up the y axis, colour = intensity. Idea from
# Dioptas' batch stack view.

import warnings

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtGui import QColor


QUANTITIES = (
    ('calibrated', 'Calibrated'),
    ('raw', 'Raw counts'),
    ('residual', 'Residual'),
)

_INTENSITY_CMAP = 'viridis'
_RESIDUAL_CMAP = 'CET-D1'   # diverging: blue ← 0 → red


class SpectraHeatmap(QtCore.QObject):
    """Heatmap of per-record spectra, driven by historyPlotWidget.

    The owner pushes the columns to draw (records in display order and
    their x positions) via set_columns(); spectra come from a dict
    {(file, frame): (ds SideSpectra, us SideSpectra)} given to
    set_spectra(), which may keep growing while a batch runs.

    Per-record rows are interpolated onto one wavelength grid per side and
    cached; display options (normalize, √, quantity) only re-assemble the
    matrix.
    """

    column_clicked = QtCore.pyqtSignal(float)   # x in plot coordinates
    settings_changed = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self._spectra: dict = {}
        self._grid: dict = {}          # side → wavelength grid
        self._row_cache: dict = {}     # (side, quantity, file, frame) → row or None
        self._records: list = []
        self._xs = np.array([], dtype=float)
        self._matrix = None
        self._mode = 'dual'

        self.plot = pg.PlotItem()
        self.plot.setMenuEnabled(False)
        self.plot.showAxis('top', show=True)
        self.plot.showAxis('right', show=True)
        self.plot.getAxis('top').setStyle(showValues=False)
        self.plot.getAxis('right').setStyle(showValues=False)
        self.plot.getAxis('bottom').setStyle(showValues=False)
        self.plot.setLabel('left', 'λ (nm)')
        vb = self.plot.getViewBox()
        vb.setMenuEnabled(False)
        vb.setMouseMode(pg.ViewBox.RectMode)
        _orig_click = vb.mouseClickEvent

        def _vb_click(ev, _orig=_orig_click, _vb=vb):
            if ev.button() == QtCore.Qt.MouseButton.LeftButton:
                self.column_clicked.emit(_vb.mapSceneToView(ev.scenePos()).x())
                ev.accept()
                return
            if ev.button() == QtCore.Qt.MouseButton.RightButton:
                _vb.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
                ev.accept()
                return
            _orig(ev)

        vb.mouseClickEvent = _vb_click

        self.image = pg.ImageItem()
        self.plot.addItem(self.image)
        self.cursor = pg.InfiniteLine(angle=90, movable=False,
                                      pen=pg.mkPen(QColor('#C90048'), width=2))
        self.cursor.setVisible(False)
        self.plot.addItem(self.cursor, ignoreBounds=True)
        self.hover = pg.TextItem(anchor=(0, 0), color='#DDDDDD', fill=pg.mkBrush(0, 0, 0, 160))
        self.hover.setZValue(20)
        self.hover.setVisible(False)
        self.plot.addItem(self.hover, ignoreBounds=True)

        self._build_controls()

    # -------- Controls ------------------------------------------------------
    def _build_controls(self) -> None:
        self.controls = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(self.controls)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.show_cb = QtWidgets.QCheckBox('Spectra:')
        self.show_cb.setChecked(True)
        self.show_cb.setToolTip('Show the spectra heatmap under the T plot')
        self.quantity_cb = QtWidgets.QComboBox()
        for key, label in QUANTITIES:
            self.quantity_cb.addItem(label, key)
        self.quantity_cb.setToolTip(
            'Calibrated: radiance the fit used · Raw counts: ROI sum after '
            'background · Residual: (data − fit) / fit')
        self._grp_side = QtWidgets.QButtonGroup(self)
        self.ds_rb = QtWidgets.QRadioButton('DS'); self.ds_rb.setChecked(True)
        self.us_rb = QtWidgets.QRadioButton('US')
        self._grp_side.addButton(self.ds_rb)
        self._grp_side.addButton(self.us_rb)
        self._grp_scale = QtWidgets.QButtonGroup(self)
        self.lin_rb = QtWidgets.QRadioButton('lin'); self.lin_rb.setChecked(True)
        self.sqrt_rb = QtWidgets.QRadioButton('√')
        self._grp_scale.addButton(self.lin_rb)
        self._grp_scale.addButton(self.sqrt_rb)
        self.normalize_cb = QtWidgets.QCheckBox('Normalize')
        self.normalize_cb.setChecked(True)
        self.normalize_cb.setToolTip('Scale each column to its own maximum')
        for w in (self.show_cb, self.quantity_cb, self.ds_rb, self.us_rb,
                  self.lin_rb, self.sqrt_rb, self.normalize_cb):
            lay.addWidget(w)

        self.show_cb.toggled.connect(self._on_settings_changed)
        self.quantity_cb.currentIndexChanged.connect(self._on_settings_changed)
        for rb in (self.ds_rb, self.us_rb, self.lin_rb, self.sqrt_rb):
            rb.clicked.connect(self._on_settings_changed)
        self.normalize_cb.toggled.connect(self._on_settings_changed)
        self._sync_control_state()

    def _on_settings_changed(self, *_):
        self._sync_control_state()
        self.settings_changed.emit()

    def _sync_control_state(self) -> None:
        on = self.show_cb.isChecked()
        residual = self.quantity() == 'residual'
        for w in (self.quantity_cb, self.ds_rb, self.us_rb):
            w.setEnabled(on)
        # Residual is already relative and signed: no normalize / √.
        for w in (self.lin_rb, self.sqrt_rb, self.normalize_cb):
            w.setEnabled(on and not residual)

    def quantity(self) -> str:
        return self.quantity_cb.currentData()

    def side(self) -> str:
        return 'us' if self.us_rb.isChecked() and self._mode != 'single' else 'ds'

    def is_shown(self) -> bool:
        return self.show_cb.isChecked()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.us_rb.setVisible(mode != 'single')
        self.ds_rb.setVisible(mode != 'single')

    # -------- Data ----------------------------------------------------------
    def set_spectra(self, spectra: dict) -> None:
        """Use `spectra` ({(file, frame): (ds, us)}). The dict may grow
        afterwards; new keys are picked up on the next set_columns()."""
        if spectra is not self._spectra:
            self._spectra = spectra
            self._grid.clear()
            self._row_cache.clear()

    def set_columns(self, records: list, xs) -> None:
        """Draw one column per record at the given (contiguous) x."""
        self._records = list(records)
        self._xs = np.asarray(xs, dtype=float)
        self._render()

    def set_cursor_x(self, x) -> None:
        if x is None or not self._records:
            self.cursor.setVisible(False)
            return
        self.cursor.setValue(float(x))
        self.cursor.setVisible(True)

    # -------- Rendering -----------------------------------------------------
    def _row(self, rec, side: str, quantity: str):
        key = (side, quantity, getattr(rec, 'file', ''), int(getattr(rec, 'frame', 0)))
        if key in self._row_cache:
            return self._row_cache[key]
        row = None
        pair = self._spectra.get(key[2:])
        spec = None if pair is None else pair[0 if side == 'ds' else 1]
        if spec is not None and spec.x.size:
            x = spec.x
            if quantity == 'raw':
                y = spec.raw
            elif quantity == 'calibrated':
                y = spec.corrected
            else:
                y = None
                if spec.fit.size and spec.fit_x.size:
                    fit = np.interp(x, spec.fit_x, spec.fit, left=np.nan, right=np.nan)
                    with np.errstate(all='ignore'):
                        y = np.where(fit > 0, (spec.corrected - fit) / fit, np.nan)
            if y is not None and y.size == x.size:
                grid = self._grid.setdefault(side, np.array(x, dtype=float))
                if x.size == grid.size and np.allclose(x, grid):
                    row = np.asarray(y, dtype=np.float32)
                else:
                    order = np.argsort(x)
                    row = np.interp(grid, x[order], y[order],
                                    left=np.nan, right=np.nan).astype(np.float32)
        # Only cache once a spectrum exists — a running batch may add it later.
        if pair is not None:
            self._row_cache[key] = row
        return row

    def _render(self) -> None:
        side, quantity = self.side(), self.quantity()
        n = len(self._records)
        rows = [self._row(r, side, quantity) for r in self._records]
        grid = self._grid.get(side)
        if not self.is_shown() or n == 0 or grid is None or grid.size < 2:
            self.image.clear()
            self._matrix = None
            return

        m = np.full((n, grid.size), np.nan, dtype=np.float32)
        for i, row in enumerate(rows):
            if row is not None:
                m[i] = row

        # The model modules set warnings.simplefilter('error'); all-NaN
        # columns would otherwise raise from nanmax/nanpercentile.
        with warnings.catch_warnings(), np.errstate(all='ignore'):
            warnings.simplefilter('ignore')
            if quantity == 'residual':
                lim = float(np.nanpercentile(np.abs(m), 98)) if np.isfinite(m).any() else 1.0
                lim = lim if np.isfinite(lim) and lim > 0 else 1.0
                levels = (-lim, lim)
                cmap = _RESIDUAL_CMAP
            else:
                if self.normalize_cb.isChecked():
                    col_max = np.nanmax(m, axis=1, keepdims=True)
                    m = np.where(col_max > 0, m / col_max, np.nan)
                if self.sqrt_rb.isChecked():
                    m = np.sqrt(np.clip(m, 0, None))
                if self.normalize_cb.isChecked():
                    levels = (0.0, 1.0)
                elif np.isfinite(m).any():
                    lo, hi = np.nanpercentile(m, [1, 99.5])
                    levels = (float(lo), float(hi) if hi > lo else float(lo) + 1.0)
                else:
                    levels = (0.0, 1.0)
                cmap = _INTENSITY_CMAP

        self._matrix = m
        self.image.setLookupTable(pg.colormap.get(cmap).getLookupTable(nPts=256))
        self.image.setImage(m, levels=levels, autoLevels=False)
        x0 = float(self._xs[0]) - 0.5 if self._xs.size else -0.5
        self.image.setRect(QtCore.QRectF(x0, float(grid[0]), float(n),
                                         float(grid[-1] - grid[0])))

    # -------- Hover ---------------------------------------------------------
    def on_mouse_moved(self, pos) -> None:
        m, grid = self._matrix, self._grid.get(self.side())
        if (m is None or grid is None or not self.plot.isVisible()
                or not self.plot.sceneBoundingRect().contains(pos)):
            self.hover.setVisible(False)
            return
        pt = self.plot.getViewBox().mapSceneToView(pos)
        col = int(round(pt.x() - (self._xs[0] if self._xs.size else 0)))
        if not (0 <= col < m.shape[0]) or not (grid[0] <= pt.y() <= grid[-1]):
            self.hover.setVisible(False)
            return
        j = int(np.argmin(np.abs(grid - pt.y())))
        rec = self._records[col]
        v = m[col, j]
        val = '—' if not np.isfinite(v) else f'{v:.3g}'
        self.hover.setText(f'{rec.file} [f{int(rec.frame)}]   {grid[j]:.1f} nm   {val}')
        xr, yr = self.plot.getViewBox().viewRange()
        self.hover.setPos(xr[0], yr[1])
        self.hover.setVisible(True)
