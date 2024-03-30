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

from functools import partial

import numpy as np

from PyQt5 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg
from pyqtgraph.graphicsItems.ROI import Handle
from pyqtgraph import ColorMap, HistogramLUTItem
from .CustomWidgets import HorizontalSpacerItem, VerticalSpacerItem, DoubleSpinBoxAlignRight 
#from .HistogramLUTItem import HistogramLUTItem

from .Widgets import StatusBar

#pg.setConfigOption('useOpenGL', False)
pg.setConfigOption('leftButtonPan', False)
pg.setConfigOption('background', (20, 20, 20))
#pg.setConfigOption('foreground', 'b')
pg.setConfigOption('antialias', True)


class RoiWidget(QtWidgets.QWidget):
    rois_changed = QtCore.pyqtSignal(list)
    wl_range_changed = QtCore.pyqtSignal(list)

    def __init__(self, roi_num=1, roi_titles=('',), roi_colors=((255, 255, 0)), *args, **kwargs):
        super(RoiWidget, self).__init__(*args, **kwargs)
        self.roi_num = roi_num
        self.roi_titles = roi_titles
        self.roi_colors = roi_colors

        self._main_vertical_layout = QtWidgets.QVBoxLayout()
        self._main_vertical_layout.setContentsMargins(0, 0, 0, 0)
        self._main_vertical_layout.setSpacing(5)

        self.img_widget = RoiImageWidget(roi_num=roi_num, roi_colors=roi_colors)

        self.wl_range_widget = wavelengthRangeGB()
        
        self.status_bar = StatusBar()

        self.roi_gb = QtWidgets.QGroupBox('ROI')
        self.roi_gb.setMaximumWidth(300)
        self._roi_v_bs_layout = QtWidgets.QVBoxLayout(self.roi_gb)
        self._roi_gbs_layout = QtWidgets.QGridLayout()
        self._roi_gbs_layout.setSpacing(2)
        self.roi_gbs = []
        self.create_roi_gbs()
        self._roi_v_bs_layout.addLayout(self._roi_gbs_layout)
        #self._roi_gbs_layout.addSpacerItem(HorizontalSpacerItem())

        self._main_vertical_layout.addWidget(self.img_widget)
        #self._main_vertical_layout.addWidget(self.roi_gb)
        self._main_vertical_layout.addWidget(self.status_bar)

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

        self.img_widget.blockSignals(True)
        
        self.img_widget.update_roi(ind, roi_list)
        self.img_widget.blockSignals(False)
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

    def add_item(self, pg_item):
        self.img_widget.pg_viewbox.addItem(pg_item)


class wavelengthRangeGB(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('Wavelength range (nm)')
        
        self._layout = QtWidgets.QGridLayout(self)
        self._layout.addWidget(QtWidgets.QLabel("Start:"),0,0)
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
        self.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.setValidator(QtGui.QIntValidator())

class IntegerSpinBox(DoubleSpinBoxAlignRight):
    def __init__(self, *args, **kwargs):
        super().__init__()
    
        self.setDecimals(0)
        self.setMinimum(0)
        self.setMaximum(10000)
        self.setSingleStep(1)
        

class RoiImageWidget(QtWidgets.QWidget):
    mouse_moved = QtCore.pyqtSignal(float, float)
    #mouse_left_clicked = QtCore.pyqtSignal(float, float)
    #mouse_left_double_clicked = QtCore.pyqtSignal(float, float)

    rois_changed = QtCore.pyqtSignal(list)

    def __init__(self, roi_num=1, roi_colors=((255, 255, 0)), *args, **kwargs):
        super(RoiImageWidget, self).__init__(*args, **kwargs)
        self.roi_num = roi_num
        self.roi_colors = roi_colors
        self.rect = None
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

        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.pg_widget)
        self.setLayout(self._layout)
        self.add_rois()
        self.modify_mouse_behavior()

    def set_wavelength_calibration(self, rect):
        
        if self.rect != rect :
            self.rect = rect
            self.pg_img_item.setRect(*rect)

    def add_rois(self):
        self.rois = []
        for ind in range(self.roi_num):
            self.rois.append(ImgROI((25, 25), (150, 150),
                                    pen=pg.mkPen(self.roi_colors[ind], width=2),
                                    active_pen=pg.mkPen('r', width=3)))
            self.pg_viewbox.addItem(self.rois[-1])
            self.rois[-1].sigRegionChanged.connect(self.roi_changed)

    def get_roi_limits(self):
        roi_limits = []
        for roi in self.rois:
            roi_pos_x = roi.pos()[0]
            roi_size_x = roi.size()[0]
            if self.rect != None:
                roi_pos_x =   (roi_pos_x-self.rect[0]) *1340 /self.rect[2]
                roi_size_x = roi_size_x / self.rect[2]*1340
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
        if self.rect != None:
            pos[0] = int(round(self.rect[0] + pos[0] /1340 *self.rect[2]))
            size[0] = int(round(size[0] * (self.rect[2]/1340)))
        
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
        x_max, y_max = data.shape
        self.pg_viewbox.setLimits(xMin=0, xMax=x_max,
                                  yMin=0, yMax=y_max)

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
        if ev.button() == QtCore.Qt.RightButton or \
                (ev.button() == QtCore.Qt.LeftButton and
                         ev.modifiers() & QtCore.Qt.ControlModifier):
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
            if ev.acceptDrags(QtCore.Qt.LeftButton):
                hover = True
            for btn in [QtCore.Qt.LeftButton, QtCore.Qt.RightButton, QtCore.Qt.MidButton]:
                if int(self.acceptedMouseButtons() & btn) > 0 and ev.acceptClicks(btn):
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
            if ev.acceptDrags(QtCore.Qt.LeftButton):
                hover = True
            for btn in [QtCore.Qt.LeftButton, QtCore.Qt.RightButton, QtCore.Qt.MidButton]:
                if int(self.acceptedMouseButtons() & btn) > 0 and ev.acceptClicks(btn):
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
