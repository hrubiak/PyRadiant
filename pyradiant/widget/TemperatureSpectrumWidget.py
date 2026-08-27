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
from PyQt6 import QtWidgets, QtCore, QtGui

from PyQt6.QtGui import QColor, QIcon
import pyqtgraph as pg
from pyqtgraph.exporters.ImageExporter import ImageExporter
from pyqtgraph.exporters.SVGExporter import SVGExporter
import numpy as np

from PyQt6.QtCore import pyqtSignal
from .CustomWidgets import HorizontalSpacerItem, VerticalSpacerItem


from .. import resources_path

pg.setConfigOption('leftButtonPan', False)
pg.setConfigOption('background', 'k')
pg.setConfigOption('foreground', 'w')
pg.setConfigOption('antialias', True)

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


class TemperatureSpectrumWidget(QtWidgets.QWidget):
    mouse_moved = QtCore.pyqtSignal(float, float)

    def __init__(self, *args, **kwargs):
        super(TemperatureSpectrumWidget, self).__init__(*args, **kwargs)
        self._layout = QtWidgets.QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self.create_plot_items()
        self.create_data_items()

        self.setLayout(self._layout)

        self.connect_mouse_signals()

    def create_plot_items(self):
        
        self._pg_us_layout_widget = pg.GraphicsLayoutWidget()
        self._pg_us_layout = pg.GraphicsLayout()
        self._pg_us_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_us_layout.layout.setVerticalSpacing(0)
        
        us_vb = CustomViewBox() 
        self._us_plot =pg.PlotItem(viewBox=us_vb)
        self._us_view_box = self._us_plot.getViewBox()
        
        self._us_plot.showAxis('top', show=True)
        self._us_plot.showAxis('right', show=True)
        self._us_plot.getAxis('top').setStyle(showValues=False)
        self._us_plot.getAxis('right').setStyle(showValues=False)
        self._us_plot.getAxis('left').setStyle(showValues=False)
        self._us_plot.setTitle("Upstream", color=QColor(colors['upstream']), size='20pt')
        self._us_plot.setLabel('bottom', '&lambda; (nm)')
        self._us_plot.setMinimumWidth(120)
        
        self._us_plot.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self._pg_us_layout.addItem(self._us_plot)
        self._pg_us_layout_widget.addItem(self._pg_us_layout)
        
        self._pg_ds_layout_widget = pg.GraphicsLayoutWidget()
        self._pg_ds_layout = pg.GraphicsLayout()
        self._pg_ds_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_ds_layout.layout.setVerticalSpacing(0)

        ds_vb = CustomViewBox() 
        self._ds_plot = pg.PlotItem(viewBox=ds_vb)
        self._ds_view_box = self._ds_plot.getViewBox()
        self._ds_plot.showAxis('top', show=True)
        self._ds_plot.showAxis('right', show=True)
        self._ds_plot.getAxis('top').setStyle(showValues=False)
        self._ds_plot.getAxis('right').setStyle(showValues=False)
        self._ds_plot.getAxis('left').setStyle(showValues=False)
        self._ds_plot.setTitle("Downstream", color=QColor(colors['downstream']), size='20pt')
        self._ds_plot.setLabel('bottom', '&lambda; (nm)')
        self._ds_plot.setMinimumWidth(120)
        
        self._ds_plot.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self._pg_ds_layout.addItem(self._ds_plot)
        self._pg_ds_layout_widget.addItem(self._pg_ds_layout)

        self.ds_mx = 2
        self.us_mx = 2
        # Cache of the last data (x, masked y) plotted per side, plus the
        # X-range of the last fit. Y scaling in normalize_range uses the
        # data max — but constrained to the fit's X range when a fit is
        # present. The corrected spectrum can spike wildly outside the
        # fit range (Photron cal-division blow-up at ROI edges); clipping
        # to the fit range keeps the real signal visible without cutting
        # off the useful data.
        self._ds_last_data = None  # tuple (x, y) or None
        self._us_last_data = None
        self._ds_fit_x_range = None  # (xmin, xmax) or None
        self._us_fit_x_range = None
        
        self.plots_widget = QtWidgets.QWidget()
        self._plots_widget_layout = QtWidgets.QGridLayout(self.plots_widget)
        self._plots_widget_layout.setContentsMargins(0, 0, 0, 0)
        self._plots_widget_layout.setSpacing(0)
        self._plots_widget_layout.addWidget(self._pg_ds_layout_widget, 0, 0)
        self._plots_widget_layout.addWidget(self._pg_us_layout_widget, 0, 1)
        
        self._layout.addWidget(self.plots_widget)
        
        
        self.time_lapse_container_widget = QtWidgets.QWidget()
        self.time_lapse_container_widget_layout = QtWidgets.QVBoxLayout(self.time_lapse_container_widget)
        self.time_lapse_container_widget_layout.setContentsMargins(0, 0, 0, 0)
        self.time_lapse_container_widget_layout.setSpacing(0)

        self.time_lapse_widget = pg.GraphicsLayoutWidget()
        self._pg_layout = pg.GraphicsLayout()
        self._pg_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_layout.layout.setVerticalSpacing(0)
        
        self._time_lapse_plot = pg.PlotItem()
        self._time_lapse_plot.showAxis('top', show=True)
        self._time_lapse_plot.showAxis('right', show=True)
        self._time_lapse_plot.getAxis('top').setStyle(showValues=False)
        self._time_lapse_plot.getAxis('right').setStyle(showValues=False)
        self._time_lapse_plot.getAxis('bottom').setStyle(showValues=True)
        self._time_lapse_plot.setLabel('left', "T (K)")

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

        self.time_lapse_widget.addItem(self._pg_layout)
        self.time_lapse_container_widget_layout.addWidget(self.time_lapse_widget)

        self.time_lapse_widget_shown = False
        
  

    def create_data_items(self):
        # self._us_data_item = pg.ScatterPlotItem(pen=pg.mkPen(colors['data_pen'], width=1),
        #                                         brush=pg.mkBrush(colors['data_brush']),
        #                                         size=3,
        #                                         symbol ='o')
        self._us_data_item = pg.PlotDataItem(pen=pg.mkPen("#fff", width=1.0, conntect='finite',antialias=True ))
        self._us_data_item.setDownsampling(True)
        self._us_masked_data_item = pg.PlotDataItem(pen=pg.mkPen("#d61cff", width=1.0, conntect='finite',antialias=True ))
        self._us_masked_data_item.setDownsampling(True)
        self._us_fit_item = pg.PlotDataItem(pen=pg.mkPen(colors['fit_pen'], width=4, conntect='finite',antialias=True))
        self._us_fit_item.setDownsampling(True)

        self._us_plot.addItem(self._us_data_item)
        self._us_plot.addItem(self._us_masked_data_item)
        self._us_plot.addItem(self._us_fit_item)

        self._us_temperature_txt_item = pg.LabelItem()
        self._us_temperature_txt_item.setParentItem(self._us_plot.vb)
        self._us_temperature_txt_item.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(15, 10))

        self._us_roi_max_txt_item = pg.LabelItem()
        self._us_roi_max_txt_item.setParentItem(self._us_plot.vb)
        self._us_roi_max_txt_item.anchor(itemPos=(1, 1), parentPos=(1, 1), offset=(-10, -10))

        self._us_intensity_indicator = IntensityIndicator()
        self._us_intensity_indicator.setParentItem(self._us_plot)

        # self._ds_data_item = pg.ScatterPlotItem(pen=pg.mkPen(colors['data_pen'], width=1),
        #                                         brush=pg.mkBrush(colors['data_brush']),
        #                                         size=3,
        #                                         symbol ='o')
        self._ds_data_item = pg.PlotDataItem(pen=pg.mkPen("#fff", width=1.0, conntect='finite',antialias=True))
        self._ds_data_item.setDownsampling(True)
        self._ds_masked_data_item = pg.PlotDataItem(pen=pg.mkPen("#d61cff", width=1.0, conntect='finite',antialias=True ))
        self._ds_masked_data_item.setDownsampling(True)
        self._ds_fit_item = pg.PlotDataItem(pen=pg.mkPen(colors['fit_pen'], width=4, conntect='finite',antialias=True))
        self._ds_fit_item.setDownsampling(True)

        self._ds_plot.addItem(self._ds_data_item)
        self._ds_plot.addItem(self._ds_masked_data_item)
        self._ds_plot.addItem(self._ds_fit_item)

        self._ds_temperature_txt_item = pg.LabelItem()
        self._ds_temperature_txt_item.setParentItem(self._ds_plot.vb)
        self._ds_temperature_txt_item.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(15, 10))

        self._ds_roi_max_txt_item = pg.LabelItem()
        self._ds_roi_max_txt_item.setParentItem(self._ds_plot.vb)
        self._ds_roi_max_txt_item.anchor(itemPos=(1, 1), parentPos=(1, 1), offset=(-10, -10))

        self._ds_intensity_indicator = IntensityIndicator()
        self._ds_intensity_indicator.setParentItem(self._ds_plot)

        self._time_lapse_ds_data_item = pg.PlotDataItem(
            pen=pg.mkPen(QColor(colors['downstream']), width=3),
            brush=pg.mkBrush(QColor(colors['downstream'])),
            symbolPen=pg.mkPen(QColor(colors['downstream']), width=1),
            symbolBrush=pg.mkBrush(QColor(colors['downstream'])),
            size=3,
            symbol='s'
        )
        self._time_lapse_us_data_item = pg.PlotDataItem(
            pen=pg.mkPen(QColor(colors['upstream']), width=3),
            brush=pg.mkBrush(QColor(colors['upstream'])),
            symbolPen=pg.mkPen(QColor(colors['upstream']), width=1),
            symbolBrush=pg.mkBrush(QColor(colors['upstream'])),
            size=3,
            symbol='s'
        )

        self._time_lapse_plot.addItem(self._time_lapse_ds_data_item)
        self._time_lapse_plot.addItem(self._time_lapse_us_data_item)

        # Vertical markers indicating which frame is currently displayed in
        # the fit plots above. In frame mode DS and US markers coincide; in
        # lab-time / sync-frame modes they separate by the per-side offset.
        self._time_lapse_ds_marker = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(QColor(colors['downstream']), width=1,
                         style=QtCore.Qt.PenStyle.DashLine))
        self._time_lapse_us_marker = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(QColor(colors['upstream']), width=1,
                         style=QtCore.Qt.PenStyle.DashLine))
        self._time_lapse_ds_marker.hide()
        self._time_lapse_us_marker.hide()
        self._time_lapse_plot.addItem(self._time_lapse_ds_marker,
                                      ignoreBounds=True)
        self._time_lapse_plot.addItem(self._time_lapse_us_marker,
                                      ignoreBounds=True)

        # Right-click resets the view (auto-range) instead of showing the
        # default ViewBox context menu.
        _tl_vb = self._time_lapse_plot.getViewBox()
        _tl_vb.setMenuEnabled(False)
        _orig_click = _tl_vb.mouseClickEvent
        def _tl_mouse_click(ev, _vb=_tl_vb, _orig=_orig_click):
            if ev.button() == QtCore.Qt.MouseButton.RightButton:
                _vb.autoRange()
                ev.accept()
            else:
                _orig(ev)
        _tl_vb.mouseClickEvent = _tl_mouse_click

    def connect_mouse_signals(self):
        pass
        #self._ds_plot.connect_mouse_move_event()
        #self._us_plot.connect_mouse_move_event()
        #self._pg_layout.addItem(self._pg_time_lapse_layout)
        #self._time_lapse_plot.connect_mouse_move_event()
        #self._pg_layout.removeItem(self._pg_time_lapse_layout)
        #self._ds_plot.mouse_moved.connect(self.mouse_moved)
        #self._us_plot.mouse_moved.connect(self.mouse_moved)
        #self._time_lapse_plot.mouse_moved.connect(self.mouse_moved)

    def plot_ds_data(self, x, y, mask=None):

        if mask is not None:
            y[~mask] = np.nan

        # Cache for normalize_range; ds_mx is a full-range fallback used
        # when no fit range is available.
        if len(x) > 0:
            self._ds_last_data = (np.asarray(x), np.asarray(y))
            finite = y[np.isfinite(y)]
            mx = float(np.max(finite)) * 1.1 if finite.size else 1.1
        else:
            self._ds_last_data = None
            mx = 1.1
        if mx < 2:
            mx = 2
        self.ds_mx = mx

        if len(x) > 0 and not np.all(np.isnan(y)):
            self._ds_data_item.setData(x, y)
        else:
            self._ds_data_item.setData([], [])

    def plot_us_data(self, x, y, mask=None):

        if mask is not None:
            y[~mask] = np.nan

        if len(x) > 0:
            self._us_last_data = (np.asarray(x), np.asarray(y))
            finite = y[np.isfinite(y)]
            mx = float(np.max(finite)) * 1.1 if finite.size else 1.1
        else:
            self._us_last_data = None
            mx = 1.1
        if mx < 2:
            mx = 2
        self.us_mx = mx

        if len(x) > 0 and not np.all(np.isnan(y)):
            self._us_data_item.setData(x, y)
        else:
            self._us_data_item.setData([], [])

    def normalize_range(self):
        # Y max is driven by the data (never clips the curve). To avoid
        # cal-division junk at ROI edges dominating (Photron mode), the
        # max is computed inside a useful X window:
        #   - the fit's X range when a fit is present, else
        #   - the inner 60% of the data X range as a heuristic (edges
        #     are where the junk lives; inner middle is where real
        #     signal lives).
        def _mx_in_range(cache, xr):
            if cache is None:
                return None
            x, y = cache
            if xr is None and x.size:
                # No fit range known — fall back to inner 60% of x span.
                x_lo = float(np.min(x))
                x_hi = float(np.max(x))
                span = x_hi - x_lo
                xr = (x_lo + 0.2 * span, x_lo + 0.8 * span)
            if xr is not None:
                sel = (x >= xr[0]) & (x <= xr[1])
                y = y[sel]
            finite = y[np.isfinite(y)]
            if not finite.size:
                return None
            # 98th percentile — drops the top ~2% so a few residual
            # spikes inside the window don't leave a big empty gap
            # above the real signal.
            return float(np.percentile(finite, 98)) * 1.1

        candidates = []
        ds_windowed = _mx_in_range(self._ds_last_data, self._ds_fit_x_range)
        us_windowed = _mx_in_range(self._us_last_data, self._us_fit_x_range)
        if ds_windowed is not None:
            candidates.append(ds_windowed)
        if us_windowed is not None:
            candidates.append(us_windowed)
        if candidates:
            mx = max(candidates)
        else:
            mx = max(self.ds_mx, self.us_mx)
        if mx < 2:
            mx = 2
        self._us_view_box.setYRange(-1, mx)
        self._ds_view_box.setYRange(-1, mx)

    def plot_ds_masked_data(self, x, y, mask=None):
       
        if mask is not None:
            #x[~mask] = np.nan
            y[mask] = np.nan
   
        if len(x) > 0 and not np.all(np.isnan(y)):
            self._ds_masked_data_item.setData(x, y)
        else:
            self._ds_masked_data_item.setData([], [])

    def plot_us_masked_data(self, x, y, mask):
       
        if mask is not None:
            #x[~mask] = np.nan
            y[mask] = np.nan
     
        if len(x) > 0 and not np.all(np.isnan(y)):
            self._us_masked_data_item.setData(x, y)
        else:
            self._us_masked_data_item.setData([], [])

    def plot_ds_fit(self, x, y):

        if len(x) > 0 and not np.all(np.isnan(y)):
            self._ds_fit_item.setData(x, y)
            x_arr = np.asarray(x)
            self._ds_fit_x_range = (float(np.min(x_arr)), float(np.max(x_arr)))
        else:
            self._ds_fit_item.setData([], [])
            self._ds_fit_x_range = None

    def plot_us_fit(self, x, y):

        if len(x) > 0 and not np.all(np.isnan(y)):
            self._us_fit_item.setData(x, y)
            x_arr = np.asarray(x)
            self._us_fit_x_range = (float(np.min(x_arr)), float(np.max(x_arr)))
        else:
            self._us_fit_item.setData([], [])
            self._us_fit_x_range = None

    def plot_ds_time_lapse(self, x, y):
        if len(x) > 0 and not np.all(np.isnan(y)):
            self._time_lapse_ds_data_item.setData(x, y)
        else:
            self._time_lapse_ds_data_item.setData([], [])

    def plot_us_time_lapse(self, x, y):
        if len(x) > 0 and not np.all(np.isnan(y)):
            self._time_lapse_us_data_item.setData(x, y)
        else:
            self._time_lapse_us_data_item.setData([], [])

    def set_time_lapse_x_axis_label(self, text):
        self._time_lapse_plot.setLabel('bottom', text)

    def set_time_lapse_frame_marker(self, ds_x, us_x):
        """Position the DS/US vertical markers on the history plot. Pass
        None for either arg to hide that side's marker."""
        if ds_x is None:
            self._time_lapse_ds_marker.hide()
        else:
            self._time_lapse_ds_marker.setPos(float(ds_x))
            self._time_lapse_ds_marker.show()
        if us_x is None:
            self._time_lapse_us_marker.hide()
        else:
            self._time_lapse_us_marker.setPos(float(us_x))
            self._time_lapse_us_marker.show()

    def update_us_temperature_txt(self, temperature, temperature_error):
        self._us_temperature_txt_item.setText('{0:.0f} K &plusmn; {1:.0f}'.format(temperature,
                                                                                  temperature_error),
                                              size='24pt',
                                              color=colors['upstream'],
                                              justify='left')

    def update_ds_temperature_txt(self, temperature, temperature_error):
        self._ds_temperature_txt_item.setText('{0:.0f} K &plusmn; {1:.0f}'.format(temperature,
                                                                                  temperature_error),
                                              size='24pt',
                                              color=colors['downstream'],
                                              justify='left')

    def update_us_roi_max_txt(self, roi_max, format_max=65536):
        self._us_roi_max_txt_item.setText('Max Int {0:.0f}'.format(roi_max),
                                          size='18pt',
                                          color='#4DDECD',
                                          justify='right')
        self._us_intensity_indicator.set_intensity(float(roi_max) / format_max)

    def update_ds_roi_max_txt(self, roi_max, format_max=65536):
        self._ds_roi_max_txt_item.setText('Max Int {0:.0f}'.format(roi_max),
                                          size='18pt',
                                          color='#4DDECD',
                                          justify='left')
        self._ds_intensity_indicator.set_intensity(float(roi_max) / format_max)

    def show_time_lapse_plot(self, bool):
        if bool:
            if not self.time_lapse_widget_shown:
                
                self._layout.addWidget(self.time_lapse_container_widget)
                self.time_lapse_container_widget.show()
                self.time_lapse_widget_shown = True
      
        else:
            if self.time_lapse_widget_shown:
                
                self._layout.removeWidget(self.time_lapse_container_widget)
                self.time_lapse_container_widget.hide()
                self.time_lapse_widget_shown = False

    def update_time_lapse_ds_temperature_txt(self, temperature, temperature_error):
        self._time_lapse_ds_temperature_txt.setText('{0:.0f} K &plusmn; {1:.0f}'.format(temperature,
                                                                                        temperature_error),
                                                    size='16pt',
                                                    color=colors['downstream'],
                                                    justify='left')

    def update_time_lapse_us_temperature_txt(self, temperature, temperature_error):
        self._time_lapse_us_temperature_txt.setText('{0:.0f} K &plusmn; {1:.0f}'.format(temperature,
                                                                                        temperature_error),
                                                    size='16pt',
                                                    color=colors['upstream'],
                                                    justify='right')

    def update_time_lapse_combined_temperature_txt(self, temperature, temperature_error):
        self._time_lapse_combined_temperature_txt.setText('{0:.0f} K &plusmn; {1:.0f}'.format(temperature,
                                                                                              temperature_error),
                                                          size='30pt',
                                                          color=colors['combined'])

    def set_mode(self, mode):
        """Show/hide the us-side subplot and retitle the ds subplot for single-sided mode."""
        dual = mode == 'dual'
        self._pg_us_layout_widget.setVisible(dual)
        # Equal-width columns in dual; collapse the empty column in single.
        # Both stretches must be set explicitly — leaving col 0 at default (0)
        # while col 1 is 1 makes col 1 hog all extra space.
        self._plots_widget_layout.setColumnStretch(0, 1)
        self._plots_widget_layout.setColumnStretch(1, 1 if dual else 0)
        if dual:
            self._ds_plot.setTitle("Downstream", color=QColor(colors['downstream']), size='20pt')
        else:
            self._ds_plot.setTitle("Temperature", color=QColor(colors['downstream']), size='20pt')
        # Time-lapse labels: hide the us column in single mode.
        if dual:
            self._time_lapse_us_temperature_txt.setText('', size='16pt')
        else:
            self._time_lapse_us_temperature_txt.setText('', size='16pt')

    def save_graph(self, ds_filename, us_filename):
        QtWidgets.QApplication.processEvents()
        if ds_filename.endswith('.png'):
            exporter = ImageExporter(self._ds_plot)
            exporter.export(ds_filename)
            exporter = ImageExporter(self._us_plot)
            exporter.export(us_filename)
        elif ds_filename.endswith('.svg'):
            exporter = SVGExporter(self._ds_plot)
            exporter.export(ds_filename)
            exporter = SVGExporter(self._us_plot)
            exporter.export(us_filename)
        QtWidgets.QApplication.processEvents()


