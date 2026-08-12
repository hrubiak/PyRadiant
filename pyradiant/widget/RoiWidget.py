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

from functools import partial
import os
import numpy as np

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QIcon
import pyqtgraph as pg
from pyqtgraph.graphicsItems.ROI import Handle
from pyqtgraph import ColorMap, HistogramLUTItem
from .CustomWidgets import HorizontalSpacerItem, VerticalSpacerItem, DoubleSpinBoxAlignRight 
#from .HistogramLUTItem import HistogramLUTItem

from .Widgets import StatusBar

from .. import resources_path

'''#pg.setConfigOption('useOpenGL', False)
pg.setConfigOption('leftButtonPan', False)
pg.setConfigOption('background', (20, 20, 20))
#pg.setConfigOption('foreground', 'b')
pg.setConfigOption('antialias', True)'''

colors = {
    'data_pen': '#FFFFFF',
    'data_brush': '#FFFFFF',
    'fit_pen': 'r',
    'downstream': '#FFD700',
    'upstream': '#FF6F61',
    'combined': '#66FFFF'
}


class RoiWidget(QtWidgets.QWidget):
    rois_changed = QtCore.pyqtSignal(list)
    wl_range_changed = QtCore.pyqtSignal(list)
    # Emitted when the user drags a signal ROI on a cal viewer while that
    # side is in cross-mode. Payload: (side, cal-dim signal-ROI limits).
    cal_signal_roi_changed = QtCore.pyqtSignal(str, list)

    def __init__(self, roi_num=1, roi_titles=('',), roi_colors=((255, 255, 0)), *args, **kwargs):
        super(RoiWidget, self).__init__(*args, **kwargs)
        # Per-side cross-mode flags. When True for a side, that side's cal
        # 2D viewer uses cal-native axis and shows only its signal ROI at
        # cal-dim coordinates (backgrounds hidden). The signal ROI on that
        # viewer is decoupled from the shared kinetics-dim ROI sync.
        self._ds_cross_mode = False
        self._us_cross_mode = False
        self.roi_num = roi_num
        self.roi_titles = roi_titles
        self.roi_colors = roi_colors

        self._main_vertical_layout = QtWidgets.QVBoxLayout()
        self._main_vertical_layout.setContentsMargins(0, 0, 0, 0)
        self._main_vertical_layout.setSpacing(5)

        self.img_widget = RoiImageWidget(roi_num=roi_num, roi_colors=roi_colors)
        self.ccd_widget = RoiImageWidget(roi_num=0, roi_colors=[])
        self.specra_widget = RoiSpectraWidget()

        # Intensity-calibration viewers (2D image + 1D extracted spectrum, per side).
        # 2D viewers carry the same 4 ROIs as the data image so users can drag
        # ROIs while inspecting the calibration; positions sync bidirectionally
        # with the data image ROIs (see _wire_roi_sync).
        # DS Cal image hides indices 1 (us) & 3 (us_bg); US Cal hides 0 (ds) & 2 (ds_bg).
        self.ds_cal_img_widget = RoiImageWidget(roi_num=roi_num, roi_colors=roi_colors)
        self.us_cal_img_widget = RoiImageWidget(roi_num=roi_num, roi_colors=roi_colors)
        self.ds_cal_spec_widget = CalibrationSpecWidget(colors['downstream'])
        self.us_cal_spec_widget = CalibrationSpecWidget(colors['upstream'])

        self.left_tab_widget = QtWidgets.QTabWidget()
        self.left_tab_widget.setTabPosition(QtWidgets.QTabWidget.TabPosition.West)
        self.left_tab_widget.setCurrentIndex(0)
        self.left_tab_widget.addTab(self.specra_widget, '1D')
        self.left_tab_widget.addTab(self.img_widget, '2D')
        self.left_tab_widget.addTab(self.ccd_widget, 'RAW')
        # Cal tabs — grouped per side, 1D then 2D to match the existing data
        # tab order above (1D, 2D, RAW).
        self._ds_cal_spec_tab_ind = self.left_tab_widget.addTab(self.ds_cal_spec_widget, 'DS Cal 1D')
        self._ds_cal_img_tab_ind = self.left_tab_widget.addTab(self.ds_cal_img_widget, 'DS Cal 2D')
        self._us_cal_spec_tab_ind = self.left_tab_widget.addTab(self.us_cal_spec_widget, 'US Cal 1D')
        self._us_cal_img_tab_ind = self.left_tab_widget.addTab(self.us_cal_img_widget, 'US Cal 2D')


        self.wl_range_widget = wavelengthRangeGB()
        
        self.status_bar = self.img_widget.status_bar

        self.roi_gb = QtWidgets.QGroupBox('ROI')
        self.roi_gb.setMaximumWidth(300)
        self._roi_v_bs_layout = QtWidgets.QVBoxLayout(self.roi_gb)
        
        self._roi_gbs_layout = QtWidgets.QGridLayout()
        self._roi_gbs_layout.setSpacing(2)
        self.roi_gbs = []
        self.create_roi_gbs()
        self._roi_v_bs_layout.addLayout(self._roi_gbs_layout)
        # (Old bg-mode checkboxes removed — replaced by BackgroundSubtractionGB
        # in TemperatureWidget's settings panel.)

        self._main_vertical_layout.addWidget(self.left_tab_widget)
        #self._main_vertical_layout.addWidget(self.roi_gb)
        #self._main_vertical_layout.addWidget(self.status_bar)

        self.pos_lbl = self.status_bar.left_lbl

        
       
        self.setLayout(self._main_vertical_layout)

        self.create_signals()
        
      

    def create_roi_gbs(self):
        
        for ind in range(self.roi_num):
            row = ind % 2
            col = ind // 2
            self.roi_gbs.append(RoiGroupBox(self.roi_titles[ind], self.roi_colors[ind]))
            self.roi_gbs[-1].roi_txt_changed.connect(partial(self._update_img_roi, ind))
            self._roi_gbs_layout.addWidget(self.roi_gbs[-1], row, col)

    def create_signals(self):
        self.img_widget.rois_changed.connect(self._update_roi_gbs)
        self.wl_range_widget.wl_start.editingFinished.connect(self.wl_range_widget_editingFinished_callback)
        self.wl_range_widget.wl_end.editingFinished.connect(self.wl_range_widget_editingFinished_callback)

        # Cal-image ROIs: hide the ones not relevant to each side, and mirror
        # positions bidirectionally with the data image ROIs so a drag on any
        # of the three viewers updates the other two.
        # DS cal: hide us(1) and us_bg(3). US cal: hide ds(0) and ds_bg(2).
        if len(self.ds_cal_img_widget.rois) >= 4:
            self.ds_cal_img_widget.rois[1].setVisible(False)
            self.ds_cal_img_widget.rois[3].setVisible(False)
        if len(self.us_cal_img_widget.rois) >= 4:
            self.us_cal_img_widget.rois[0].setVisible(False)
            self.us_cal_img_widget.rois[2].setVisible(False)
        self._wire_roi_sync()

    def _wire_roi_sync(self):
        """Mirror ROI positions across data + ds_cal + us_cal 2D viewers.

        When a ROI at index i moves on any viewer, copy its (pos, size) to the
        same-index ROI on the other viewers. A re-entry flag prevents the
        obvious infinite ping-pong; we don't block signals on the receivers so
        that each viewer's own roi_changed handler still runs (which does the
        within-image x-coord synchronization the app relies on).
        """
        self._roi_sync_in_progress = False
        viewers = [self.img_widget, self.ds_cal_img_widget, self.us_cal_img_widget]
        n = min(len(v.rois) for v in viewers)
        for i in range(n):
            for src in viewers:
                src.rois[i].sigRegionChanged.connect(
                    partial(self._mirror_roi, viewers, i))

    def _mirror_roi(self, viewers, index, source_roi):
        if self._roi_sync_in_progress:
            return
        self._roi_sync_in_progress = True
        try:
            # In cross-mode, the affected cal viewer's signal ROI lives in
            # cal-dim coords while the data viewer's ROI is in kinetics-dim
            # coords — a raw copy would smear one coordinate system onto the
            # other. Skip signal-ROI mirroring for the affected side; the
            # model layer's sync helpers keep the underlying ROIs consistent
            # and the controller re-plots after any resulting change.
            def is_signal_index_for_side(idx, side):
                return (side == 'ds' and idx == 0) or (side == 'us' and idx == 1)

            def skip_target(target_viewer, idx):
                if (self._ds_cross_mode and target_viewer is self.ds_cal_img_widget
                        and is_signal_index_for_side(idx, 'ds')):
                    return True
                if (self._us_cross_mode and target_viewer is self.us_cal_img_widget
                        and is_signal_index_for_side(idx, 'us')):
                    return True
                return False

            def skip_source(src_viewer, idx):
                if (self._ds_cross_mode and src_viewer is self.ds_cal_img_widget
                        and is_signal_index_for_side(idx, 'ds')):
                    return True
                if (self._us_cross_mode and src_viewer is self.us_cal_img_widget
                        and is_signal_index_for_side(idx, 'us')):
                    return True
                return False

            source_viewer = None
            for v in viewers:
                if v.rois[index] is source_roi:
                    source_viewer = v
                    break

            if source_viewer is not None and skip_source(source_viewer, index):
                # Cal-viewer drag in cross-mode: don't mirror, let the
                # dedicated cal_signal_roi_changed signal drive the update.
                side = 'ds' if source_viewer is self.ds_cal_img_widget else 'us'
                limits = source_viewer.get_roi_limits()[index]
                self.cal_signal_roi_changed.emit(side, limits)
                return

            pos = source_roi.pos()
            size = source_roi.size()
            for v in viewers:
                target = v.rois[index]
                if target is source_roi:
                    continue
                if skip_target(v, index):
                    continue
                target.setPos(pos)
                target.setSize(size)
        finally:
            self._roi_sync_in_progress = False

    def set_cross_mode_cal(self, side, cal_shape, signal_roi_limits,
                           wavelength_rect):
        """Enable cross-mode display for a cal viewer.

        `wavelength_rect` is (x, 0, w, cal_h) so the cal image occupies its
        native pixel-row range on the y axis. `signal_roi_limits` is the
        cal-dim [x_min, x_max, y_min, y_max] for the side's signal ROI.
        The other side's ROIs remain hidden as usual; background ROIs
        (idx 2 and 3) are hidden too since they don't map meaningfully
        onto a full-chip cal in kinetics mode.
        """
        assert side in ('ds', 'us')
        cal_widget = self.ds_cal_img_widget if side == 'ds' else self.us_cal_img_widget
        signal_idx = 0 if side == 'ds' else 1
        if side == 'ds':
            self._ds_cross_mode = True
        else:
            self._us_cross_mode = True
        # Cal-native geometry
        cal_widget.set_wavelength_calibration(wavelength_rect)
        # Backgrounds hidden on the cal viewer
        if len(cal_widget.rois) >= 4:
            cal_widget.rois[2].setVisible(False)
            cal_widget.rois[3].setVisible(False)
        # Position the signal ROI at cal-dim coords, bypassing shared sync
        self._roi_sync_in_progress = True
        try:
            cal_widget.blockSignals(True)
            cal_widget.update_roi(signal_idx, signal_roi_limits)
            cal_widget.blockSignals(False)
        finally:
            self._roi_sync_in_progress = False

    def clear_cross_mode_cal(self, side, shared_rect=None,
                             shared_signal_roi=None):
        """Disable cross-mode for a cal viewer; restore shared visibility
        (DS Cal shows idx 0, 2; US Cal shows idx 1, 3). Optionally apply
        the shared wavelength rect and the shared (data-dim) signal ROI
        position so the cal viewer matches the data viewer immediately —
        needed when transitioning back from cross-mode without going
        through the full data_changed_signal_callback pipeline."""
        if side == 'ds':
            self._ds_cross_mode = False
            cal_widget = self.ds_cal_img_widget
            signal_idx = 0
            visibility = (True, False, True, False)
        else:
            self._us_cross_mode = False
            cal_widget = self.us_cal_img_widget
            signal_idx = 1
            visibility = (False, True, False, True)
        if len(cal_widget.rois) >= 4:
            for i, vis in enumerate(visibility):
                cal_widget.rois[i].setVisible(vis)
        if shared_rect is not None:
            cal_widget.set_wavelength_calibration(shared_rect)
        if shared_signal_roi is not None:
            self._roi_sync_in_progress = True
            try:
                cal_widget.blockSignals(True)
                cal_widget.update_roi(signal_idx, shared_signal_roi)
                cal_widget.blockSignals(False)
            finally:
                self._roi_sync_in_progress = False

    def wl_range_widget_editingFinished_callback(self):
        wl_range = [int(round(float(str(self.wl_range_widget.wl_start.text())))),int(round(float(str(self.wl_range_widget.wl_end.text()))))]
        self.wl_range_changed.emit(wl_range)
    
    def set_wl_range(self, wl_range):
        self.wl_range_widget.blockSignals(True)
        self.wl_range_widget.wl_start.setText(str(wl_range[0]))
        self.wl_range_widget.wl_end.setText(str(wl_range[1]))
        self.wl_range_widget.blockSignals(False)

    def _update_roi_gbs(self, rois_list):
        for ind, roi_gb in enumerate(self.roi_gbs):
            roi_gb.blockSignals(True)
            roi_gb.update_roi_txt(rois_list[ind])
            roi_gb.blockSignals(False)
        roi_limits = self.img_widget.get_roi_limits()
        self.rois_changed.emit(roi_limits)

    def _update_img_roi(self, ind, roi_list):

        gb_y_start = int(str(self.roi_gbs[ind].y_min_txt.text()))
        gb_y_end = int(str(self.roi_gbs[ind].y_max_txt.text()))
        n = int(round(abs(gb_y_end - gb_y_start)))
        self.roi_gbs[ind].y_n_txt.setText(str(n))
        # make user horizontal range is always synched
        for gb in self.roi_gbs:

            x_start = roi_list[0]
            x_end = roi_list[1]
            gb_x_start = int(round(float(gb.x_min_txt.value())))
            gb_x_end = int(round(float(gb.x_max_txt.value())))
            
            gb.blockSignals(True)
            gb.x_min_txt.setValue(int(x_start))
            gb.x_max_txt.setValue(int(x_end))
            gb.blockSignals(False)

        # Update the data image and both cal images in lockstep. Each viewer's
        # update_roi blocks its own sigRegionChanged, so the sigRegionChanged-
        # driven mirror in _wire_roi_sync is bypassed here — we mirror
        # explicitly instead so the cal-image ROIs stay in sync after .trs
        # restore / set_rois calls (which never fire sigRegionChanged).
        # In cross-mode, a cal viewer's signal ROI lives in cal-dim coords
        # and is set separately via set_cross_mode_cal — skip it here so
        # kinetics-dim coords don't clobber the cal-dim display.
        for w in (self.img_widget, self.ds_cal_img_widget, self.us_cal_img_widget):
            if w is self.ds_cal_img_widget and self._ds_cross_mode and ind == 0:
                continue
            if w is self.us_cal_img_widget and self._us_cross_mode and ind == 1:
                continue
            w.blockSignals(True)
            w.update_roi(ind, roi_list)
            w.blockSignals(False)
        roi_limits = self.img_widget.get_roi_limits()
        self.rois_changed.emit(roi_limits)

    def set_rois(self, rois_list):
        self.blockSignals(True)
        self.roi_gb.blockSignals(True)
        for ind in range(self.roi_num):
            self._update_img_roi(ind, rois_list[ind])
        self._update_roi_gbs(rois_list)
        
        self.blockSignals(False)
        self.roi_gb.blockSignals(False)

    def get_rois(self):
        return self.img_widget.get_roi_limits()

    def plot_img(self, img_data):
        if img_data is not None:
            self.img_widget.plot_image(img_data.T)

    def plot_raw_ccd(self, ccd_data):
        if ccd_data is not None:
            self.ccd_widget.plot_image(ccd_data.T)

    def plot_ds_calibration_image(self, img_data):
        if img_data is not None:
            self.ds_cal_img_widget.plot_image(img_data.T)

    def plot_us_calibration_image(self, img_data):
        if img_data is not None:
            self.us_cal_img_widget.plot_image(img_data.T)

    def plot_ds_calibration_spectrum(self, x, y):
        self.ds_cal_spec_widget.plot_data(x, y)

    def plot_us_calibration_spectrum(self, x, y):
        self.us_cal_spec_widget.plot_data(x, y)

    def add_item(self, pg_item):
        self.img_widget.pg_viewbox.addItem(pg_item)

    def set_mode(self, mode):
        """Hide the us ROI (index 1) and us_bg ROI (index 3) in single-sided mode:
        the ROI overlays on the 2D image, the us spectrum panel, the us/us_bg
        parameter group boxes in the 'ROI' section, and the US Cal tabs."""
        self.img_widget.set_mode(mode)
        self.specra_widget.set_mode(mode)
        # ROI parameter group boxes: laid out (0,0)=ds, (1,0)=us, (0,1)=ds_bg, (1,1)=us_bg.
        # Hide the us row (indices 1 and 3).
        dual = mode == 'dual'
        if len(self.roi_gbs) >= 4:
            self.roi_gbs[1].setVisible(dual)
            self.roi_gbs[3].setVisible(dual)
        # Hide US Cal tabs when only one side is meaningful.
        self.left_tab_widget.setTabVisible(self._us_cal_img_tab_ind, dual)
        self.left_tab_widget.setTabVisible(self._us_cal_spec_tab_ind, dual)


