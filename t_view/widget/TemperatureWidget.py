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

from PyQt5 import QtCore, QtWidgets, QtGui
import os
from .TemperatureSpectrumWidget import TemperatureSpectrumWidget
from .RoiWidget import RoiWidget, IntegerTextField
from .Widgets import FileGroupBox
from .Widgets import OutputGroupBox, StatusBar
from .CustomWidgets import HorizontalSpacerItem, VerticalSpacerItem


from .. import style_path


class TemperatureWidget(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super(TemperatureWidget, self).__init__(*args, **kwargs)
        self._main_layout = QtWidgets.QVBoxLayout()
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self.control_widget =  TemperatureFileNavigation()
        self._main_layout.addWidget(self.control_widget)
        
        self.tab_widget = QtWidgets.QTabWidget()
        
        self.graph_widget = QtWidgets.QWidget()
        self._graph_widget_layout = QtWidgets.QVBoxLayout(self.graph_widget)
        self.temperature_spectrum_widget = TemperatureSpectrumWidget()
        self.graph_status_bar = StatusBar()
        self._graph_widget_layout.addWidget(self.temperature_spectrum_widget)
        self._graph_widget_layout.addWidget(self.graph_status_bar)
        
        self.settings_widget = QtWidgets.QWidget()
        self._settings_widget_layout = QtWidgets.QHBoxLayout(self.settings_widget)
        self._settings_widget_layout.setSpacing(0)
        self._settings_widget_layout.setContentsMargins(0, 0, 0, 0)
        
        self.roi_settings_widget = QtWidgets.QWidget()
        self._roi_settings_widget_layout = QtWidgets.QVBoxLayout(self.roi_settings_widget)
        self.roi_widget = RoiWidget(4, ['Downstream', 'Upstream', 'Background', 'Background'],
                                    roi_colors=[(255, 255, 0), (255, 140, 0),(155, 155, 0), (175,  110, 0)])
        self._roi_settings_widget_layout.addWidget(self.roi_widget)
        
        # scroll area stuff
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setStyleSheet("QScrollArea { border: 0px;}")
        self.scroll.setMaximumWidth(320)
        
        self.other_settings_widget = QtWidgets.QWidget()
        self.scroll.setWidget(self.other_settings_widget)
        self.scroll.setWidgetResizable(True)

        
        self.other_settings_widget.setMaximumWidth(300)
        self._other_settings_widget_layout = QtWidgets.QVBoxLayout(self.other_settings_widget)
        
        self.calibration_section = TemperatureCalibrationSection()
        
        
        self.settings_gb = SettingsGroupBox()
        self.epics_gb = EPICSGroupBox()
        
        self.roi_gb = self.roi_widget.roi_gb
        self.wl_range_widget = self.roi_widget.wl_range_widget

        self._other_settings_widget_layout.addWidget(self.settings_gb)
        self._other_settings_widget_layout.addWidget(self.wl_range_widget)
        self._other_settings_widget_layout.addWidget(self.roi_gb)
        self._other_settings_widget_layout.addWidget(self.calibration_section)
        self._other_settings_widget_layout.addWidget(self.epics_gb)
        self._other_settings_widget_layout.addSpacerItem(VerticalSpacerItem())
        
        self._settings_widget_layout.addWidget(self.roi_settings_widget)
        self._settings_widget_layout.addWidget(self.scroll)
        
        self.tab_widget.addTab(self.graph_widget, 'Temperature')
        self.tab_widget.addTab(self.settings_widget, 'Settings')
        
        
        self._main_layout.addWidget(self.tab_widget)
        
        

        self.setLayout(self._main_layout)

        self.style_widgets()
        self.create_shortcuts()

    def style_widgets(self):
        pass

    def create_shortcuts(self):
        self.load_data_file_btn = self.control_widget.file_gb.load_file_btn
        self.load_next_data_file_btn = self.control_widget.file_gb.load_next_file_btn
        self.load_previous_data_file_btn = self.control_widget.file_gb.load_previous_file_btn

        self.load_next_frame_btn = self.control_widget.file_gb.load_next_frame_btn
        self.load_previous_frame_btn = self.control_widget.file_gb.load_previous_frame_btn
        self.frame_num_txt = self.control_widget.file_gb.frame_txt
        self.frame_widget = self.control_widget.file_gb.frame_control_widget

        self.autoprocess_cb = self.control_widget.file_gb.autoprocess_cb
        self.filename_lbl = self.control_widget.file_gb.filename_lbl
        self.dirname_lbl = self.control_widget.file_gb.dirname_lbl
        self.mtime = self.control_widget.file_gb.mtime

        self.load_ds_calibration_file_btn = self.calibration_section.downstream_gb.load_file_btn
        self.load_us_calibration_file_btn = self.calibration_section.upstream_gb.load_file_btn
        self.ds_calibration_filename_lbl = self.calibration_section.downstream_gb.file_lbl
        self.us_calibration_filename_lbl = self.calibration_section.upstream_gb.file_lbl

        self.ds_temperature_rb = self.calibration_section.downstream_gb.temperature_rb
        self.us_temperature_rb = self.calibration_section.upstream_gb.temperature_rb
        self.ds_standard_rb = self.calibration_section.downstream_gb.standard_rb
        self.us_standard_rb = self.calibration_section.upstream_gb.standard_rb
        self.ds_load_standard_file_btn = self.calibration_section.downstream_gb.load_standard_btn
        self.us_load_standard_file_btn = self.calibration_section.upstream_gb.load_standard_btn
        self.ds_standard_filename_lbl = self.calibration_section.downstream_gb.standard_file_lbl
        self.us_standard_filename_lbl = self.calibration_section.upstream_gb.standard_file_lbl
        self.ds_temperature_txt = self.calibration_section.downstream_gb.temperature_txt
        self.us_temperature_txt = self.calibration_section.upstream_gb.temperature_txt

        self.load_setting_btn = self.settings_gb.load_setting_btn
        self.save_setting_btn = self.settings_gb.save_setting_btn

        self.save_data_btn = self.control_widget.output_gb.save_data_btn
        self.save_graph_btn = self.control_widget.output_gb.save_graph_btn

        self.settings_cb = self.settings_gb.settings_cb

        self.roi_img_item = self.roi_widget.img_widget.pg_img_item
        self.time_lapse_layout = self.temperature_spectrum_widget._pg_time_lapse_layout

        self.graph_mouse_pos_lbl = self.graph_status_bar.left_lbl
        self.graph_info_lbl = self.graph_status_bar.right_lbl

        self.setup_epics_pb = self.epics_gb. setup_epics_pb
        self.connect_to_epics_cb = self.epics_gb. connect_to_epics_cb


        self.browse_by_name_rb = self.control_widget.file_gb.browse_by_name_rb 
        self.browse_by_time_rb = self.control_widget.file_gb.browse_by_time_rb 
        

class TemperatureFileNavigation(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._layout = QtWidgets.QHBoxLayout()

        self.file_gb = FileGroupBox()
        self.output_gb = OutputGroupBox()
        
        self._layout.addWidget(self.file_gb)
        self._layout.addWidget(self.output_gb)

      
        self.setLayout(self._layout)
        


class EPICSGroupBox(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('EPICS')

        self._layout = QtWidgets.QHBoxLayout()
   
        self.setup_epics_pb = QtWidgets.QPushButton("Setup Epics")
        self.connect_to_epics_cb = QtWidgets.QCheckBox("Connect to Epics")
        self.connect_to_epics_cb.setLayoutDirection(QtCore.Qt.RightToLeft)
        self._layout.addWidget(self.setup_epics_pb)
        self._layout.addWidget(self.connect_to_epics_cb)
        
        self.setLayout(self._layout)
        self.setMaximumWidth(300)


class SettingsGroupBox(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('Settings save and restore')

        self._layout = QtWidgets.QVBoxLayout()
   
        self.settings_cb = QtWidgets.QComboBox()
        '''self.settings_cb.setMinimumWidth(250)
        self.settings_cb.setMaximumWidth(250)'''
        
        self._btns_layout = QtWidgets.QHBoxLayout()
        self.load_setting_btn = QtWidgets.QPushButton('Load')
        self.save_setting_btn = QtWidgets.QPushButton('Save')
        self._btns_layout.addWidget(self.load_setting_btn)
        self._btns_layout.addWidget(self.save_setting_btn)
        #self._btns_layout.addSpacerItem(HorizontalSpacerItem())

        self._layout.addWidget(self.settings_cb)
        self._layout.addLayout(self._btns_layout)


        self.setLayout(self._layout)
        self.setMaximumWidth(300)


class TemperatureCalibrationSection(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('Intensity calibration')
        self._layout = QtWidgets.QVBoxLayout()

        self.downstream_gb = CalibrationGB('Downstream', 'rgba(255, 255, 0, 255)')
        self.upstream_gb = CalibrationGB('Upstream', 'rgba(255, 140, 0, 255)')

        self._layout.addWidget(self.downstream_gb)
        self._layout.addWidget(self.upstream_gb)

        #self._layout.addSpacerItem(VerticalSpacerItem())
        self.setLayout(self._layout)
        self.setMaximumWidth(300)




class CalibrationGB(QtWidgets.QGroupBox):
    def __init__(self, title, color):
        super(CalibrationGB, self).__init__(title)

        self.color = color
        self._layout = QtWidgets.QGridLayout()
        self._layout.setVerticalSpacing(8)
        self._layout.setHorizontalSpacing(8)

        self.load_file_btn = QtWidgets.QPushButton('Load File')
        self.file_lbl = QtWidgets.QLabel('Select File...')

        self.temperature_txt = QtWidgets.QLineEdit('2000')
        self.temperature_txt.setMinimumWidth(70)
        self.temperature_unit_lbl = QtWidgets.QLabel('K')

        self.temperature_rb = QtWidgets.QRadioButton('Temperature')
        self.standard_rb = QtWidgets.QRadioButton('Standard Spectrum')
        self.load_standard_btn = QtWidgets.QPushButton('...')
        self.standard_file_lbl = QtWidgets.QLabel('Select File...')

        self._layout.addWidget(self.load_file_btn, 0, 0, 1, 3)
        self._layout.addWidget(self.file_lbl, 0, 3)
        self._layout.addWidget(self.temperature_txt, 1, 0, 1, 2)
        self._layout.addWidget(self.temperature_unit_lbl, 1, 2)
        self._layout.addWidget(self.temperature_rb, 1, 3)
        self._layout.addWidget(self.load_standard_btn, 2, 1, 1, 2)
        self._layout.addWidget(self.standard_rb, 2, 3)
        self._layout.addWidget(self.standard_file_lbl, 3, 3)

        self.setLayout(self._layout)
        self.style_widgets()
        self.set_stylesheet()

    def style_widgets(self):

        self.temperature_txt.setValidator(QtGui.QDoubleValidator())
        self.temperature_txt.setAlignment(QtCore.Qt.AlignRight)
        self.temperature_rb.toggle()

    def set_stylesheet(self):
        style_str = "QGroupBox { color: %s; border: 1px solid %s}" % (self.color, self.color)
        self.setStyleSheet(style_str)


class SetupEpicsDialog(QtWidgets.QDialog):
    """
    Dialog for inputting map positions manually
    """

    def __init__(self, parent):
        super(SetupEpicsDialog, self).__init__()

        self._parent = parent
        self._create_widgets()
        self._layout_widgets()
        self._style_widgets()

        self._connect_widgets()
        self.approved = False

    def _create_widgets(self):
        self.us_temp_lbl = QtWidgets.QLabel("US Temperature PV")
        self.us_temp_lbl.setMinimumWidth(200)
        self.ds_temp_lbl = QtWidgets.QLabel("DS Temperature PV")
     
        self.temperature_file_directory_pv_lbl = QtWidgets.QLabel("T File Directory PV")

        self.us_temp_txt = QtWidgets.QLineEdit()
        self.us_temp_txt.setMinimumWidth(200)
        self.ds_temp_txt = QtWidgets.QLineEdit()
   
        self.temperature_file_directory_pv_txt = QtWidgets.QLineEdit()

        self.us_temp_txt.setToolTip("Enter the complete PV, or None")
        self.ds_temp_txt.setToolTip("Enter the complete PV, or None")
     
        self.temperature_file_directory_pv_txt.setToolTip("Enter the PV which points to the T files folder, or None")

        self.ok_btn = QtWidgets.QPushButton("Done")
        self.cancel_btn = QtWidgets.QPushButton("Cancel")

    def _layout_widgets(self):
        self._grid_layout = QtWidgets.QGridLayout()

        self._grid_layout.addWidget(self.us_temp_lbl, 0, 0)
        self._grid_layout.addWidget(self.ds_temp_lbl, 1, 0)

        self._grid_layout.addWidget(self.temperature_file_directory_pv_lbl, 2, 0)
        self._grid_layout.addWidget(self.us_temp_txt, 0, 1)
        self._grid_layout.addWidget(self.ds_temp_txt, 1, 1)

        self._grid_layout.addWidget(self.temperature_file_directory_pv_txt, 2, 1)

        self._grid_layout.addWidget(self.ok_btn, 3, 0)
        self._grid_layout.addWidget(self.cancel_btn, 3, 1)

        self.setLayout(self._grid_layout)

    def _style_widgets(self):
        self.ok_btn.setEnabled(False)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)

        file = open(os.path.join(style_path, "stylesheet.qss"))
        stylesheet = file.read()
        self.setStyleSheet(stylesheet)
        file.close()

    def _connect_widgets(self):
        """
        Connecting actions to slots.
        """
        self.ok_btn.clicked.connect(self.accept_epics_setup)
        self.cancel_btn.clicked.connect(self.reject_epics_setup)

    def accept_epics_setup(self):
        self.approved = True
        self.accept()

    def reject_epics_setup(self):
        self.approved = False
        self.reject()

    @property
    def us_temp_pv(self):
        return str(self.us_temp_txt.text())

    @us_temp_pv.setter
    def us_temp_pv(self, pv):
        self.us_temp_txt.setText(pv)

    @property
    def ds_temp_pv(self):
        return str(self.ds_temp_txt.text())

    @ds_temp_pv.setter
    def ds_temp_pv(self, pv):
        self.ds_temp_txt.setText(pv)

    @property
    def temperature_file_folder_pv(self):
        return str(self.temperature_file_directory_pv_txt.text())

    @temperature_file_folder_pv.setter
    def temperature_file_folder_pv(self, pv):
        self.temperature_file_directory_pv_txt.setText(pv)

    def exec_(self):
        """
        Overwriting the dialog exec_ function to center the widget in the parent window before execution.
        """
        parent_center = self._parent.window().mapToGlobal(self._parent.window().rect().center())
        self.move(parent_center.x() - 101, parent_center.y() - 48)
        super(SetupEpicsDialog, self).exec_()