class IntensityIndicator(pg.GraphicsWidget):
    def __init__(self):
        pg.GraphicsWidget.__init__(self)
        self.outside_rect = QtWidgets.QGraphicsRectItem(0, 0, 100, 100)
        self.inside_rect = QtWidgets.QGraphicsRectItem(0, 0, 50, 50)

        self._layout = QtWidgets.QGraphicsGridLayout()

        self.outside_rect.setPen(pg.mkPen(color=(255, 255, 255), width=1))
        self.inside_rect.setBrush(QtGui.QBrush(QtGui.QColor(0, 255, 0, 150)))

        self.__parent = None
        self.__parentAnchor = None
        self.__itemAnchor = None
        self.__offset = (0, 0)

        self.inside_rect.setParentItem(self)
        self.outside_rect.setParentItem(self)
        self.inside_rect.setZValue(-100000)
        self.outside_rect.setZValue(200)

        self._intensity_level = 0

    def setParentItem(self, parent):
        pg.GraphicsWidget.setParentItem(self, parent)
        parent = self.parentItem()
        self.__parent = parent
        parent.geometryChanged.connect(self.__geometryChanged)
        self.__geometryChanged()

    def __geometryChanged(self):
        if self.__parent is None:
            return

        bounding_rect = self.__parent.vb.boundingRect()
        title_label_height = self.__parent.titleLabel.boundingRect().height()
        bar_width = 12
        self.outside_rect.setRect(1,
                                  title_label_height + 1,
                                  bar_width,
                                  bounding_rect.height())

        if self._intensity_level < 0.8:
            set_color = QtGui.QColor(0, 255, 0, 150)
        else:
            set_color = QtGui.QColor(255, 0, 0, 150)
        self.inside_rect.setRect(1,
                                 title_label_height + bounding_rect.height() * (1 - self._intensity_level) + 1,
                                 bar_width,
                                 bounding_rect.height() * self._intensity_level)
        self.inside_rect.setBrush(QtGui.QBrush(set_color))

    def set_intensity(self, intensity):
        # Clamp to [0, 1]; anything above 1 would draw the bar past the
        # plot rect and cross the 0.8 red threshold on an out-of-scale
        # ratio (e.g. summed data against a stale uint16 full-scale).
        try:
            v = float(intensity)
        except (TypeError, ValueError):
            v = 0.0
        self._intensity_level = max(0.0, min(1.0, v))
        self.__geometryChanged()


        
class CustomViewBox(pg.ViewBox):  
    plotMouseCursorSignal = pyqtSignal(float)
    plotMouseCursor2Signal = pyqtSignal(float)  
    def __init__(self, *args, **kwds):
        super().__init__()
        self.cursor_signals = [self.plotMouseCursorSignal, self.plotMouseCursor2Signal]
        self.setMouseMode(self.RectMode)
        self.enableAutoRange(self.XYAxes, True)
        self.cursorPoint = 0
    

    ## reimplement right-click to zoom out
    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            #self.enableAutoRange(self.XYAxes, True)    
            
            self.enableAutoRange(enable=1) 
        elif ev.button() == QtCore.Qt.MouseButton.LeftButton: 
            pos = ev.pos()  ## using signal proxy turns original arguments into a tuple
            mousePoint = self.mapSceneToView(pos)

            mptx = mousePoint.x()
            self.cursorPoint=mptx

            self.plotMouseCursorSignal.emit(self.cursorPoint) 
        ev.accept()   