class wavelengthRangeGB(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('Wavelength range (nm)')
        
        self._layout = QtWidgets.QGridLayout(self)

        start_lbl = QtWidgets.QLabel("Start:")
        

        self._layout.addWidget(start_lbl,0,0)
        self.wl_start = IntegerTextField('0')
        self._layout.addWidget(self.wl_start,0,1)
        self._layout.addWidget(QtWidgets.QLabel("End:"),0,2)
        self.wl_end= IntegerTextField('0')
        self._layout.addWidget(self.wl_end,0,3)
        
        self.setMaximumWidth(300)

class RoiGroupBox(QtWidgets.QGroupBox):
    roi_txt_changed = QtCore.pyqtSignal(list)

    def __init__(self, title, color):
        super(RoiGroupBox, self).__init__(title)
        self.color = color
        self._layout = QtWidgets.QVBoxLayout()
        self._grid_layout = QtWidgets.QGridLayout()
        self._grid_layout.setSpacing(2)
        self._layout.addLayout(self._grid_layout)

        self.x_min_txt = IntegerSpinBox()
        self.x_max_txt = IntegerSpinBox()
        self.y_min_txt = IntegerSpinBox()
        self.y_max_txt = IntegerSpinBox()
        self.y_n_txt = IntegerTextField()
        self.y_n_txt.setReadOnly(True)
        
        self.y_min_txt.setMaximumWidth(70)
        self.y_max_txt.setMaximumWidth(70)
        self.y_n_txt.setMaximumWidth(70)

        
        #self._grid_layout.addWidget(CenteredQLabel('Row:'), 1, 0)
        self._start_lbl = QtWidgets.QLabel('Start:')
        self._end_lbl = QtWidgets.QLabel('End:')
        self._n_lbl = QtWidgets.QLabel('n:')
        self._start_lbl.setMaximumWidth(70)
        self._end_lbl.setMaximumWidth(70)
        self._n_lbl.setMaximumWidth(70)
        self._grid_layout.addWidget(self._start_lbl, 0, 0)
        self._grid_layout.addWidget(self._end_lbl, 1, 0)
        self._grid_layout.addWidget(self._n_lbl, 2, 0)
        self._grid_layout.addWidget(self.y_min_txt, 0, 1)
        self._grid_layout.addWidget(self.y_max_txt, 1, 1)
        self._grid_layout.addWidget(self.y_n_txt, 2, 1)


        #self._grid_layout.addWidget(CenteredQLabel('X:'), 1, 0)
        #self._grid_layout.addWidget(self.x_min_txt, 3, 1)
        #self._grid_layout.addWidget(self.x_max_txt, 4, 1)

        


        self.setLayout(self._layout)
        style_str = "color: rgb{0}; border: 1px solid rgb{0};".format(self.color)
        self.setStyleSheet('QGroupBox {' + style_str + '}')
        '''self.setMaximumWidth(150)
        self.setMinimumWidth(150)'''
        self.create_signals()
     

    def create_signals(self):
        self.x_min_txt.valueChanged.connect(partial(self._roi_txt_changed, self.x_min_txt))
        self.x_max_txt.valueChanged.connect(partial(self._roi_txt_changed, self.x_max_txt))
        self.y_min_txt.valueChanged.connect(partial(self._roi_txt_changed, self.y_min_txt))
        self.y_max_txt.valueChanged.connect(partial(self._roi_txt_changed, self.y_max_txt))

    def get_roi_limits(self):
        x_min = int(self.x_min_txt.value())
        x_max = int(self.x_max_txt.value())
        y_min = int(self.y_min_txt.value())
        y_max = int(self.y_max_txt.value())

        return [x_min, x_max, y_min, y_max]

    def update_roi_txt(self, roi_list):
        self.blockSignals(True)
        self.x_min_txt.setValue(int(np.round(roi_list[0])))
        self.x_max_txt.setValue(int(np.round(roi_list[1])))
        self.y_min_txt.setValue(int(np.round(roi_list[2])))
        self.y_max_txt.setValue(int(np.round(roi_list[3])))
        self.y_n_txt.setText(str(int(abs(np.round(roi_list[3])-np.round(roi_list[2])))))
        self.blockSignals(False)

    def _roi_txt_changed(self, txt_box):
        self.roi_txt_changed.emit(self.get_roi_limits())


class CenteredQLabel(QtWidgets.QLabel):
    def __init__(self, *args, **kwargs):
        super(CenteredQLabel, self).__init__(*args, **kwargs)
        self.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignHCenter)


