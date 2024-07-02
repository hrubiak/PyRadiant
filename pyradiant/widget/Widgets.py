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

from PyQt6 import QtWidgets, QtCore, QtGui

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QStyle
import os
from .CustomWidgets import HorizontalSpacerItem, VerticalSpacerItem
from .. import resources_path

class FileGroupBox(QtWidgets.QGroupBox):
    def __init__(self, *args):
        super().__init__()
        self.setTitle('File')
        self._main_layout = QtWidgets.QVBoxLayout()
        self._main_layout.setContentsMargins(8, 8, 8, 8)
        self._main_layout.setSpacing(8)

        self.create_file_control_widget()

        self._main_layout.addWidget(self.file_control_widget)
        
        self._second_row_widget = QtWidgets.QWidget()
        self._second_row_widget_layout = QtWidgets.QHBoxLayout(self._second_row_widget)
        self._second_row_widget_layout.setContentsMargins(0, 0, 0, 0)
        self._second_row_widget_layout.setSpacing(0)
        self._second_row_widget_layout.addWidget(self.dirname_lbl)

        self._second_row_widget_layout.addWidget(QtWidgets.QLabel(os.path.sep))
        self._second_row_widget_layout.addWidget(self.filename_lbl)
        self._second_row_widget_layout.addSpacerItem(HorizontalSpacerItem())
        self._second_row_widget_layout.addWidget(self.frame_control_widget)
        self._second_row_widget_layout.addSpacerItem(HorizontalSpacerItem())
        self.mtime = QtWidgets.QLabel('')
        self._second_row_widget_layout.addWidget(self.mtime)
        self._main_layout.addWidget(self._second_row_widget)
        
        self.frame_control_widget.hide()
        
        self.setLayout(self._main_layout)

    def create_file_control_widget(self):
        self.file_control_widget = QtWidgets.QWidget()

        self._file_control_layout = QtWidgets.QHBoxLayout()
        self._file_control_layout.setContentsMargins(0, 0, 0, 0)
        
        

        self.load_file_btn = QtWidgets.QPushButton("Load")
        load_file_pixmap = QStyle.StandardPixmap.SP_FileDialogStart
        load_file_icon = self.style().standardIcon(load_file_pixmap)  
        self.load_file_btn.setIcon(load_file_icon)

        self.load_next_file_btn = QtWidgets.QPushButton()
        next_pixmap = QStyle.StandardPixmap.SP_ArrowRight
        next_icon = self.style().standardIcon(next_pixmap)  
        self.load_next_file_btn.setIcon(next_icon)
        self.load_previous_file_btn = QtWidgets.QPushButton()
        prev_pixmap = QStyle.StandardPixmap.SP_ArrowLeft
        prev_icon = self.style().standardIcon(prev_pixmap)  
        self.load_previous_file_btn.setIcon(prev_icon)
        self.browse_by_name_rb = QtWidgets.QRadioButton()
        browse_by_name_icon = QIcon()
        browse_by_name_icon.addFile(os.path.join(resources_path,'style','sort_by_alpha.svg'))
        self.browse_by_name_rb.setIcon(browse_by_name_icon)
        self.browse_by_name_rb.setChecked(True)
        self.browse_by_time_rb = QtWidgets.QRadioButton()
        browse_by_time_icon = QIcon()
        browse_by_time_icon.addFile(os.path.join(resources_path,'style','watch.svg'))
        self.browse_by_time_rb.setIcon(browse_by_time_icon)

        self.autoprocess_cb = QtWidgets.QCheckBox('auto')
        autoprocess_icon = QIcon()
        autoprocess_icon.addFile(os.path.join(resources_path,'style','thermostat_auto.svg'))
        self.autoprocess_cb.setIcon(autoprocess_icon)

        self.autoprocess_lbl = QtWidgets.QLabel('')
        self.filename_lbl = QtWidgets.QLabel('file')
        self.dirname_lbl = QtWidgets.QLabel('folder')
        

        self._file_control_layout.addWidget(self.load_file_btn)
        self._file_control_layout.addWidget(self.load_previous_file_btn)
        self._file_control_layout.addWidget(self.load_next_file_btn)
        self._file_control_layout.addWidget(self.browse_by_name_rb)
        self._file_control_layout.addWidget(self.browse_by_time_rb)
        self._file_control_layout.addWidget(self.autoprocess_cb)
        self._file_control_layout.addWidget(self.autoprocess_lbl)
        
        self._file_control_layout.addSpacerItem(HorizontalSpacerItem())
        
        self.data_history_btn = QtWidgets.QPushButton("T Log")
        data_history_icon = QIcon()
        data_history_icon.addFile(os.path.join(resources_path,'style','timeline.svg'))
        self.data_history_btn.setIcon(data_history_icon)

        self._file_control_layout.addWidget(self.data_history_btn)
        
        
        self.frame_control_widget = QtWidgets.QWidget()

        self._frame_control_layout = QtWidgets.QHBoxLayout()
        self._frame_control_layout.setContentsMargins(0, 0, 0, 0)
        self._frame_control_layout.setSpacing(5)
        
    
        self.load_previous_frame_btn = QtWidgets.QPushButton('<')
        self.load_next_frame_btn = QtWidgets.QPushButton('>')
        self.frame_txt = QtWidgets.QLineEdit('100')
        self.frame_txt.setMaximumWidth(50)

        self._frame_control_layout.addWidget(self.load_previous_frame_btn)
        self._frame_control_layout.addWidget(self.frame_txt)
        self._frame_control_layout.addWidget(self.load_next_frame_btn)
       
        self.frame_control_widget.setLayout(self._frame_control_layout)
        
        #self.timelapse_btn = QtWidgets.QPushButton('Time Lapse')
        #self._frame_control_layout.addWidget(self.timelapse_btn)
        self._frame_control_layout.addSpacerItem(HorizontalSpacerItem())

        self.load_previous_frame_btn.setMaximumWidth(25)
        self.load_next_frame_btn.setMaximumWidth(25)
        
        self.file_control_widget.setLayout(self._file_control_layout)




