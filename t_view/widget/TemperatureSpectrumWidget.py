# -*- coding: utf8 -*-
# T-View - GUI program for analysis of thermal spectra during
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

from PyQt5 import QtWidgets, QtCore, QtGui
#from PyQt5.QtWidgets import QSizePolicy
from PyQt5.QtGui import QColor
import pyqtgraph as pg
from pyqtgraph.exporters.ImageExporter import ImageExporter
from pyqtgraph.exporters.SVGExporter import SVGExporter
import numpy as np
#from .ModifiedPlotItem import ModifiedPlotItem
from PyQt5.QtCore import pyqtSignal

pg.setConfigOption('useOpenGL', False)
pg.setConfigOption('leftButtonPan', False)
pg.setConfigOption('background', 'k')
pg.setConfigOption('foreground', 'w')
pg.setConfigOption('antialias', True)

colors = {
    'data_pen': '#FFFFFF',
    'data_brush': '#FFFFFF',
    'fit_pen': 'r',
    'downstream': '#FFFF00',
    'upstream': '#FF9900',
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
        
        self._us_plot.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
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
        
        self._ds_plot.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._pg_ds_layout.addItem(self._ds_plot)
        self._pg_ds_layout_widget.addItem(self._pg_ds_layout)
        
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
        self._us_fit_item = pg.PlotDataItem(pen=pg.mkPen(colors['fit_pen'], width=3, conntect='finite',antialias=True))
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
        self._ds_fit_item = pg.PlotDataItem(pen=pg.mkPen(colors['fit_pen'], width=3, conntect='finite',antialias=True))
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
        if len(x)>0:
            mx = np.amax(y)*1.1
        else:
            mx = 1.1
        if mx < 2:
            mx = 2
        if mask is not None:
            #x[~mask] = np.nan
            y[~mask] = np.nan
        self._ds_view_box.setYRange(-1,mx)
        
        self._ds_data_item.setData(x, y)

    def plot_us_data(self, x, y, mask):
        if len(x)>0:
            mx = np.amax(y)*1.1
        else:
            mx = 1.1
        if mx < 2:
            mx = 2
        if mask is not None:
            #x[~mask] = np.nan
            y[~mask] = np.nan
        self._us_view_box.setYRange(-1,mx)
        self._us_data_item.setData(x, y)

    def plot_ds_masked_data(self, x, y, mask=None):
       
        if mask is not None:
            #x[~mask] = np.nan
            y[mask] = np.nan
   
        self._ds_masked_data_item.setData(x, y)

    def plot_us_masked_data(self, x, y, mask):
       
        if mask is not None:
            #x[~mask] = np.nan
            y[mask] = np.nan
     
        self._us_masked_data_item.setData(x, y)

    def plot_ds_fit(self, x, y):
        
        self._ds_fit_item.setData(x, y)

    def plot_us_fit(self, x, y):
        
        self._us_fit_item.setData(x, y)

    def plot_ds_time_lapse(self, x, y):
        self._time_lapse_ds_data_item.setData(x, y)

    def plot_us_time_lapse(self, x, y):
        self._time_lapse_us_data_item.setData(x, y)

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
                                          color='#33CC00',
                                          justify='right')
        self._us_intensity_indicator.set_intensity(float(roi_max) / format_max)

    def update_ds_roi_max_txt(self, roi_max, format_max=65536):
        self._ds_roi_max_txt_item.setText('Max Int {0:.0f}'.format(roi_max),
                                          size='18pt',
                                          color='#33CC00',
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
        self._intensity_level = intensity
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
        if ev.button() == QtCore.Qt.RightButton:
            #self.enableAutoRange(self.XYAxes, True)    
            
            self.enableAutoRange(enable=1) 
        elif ev.button() == QtCore.Qt.LeftButton: 
            pos = ev.pos()  ## using signal proxy turns original arguments into a tuple
            mousePoint = self.mapSceneToView(pos)

            mptx = mousePoint.x()
            self.cursorPoint=mptx

            self.plotMouseCursorSignal.emit(self.cursorPoint) 
        ev.accept()   