class IntegerTextField(QtWidgets.QLineEdit):
    def __init__(self, *args, **kwargs):
        super(IntegerTextField, self).__init__(*args, **kwargs)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.setValidator(QtGui.QIntValidator())

class IntegerSpinBox(DoubleSpinBoxAlignRight):
    def __init__(self, *args, **kwargs):
        super().__init__()
    
        self.setDecimals(0)
        self.setMinimum(0)
        self.setMaximum(10000)
        self.setSingleStep(1)
        
class SpectrumViewBox(pg.ViewBox):
    """Viewbox that mirrors the Temperature-tab spectrum interaction:
    left-drag to rect-zoom, right-click to auto-range (unzoom) rather than
    opening pyqtgraph's default context menu."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseMode(self.RectMode)
        self.enableAutoRange(self.XYAxes, True)

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            self.enableAutoRange(enable=1)
            ev.accept()
        else:
            super().mouseClickEvent(ev)


class CalibrationSpecWidget(QtWidgets.QWidget):
    """Single-side 1D calibration spectrum viewer, used for the DS Cal / US Cal
    1D tabs. Read-only — just renders whatever the controller pushes in via
    plot_data(x, y). Empty x/y clears the plot (fresh/no-cal state)."""
    def __init__(self, color, *args, **kwargs):
        super().__init__()
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._pg_layout_widget = pg.GraphicsLayoutWidget()
        self._pg_layout = pg.GraphicsLayout()
        self._pg_layout.setContentsMargins(0, 0, 0, 0)

        self._plot = pg.PlotItem(viewBox=SpectrumViewBox())
        self._view_box = self._plot.getViewBox()
        self._plot.showAxis('top', show=True)
        self._plot.showAxis('right', show=True)
        self._plot.getAxis('top').setStyle(showValues=False)
        self._plot.getAxis('right').setStyle(showValues=False)
        self._plot.getAxis('left').setStyle(showValues=True)
        self._plot.setLabel('bottom', '&lambda; (nm)')

        self._data_item = pg.PlotDataItem(pen=pg.mkPen(color, width=1.0))
        self._data_item.setDownsampling(True)
        self._plot.addItem(self._data_item)

        self._pg_layout.addItem(self._plot)
        self._pg_layout_widget.addItem(self._pg_layout)
        self._layout.addWidget(self._pg_layout_widget)

    def plot_data(self, x, y):
        if x is not None and y is not None and len(x):
            mx = np.amax(y) * 1.1
            if mx < 2:
                mx = 2
            self._view_box.setYRange(-1, mx)
            self._data_item.setData(x, y)
        else:
            self._data_item.setData([], [])


class RoiSpectraWidget(QtWidgets.QWidget):
    mouse_moved = QtCore.pyqtSignal(float, float)


    def __init__(self, *args, **kwargs):
        super().__init__()

        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._pg_us_layout_widget = pg.GraphicsLayoutWidget()
        self._pg_us_layout = pg.GraphicsLayout()
        self._pg_us_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_us_layout.layout.setVerticalSpacing(0)
        
        self._us_plot = pg.PlotItem(viewBox=SpectrumViewBox())
        self._us_view_box = self._us_plot.getViewBox()
        
        self._us_plot.showAxis('top', show=True)
        self._us_plot.showAxis('right', show=True)
        self._us_plot.getAxis('top').setStyle(showValues=False)
        self._us_plot.getAxis('right').setStyle(showValues=False)
        self._us_plot.getAxis('left').setStyle(showValues=True)
        self._us_plot.setTitle("Upstream", color=QColor(colors['upstream']), size='20pt')
        self._us_plot.setLabel('bottom', '&lambda; (nm)')
        #self._us_plot.setMinimumWidth(120)
        
        #self._us_plot.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._pg_us_layout.addItem(self._us_plot)
        self._pg_us_layout_widget.addItem(self._pg_us_layout)

        self._pg_ds_layout_widget = pg.GraphicsLayoutWidget()
        self._pg_ds_layout = pg.GraphicsLayout()
        self._pg_ds_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_ds_layout.layout.setVerticalSpacing(0)

        self._ds_plot = pg.PlotItem(viewBox=SpectrumViewBox())
        self._ds_view_box = self._ds_plot.getViewBox()
        self._ds_plot.showAxis('top', show=True)
        self._ds_plot.showAxis('right', show=True)
        self._ds_plot.getAxis('top').setStyle(showValues=False)
        self._ds_plot.getAxis('right').setStyle(showValues=False)
        self._ds_plot.getAxis('left').setStyle(showValues=True)
        self._ds_plot.setTitle("Downstream", color=QColor(colors['downstream']), size='20pt')
        self._ds_plot.setLabel('bottom', '&lambda; (nm)')
        #self._ds_plot.setMinimumWidth(120)
        
        #self._ds_plot.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._pg_ds_layout.addItem(self._ds_plot)
        self._pg_ds_layout_widget.addItem(self._pg_ds_layout)


        self.plots_widget = QtWidgets.QWidget()
        self._plots_widget_layout = QtWidgets.QHBoxLayout(self.plots_widget)
        self._plots_widget_layout.setContentsMargins(0, 0, 0, 0)
        self._plots_widget_layout.setSpacing(0)
        self._plots_widget_layout.addWidget(self._pg_ds_layout_widget)
        self._plots_widget_layout.addWidget(self._pg_us_layout_widget)
        
        self._layout.addWidget(self.plots_widget)

        self._ds_data_item = pg.PlotDataItem(pen=pg.mkPen("#fff", width=1.0))
        self._ds_data_item.setDownsampling(True)
        self._ds_plot.addItem(self._ds_data_item)

        self._us_data_item = pg.PlotDataItem(pen=pg.mkPen("#fff", width=1.0))
        self._us_data_item.setDownsampling(True)
        self._us_plot.addItem(self._us_data_item)
        
    def plot_ds_data(self, x, y):
        if len(y):
            mx = np.amax(y)*1.1
            if mx < 2:
                mx = 2
            self._ds_view_box.setYRange(-1,mx)
        self._ds_data_item.setData(x, y)

    def plot_us_data(self, x, y):
        if len(y):
            mx = np.amax(y)*1.1
            if mx < 2:
                mx = 2
            self._us_view_box.setYRange(-1,mx)
        self._us_data_item.setData(x, y)

    def set_mode(self, mode):
        dual = mode == 'dual'
        self._pg_us_layout_widget.setVisible(dual)
        if dual:
            self._ds_plot.setTitle("Downstream", color=QColor(colors['downstream']), size='20pt')
        else:
            # This tab shows the raw 1D ROI-extracted counts vs wavelength — call
            # it "Spectrum", not "Temperature" (that name belongs on the fit view).
            self._ds_plot.setTitle("Spectrum", color=QColor(colors['downstream']), size='20pt')


class RoiImageWidget(QtWidgets.QWidget):
    mouse_moved = QtCore.pyqtSignal(float, float)
    #mouse_left_clicked = QtCore.pyqtSignal(float, float)
    #mouse_left_double_clicked = QtCore.pyqtSignal(float, float)

    rois_changed = QtCore.pyqtSignal(list)

    def __init__(self, roi_num=1, roi_colors=((255, 255, 0)), *args, **kwargs):
        super(RoiImageWidget, self).__init__(*args, **kwargs)
        self.roi_num = roi_num
        self.roi_colors = roi_colors
        self.rectangle = None
        self.pg_widget = pg.GraphicsLayoutWidget()
        self.pg_layout = self.pg_widget.ci
        self.pg_layout.setContentsMargins(0, 10, 15, 0)
        self.pg_viewbox = self.pg_layout.addViewBox(1, 1, lockAspect=False)
        self.pg_viewbox.invertY(True)

        self.bottom_axis = pg.AxisItem('bottom',  linkView=self.pg_viewbox)
        self.bottom_axis.setLabel('&lambda; (nm)')
        self.left_axis = pg.AxisItem('left', linkView=self.pg_viewbox)

        self.pg_layout.addItem(self.bottom_axis, 2, 1)
        self.pg_layout.addItem(self.left_axis, 1, 0)

        self.pg_img_item = pg.ImageItem()
      
        self.pg_viewbox.addItem(self.pg_img_item)

        # Define your custom colors
        custom_colors = [(49, 52, 138),  
                        (112, 186,234),  
                        (207,223,96), 
                        (222,131,46), 
                        (119,31,28)]  
        # Create a ColorMap using the custom colors
        cmap = ColorMap(pos=[0, 0.25, 0.5, 0.75, 1], color=custom_colors)

        self.pg_hist_item = HistogramLUTItem(self.pg_img_item)#, orientation='vertical',
                                            #)
        self.pg_hist_item.axis.setStyle(showValues=False)
        self.pg_hist_item.setFixedWidth(70)

        self.pg_hist_item.gradient.setColorMap(cmap)

        self.pg_layout.addItem(self.pg_hist_item, 1, 2, 1, 3)

        self._layout = QtWidgets.QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.pg_widget)
        self.status_bar = StatusBar()
        self._layout.addWidget(self.status_bar)
        self.setLayout(self._layout)
        self.add_rois()
        self.modify_mouse_behavior()

    def set_wavelength_calibration(self, rectangle):
        if self.rectangle != rectangle:
            self.rectangle = rectangle
            # setRect requires the image item to have a shape; when no image is
            # loaded yet (fresh cal-image viewer waiting for data), just store
            # the rectangle. plot_image will re-apply it once an image lands.
            if self.pg_img_item.image is not None:
                self.pg_img_item.setRect(*rectangle)
            x_min = self.rectangle[0]
            x_max = self.rectangle[0]+ self.rectangle[2]
            y_min = self.rectangle[1]
            y_max = self.rectangle[1]+ self.rectangle[3]
            self.pg_viewbox.setLimits(xMin=x_min, xMax=x_max,
                                    yMin=y_min, yMax=y_max)
            self.pg_viewbox.autoRange()

    def add_rois(self):
        self.rois = []
        for ind in range(self.roi_num):
            self.rois.append(ImgROI((25, 25), (150, 150),
                                    pen=pg.mkPen(self.roi_colors[ind], width=2),
                                    active_pen=pg.mkPen('r', width=3)))
            self.pg_viewbox.addItem(self.rois[-1])
            self.rois[-1].sigRegionChanged.connect(self.roi_changed)

    def set_mode(self, mode):
        """Hide the us data ROI (index 1) and us background ROI (index 3) in single-sided mode."""
        if not hasattr(self, 'rois') or len(self.rois) < 4:
            return
        dual = mode == 'dual'
        self.rois[1].setVisible(dual)
        self.rois[3].setVisible(dual)

    def get_roi_limits(self):
        roi_limits = []
        for roi in self.rois:
            roi_pos_x = roi.pos()[0]
            roi_size_x = roi.size()[0]
            if self.rectangle is not None and self.pg_img_item.image is not None:
                roi_pos_x =   (roi_pos_x-self.rectangle[0]) *self.pg_img_item.image.shape[0] /self.rectangle[2]
                roi_size_x = roi_size_x / self.rectangle[2]*self.pg_img_item.image.shape[0]
            limit = [int(round(roi_pos_x)), int(round(roi_pos_x + roi_size_x)),
                               roi.pos()[1], roi.pos()[1] + roi.size()[1]]
            roi_limits.append(limit)
        return roi_limits

    def roi_changed(self, *args):
        changed_roi = args[0]
        i = self.rois.index(changed_roi)
        changed_roi_x = [changed_roi.pos()[0], changed_roi.size()[0]]
        roi: ImgROI
        
        for roi in self.rois:
            roi.blockSignals(True)
            roi_pos = roi.pos()
            roi_size = roi.size()
            if roi_pos[0] != changed_roi_x[0]:
                roi_pos[0]=changed_roi_x[0]
                roi.setPos(roi_pos)
            if roi_size[0] != changed_roi_x[1]:
                roi_size[0]=changed_roi_x[1]
                roi.setSize(roi_size)
            roi.blockSignals(False)
        
        limits = self.get_roi_limits()
        #print(limits)
        self.rois_changed.emit(limits)

    def update_roi(self, ind, roi_limits):
        

        pos = [roi_limits[0], roi_limits[2]]
        size = [roi_limits[1] - roi_limits[0],
                                roi_limits[3] - roi_limits[2]]
        # Convert pixel-space limits to viewbox (wavelength) coords only when
        # both the wavelength rectangle AND the underlying image are available.
        # After a config switch that cleared a cal-image viewer, `rectangle`
        # can still carry the previous config's calibration but `image` is None.
        if self.rectangle is not None and self.pg_img_item.image is not None:
            pos[0] = int(round(self.rectangle[0] + pos[0] /self.pg_img_item.image.shape[0] *self.rectangle[2]))
            size[0] = int(round(size[0] * (self.rectangle[2]/self.pg_img_item.image.shape[0])))
        
        self.rois[ind].blockSignals(True)
        self.rois[ind].setPos(pos)
        self.rois[ind].setSize(size)
        self.rois[ind].blockSignals(False)

        for roi in self.rois:
            p = roi.pos()
            s = roi.size()
            if p[0] != pos[0] or s[0] != size[0]:
                roi .blockSignals(True)
                p[0] = pos[0]
                s[0] = size[0]
                roi.setPos(p)
                roi.setSize(s)
                roi .blockSignals(False)

    def plot_image(self, data):
        self.pg_img_item.setImage(data)
        # If a wavelength rectangle was set before the image arrived, apply it now.
        if self.rectangle is not None:
            self.pg_img_item.setRect(*self.rectangle)
        '''if self.rectangle != None:
            x_min = self.rectangle[0]
            x_max = self.rectangle[0]+ self.rectangle[2]
            y_min = self.rectangle[1]
            y_max = self.rectangle[1]+ self.rectangle[3]
            self.pg_viewbox.setLimits(xMin=x_min, xMax=x_max,
                                    yMin=y_min, yMax=y_max)
        else:
            
            x_max, y_max = data.shape
            self.pg_viewbox.setLimits(xMin=0, xMax=x_max,
                                    yMin=0, yMax=y_max)
        '''
    @property
    def img_data(self):
        return self.pg_img_item.image

    def mouseMoved(self, pos):
        pos = self.pg_img_item.mapFromScene(pos)
        self.mouse_moved.emit(pos.x(), pos.y())

    def modify_mouse_behavior(self):
        self.pg_viewbox.setMouseMode(self.pg_viewbox.RectMode)
        self.pg_layout.scene().sigMouseMoved.connect(self.mouseMoved)
        self.pg_viewbox.mouseClickEvent = self.myMouseClickEvent

    def myMouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.RightButton or \
                (ev.button() == QtCore.Qt.MouseButton.LeftButton and
                         ev.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier):
            self.pg_viewbox.autoRange()
        ev.accept()

class ImgROI(pg.ROI):

    def __init__(self, pos, size, pen, active_pen):
        super(ImgROI, self).__init__(pos, size, False, True)

        self.setPen(pen)
        self.active_pen = active_pen

        self.addScaleHandle([1, 0.5], [0, 0.5],
                            item=CustomHandle(7.5, typ='f', pen=pen, activePen=active_pen, parent=self))
        self.addScaleHandle([0.5, 1], [0.5, 0],
                            item=CustomHandle(7.5, typ='f', pen=pen, activePen=active_pen, parent=self))
        self.addScaleHandle([0, 0.5], [1, 0.5],
                            item=CustomHandle(7.5, typ='f', pen=pen, activePen=active_pen, parent=self))
        self.addScaleHandle([0.5, 0], [0.5, 1],
                            item=CustomHandle(7.5, typ='f', pen=pen, activePen=active_pen, parent=self))

    def hoverEvent(self, ev):
        hover = False
        if not ev.isExit():
            if ev.acceptDrags(QtCore.Qt.MouseButton.LeftButton):
                hover = True
            for btn in [QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.MiddleButton]:
                # Check if the acceptedMouseButtons mask includes the button
                if (self.acceptedMouseButtons() & btn) and ev.acceptClicks(btn):
                    hover = True

        if hover:
            self.currentPen = self.active_pen
        else:
            self.currentPen = self.pen
        self.update()

    def addHandle(self, info, index=None):
        h = super(ImgROI, self).addHandle(info, index)
        h.setPos(info['pos'] * self.state['size'])
        return h


class CustomHandle(pg.graphicsItems.ROI.Handle):
    def __init__(self, radius, typ=None, pen=pg.mkPen(200, 200, 220), parent=None, deletable=False,
                 activePen=pg.mkPen(255, 255, 0)):
        super(CustomHandle, self).__init__(radius, typ=typ, pen=pen, parent=parent, deletable=deletable)

        self.pen = pen
        self.activePen = activePen

    def hoverEvent(self, ev):
        hover = False
        if not ev.isExit():
            if ev.acceptDrags(QtCore.Qt.MouseButton.LeftButton):
                hover = True
            for btn in [QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.RightButton, QtCore.Qt.MouseButton.MiddleButton]:
                if (self.acceptedMouseButtons() & btn) and ev.acceptClicks(btn):
                    hover = True

        if hover:
            self.currentPen = self.activePen
        else:
            self.currentPen = self.pen
        self.update()

    def mouseDragEvent(self, ev):
        super(CustomHandle, self).mouseDragEvent(ev)
        if ev.isFinish():
            self.currentPen = self.pen
        elif ev.isStart():
            self.currentPen = self.activePen

        if self.isMoving:  ## note: isMoving may become False in mid-drag due to right-click.
            self.currentPen = self.activePen
        self.update()