class OutputGroupBox(QtWidgets.QGroupBox, object):
    def __init__(self, *args, **kwargs):
        super(OutputGroupBox, self).__init__("Output", *args, **kwargs)

        self.create_widgets()
        self.create_layout()
        #self.style_widgets()

    def create_widgets(self):
        
        self.save_data_btn = QtWidgets.QPushButton("Save Data")
        save_data_icon = QIcon()
        save_data_icon.addFile(os.path.join(resources_path,'style','rubric.svg'))
        self.save_data_btn.setIcon(save_data_icon)

        self.save_graph_btn = QtWidgets.QPushButton("Save Graph")
        save_graph_icon = QIcon()
        save_graph_icon.addFile(os.path.join(resources_path,'style','filter.svg'))
        self.save_graph_btn.setIcon(save_graph_icon)

    def create_layout(self):
        self._layout = QtWidgets.QHBoxLayout()
        
        self._layout.addWidget(self.save_data_btn)
        self._layout.addWidget(self.save_graph_btn)
        self.setLayout(self._layout)

    '''def style_widgets(self):
        self.save_data_btn.setFlat(True)
        self.save_graph_btn.setFlat(True)'''


class StatusBar(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super(StatusBar, self).__init__(*args, **kwargs)

        self.create_widgets()
        self.create_layout()

    def create_widgets(self):
        self.left_lbl = QtWidgets.QLabel()
        self.right_lbl = QtWidgets.QLabel()
 
    def create_layout(self):
        
        self._status_layout = QtWidgets.QHBoxLayout()
        self._status_layout.setContentsMargins(0,0,0,0)
        self._status_layout.setSpacing(0)
        self._status_layout.addWidget(self.left_lbl)
        self._status_layout.addSpacerItem(HorizontalSpacerItem())
        self._status_layout.addWidget(self.right_lbl)

        self.setLayout(self._status_layout)


def open_file_dialog(parent_widget, caption, directory, filter=None):
    filename = QtWidgets.QFileDialog.getOpenFileName(parent_widget, caption=caption,
                                                     directory=directory,
                                                     filter=filter)
    if isinstance(filename, tuple):  # PyQt6 returns a tuple...
        return str(filename[0])
    return str(filename)

def open_files_dialog(parent_widget, caption, directory, filter=None):
    filename = QtWidgets.QFileDialog.getOpenFileNames(parent_widget, caption=caption,
                                                      directory=directory,
                                                      filter=filter)
    if isinstance(filename, tuple):  # PyQt6 returns a tuple...
        return filename[0]
    return filename

def save_file_dialog(parent_widget, caption, directory, filter=None):
    filename = QtWidgets.QFileDialog.getSaveFileName(parent_widget, caption,
                                                     directory=directory,
                                                     filter=filter)
    if isinstance(filename, tuple):  # PyQt6 returns a tuple...
        return str(filename[0])
    return str(filename)
