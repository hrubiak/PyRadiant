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

class historyPlotWidget(pg.GraphicsLayoutWidget):
    def __init__(self, scale_label,*args,  **kwargs):
        super().__init__()

        self._pg_layout = pg.GraphicsLayout()
        self._pg_layout.setContentsMargins(0, 0, 0, 0)
        self._pg_layout.layout.setVerticalSpacing(0)
        
        self._time_lapse_plot = pg.PlotItem()
        self._time_lapse_plot.showAxis('top', show=True)
        self._time_lapse_plot.showAxis('right', show=True)
        self._time_lapse_plot.getAxis('top').setStyle(showValues=False)
        self._time_lapse_plot.getAxis('right').setStyle(showValues=False)
        self._time_lapse_plot.getAxis('bottom').setStyle(showValues=True)
        self._time_lapse_plot.setLabel('left', scale_label)

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

        self.addItem(self._pg_layout)

        self.create_data_items()


    def create_data_items(self):
        self._time_lapse_ds_data_item = pg.PlotDataItem(
            pen=pg.mkPen(QColor(colors['downstream']), width=1),
            brush=pg.mkBrush(QColor(colors['downstream'])),
            symbolPen=pg.mkPen(QColor(colors['downstream']), width=1),
            symbolBrush=pg.mkBrush(QColor(colors['downstream'])),
            size=1,
            symbolSize=5,
            symbol='s'
        )
        self._time_lapse_us_data_item = pg.PlotDataItem(
            pen=pg.mkPen(QColor(colors['upstream']), width=1),
            brush=pg.mkBrush(QColor(colors['upstream'])),
            symbolPen=pg.mkPen(QColor(colors['upstream']), width=1),
            symbolBrush=pg.mkBrush(QColor(colors['upstream'])),
            size=1,
            symbolSize=5,
            symbol='s'
        )

        self._time_lapse_plot.addItem(self._time_lapse_ds_data_item)
        self._time_lapse_plot.addItem(self._time_lapse_us_data_item)

    def plot_ds_time_lapse(self, x, y):
        self._time_lapse_ds_data_item.setData(x, y)

    def plot_us_time_lapse(self, x, y):
        self._time_lapse_us_data_item.setData(x, y)

    def update_time_lapse_ds_temperature_txt(self, txt):
        self._time_lapse_ds_temperature_txt.setText(txt,
                                                    size='16pt',
                                                    color=colors['downstream'],
                                                    justify='left')

    def update_time_lapse_us_temperature_txt(self, txt):
        self._time_lapse_us_temperature_txt.setText(txt,
                                                    size='16pt',
                                                    color=colors['upstream'],
                                                    justify='right')
        

class dataHistoryWidget(QtWidgets.QWidget):
    file_dragged_in = QtCore.pyqtSignal(list)
    def __init__(self, *args, **kwargs):
        super().__init__()

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self.file_navigation_widget = QtWidgets.QWidget()
        #self.file_navigation_widget.setStyleSheet("QWidget { background: #000000; color: #F1F1F1}")
        self._file_navigation_widget_layout = QtWidgets.QHBoxLayout(self.file_navigation_widget)
        self._file_navigation_widget_layout.setSpacing(5)
        self.btn_lbl = QtWidgets.QLabel("Log file ")
        self.load_data_log_file_btn = QtWidgets.QPushButton('Load')
        self.load_data_log_file_lbl = QtWidgets.QLabel('')
        self._file_navigation_widget_layout.addWidget(self.btn_lbl)
        #self._file_navigation_widget_layout.addWidget(self.load_data_log_file_btn)
        self._file_navigation_widget_layout.addWidget(self.load_data_log_file_lbl)
        self._file_navigation_widget_layout.addSpacerItem(HorizontalSpacerItem())
        self.clear_data_log_file_btn = QtWidgets.QPushButton('Clear Log')
        clear_data_icon = QIcon()
        clear_data_icon.addFile(os.path.join(resources_path,'style','delete_forever.svg'))
        self.clear_data_log_file_btn.setIcon(clear_data_icon)

        self._file_navigation_widget_layout.addWidget(self.clear_data_log_file_btn)

        self._layout.addWidget(self.file_navigation_widget)

        self.temperatures_plot_widget = historyPlotWidget(scale_label="T (K)")
        self.static_temperature_plot_widget = historyPlotWidget(scale_label="Scaling")

        self.plot_tab_widget = QtWidgets.QTabWidget()
        self.plot_tab_widget.addTab(self.temperatures_plot_widget,'Moving')
        self.plot_tab_widget.addTab(self.static_temperature_plot_widget, 'Static')
        self._layout.addWidget(self.plot_tab_widget)



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
        """
        Drop files directly onto the widget

        File locations are stored in fname
        :param e:
        :return:
        """
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