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

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStyle
from PyQt6.QtGui import QIcon
import os
from .TemperatureSpectrumWidget import TemperatureSpectrumWidget
from .RoiWidget import RoiWidget, IntegerTextField
from .Widgets import FileGroupBox
from .Widgets import OutputGroupBox, StatusBar
from .CustomWidgets import HorizontalSpacerItem, VerticalSpacerItem

from .. import resources_path



class TemperatureWidget(QtWidgets.QWidget):
    file_dragged_in = QtCore.pyqtSignal(list)
    def __init__(self, *args, **kwargs):
        super().__init__()

        self.config_widget = args[0].configuration_widget
        self._main_layout = QtWidgets.QVBoxLayout()
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self.control_widget =  TemperatureFileNavigation()
        self._main_layout.addWidget(self.control_widget)
        

        self.left_widget = QtWidgets.QWidget()
        self._left_layout = QtWidgets.QVBoxLayout(self.left_widget)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(0)
        self.left_widget.resize(600,600)

        self.splitter_horizontal = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self._main_layout.addWidget(self.splitter_horizontal)

        
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
                                    roi_colors=[(255, 215, 0), (255, 111, 97),(151, 135, 50), (157,  60, 50)])
        self._roi_settings_widget_layout.addWidget(self.roi_widget)
        
        # scroll area stuff
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setStyleSheet("QScrollArea { border: 0px;}")
        self.scroll_area.setMaximumWidth(320)
        self.scroll_area.setMinimumWidth(320)
        
        self.other_settings_widget = QtWidgets.QWidget()
        self.scroll_area.setWidget(self.other_settings_widget)
        self.scroll_area.setWidgetResizable(True)

        
        self.other_settings_widget.setMaximumWidth(300)
        self._other_settings_widget_layout = QtWidgets.QVBoxLayout(self.other_settings_widget)

        self.side_bar_close_btn_widget = QtWidgets.QWidget()
        self._side_bar_close_btn_widget_layout = QtWidgets.QHBoxLayout(self.side_bar_close_btn_widget)
        self._side_bar_close_btn_widget_layout.setSpacing(0)
        self._side_bar_close_btn_widget_layout.setContentsMargins(0, 0, 0, 0)
        self.side_bar_close_btn = QtWidgets.QPushButton()
        side_bar_close_icon = QIcon()
        side_bar_close_icon.addFile(os.path.join(resources_path,'style','right_panel_close.svg'))
        self.side_bar_close_btn.setIcon(side_bar_close_icon)
        self._side_bar_close_btn_widget_layout.addWidget(self.side_bar_close_btn)
        self._side_bar_close_btn_widget_layout.addSpacerItem(HorizontalSpacerItem())
        
        self.calibration_section = TemperatureCalibrationSection()
        
        self.t_function_type_section = TemperatureFitSettings()
        
        self.settings_gb = SettingsGroupBox()
        self.epics_gb = EPICSGroupBox()
        
        self.roi_gb = self.roi_widget.roi_gb
        self.wl_range_widget = self.roi_widget.wl_range_widget

        
        self._other_settings_widget_layout.addWidget(self.side_bar_close_btn_widget)
        self._other_settings_widget_layout.addWidget(self.config_widget)
        self._other_settings_widget_layout.addWidget(self.settings_gb)
        self._other_settings_widget_layout.addWidget(self.wl_range_widget)
        self._other_settings_widget_layout.addWidget(self.roi_gb)
        self._other_settings_widget_layout.addWidget(self.calibration_section)
        self._other_settings_widget_layout.addWidget(self.t_function_type_section)
        self._other_settings_widget_layout.addWidget(self.epics_gb)
        self._other_settings_widget_layout.addSpacerItem(VerticalSpacerItem())
        
        self._settings_widget_layout.addWidget(self.roi_settings_widget)


        temperature_tab_icon = QIcon()
        temperature_tab_icon.addFile(os.path.join(resources_path,'style','device_thermostat.svg'))
        spectrum_tab_icon = QIcon()
        spectrum_tab_icon.addFile(os.path.join(resources_path,'style','infrared.svg'))
        
        self.tab_widget.addTab(self.graph_widget,temperature_tab_icon, 'Temperature')
        self.tab_widget.addTab(self.settings_widget,spectrum_tab_icon, 'Spectrum')
        
        
        self._left_layout.addWidget(self.tab_widget)

        self.splitter_horizontal.addWidget(self.left_widget)
        self.splitter_horizontal.addWidget(self.scroll_area)
        
        

        self.setLayout(self._main_layout)

        self.style_widgets()
        self.create_shortcuts()

        self.setAcceptDrops(True) 

        self.side_bar_close_btn.clicked.connect(self.hide_right_panel)

    def hide_right_panel(self):
        self.splitter_horizontal.setSizes([self.splitter_horizontal.width(), 0])
        
    def show_right_panel(self):    
        self.splitter_horizontal.setSizes([self.splitter_horizontal.width() - self.scroll_area.width(), self.scroll_area.width()])

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
        self.autoprocess_lbl = self.control_widget.file_gb.autoprocess_lbl
        self.filename_lbl = self.control_widget.file_gb.filename_lbl
        self.dirname_lbl = self.control_widget.file_gb.dirname_lbl
        self.mtime = self.control_widget.file_gb.mtime

        self.load_ds_calibration_file_btn = self.calibration_section.downstream_gb.load_file_btn
        self.load_us_calibration_file_btn = self.calibration_section.upstream_gb.load_file_btn
        self.ds_calibration_filename_lbl = self.calibration_section.downstream_gb.file_lbl
        self.us_calibration_filename_lbl = self.calibration_section.upstream_gb.file_lbl
        self.ds_calibration_start_frame = self.calibration_section.downstream_gb.start_frame
        self.us_calibration_start_frame = self.calibration_section.upstream_gb.start_frame
        self.ds_calibration_end_frame = self.calibration_section.downstream_gb.end_frame
        self.us_calibration_end_frame = self.calibration_section.upstream_gb.end_frame

        self.ds_temperature_rb = self.calibration_section.downstream_gb.temperature_rb
        self.us_temperature_rb = self.calibration_section.upstream_gb.temperature_rb
        self.ds_standard_rb = self.calibration_section.downstream_gb.standard_rb
        self.us_standard_rb = self.calibration_section.upstream_gb.standard_rb
        self.ds_load_standard_file_btn = self.calibration_section.downstream_gb.load_standard_btn
        self.us_load_standard_file_btn = self.calibration_section.upstream_gb.load_standard_btn

        self.ds_save_standard_file_btn = self.calibration_section.downstream_gb.save_standard_btn
        self.us_save_standard_file_btn = self.calibration_section.upstream_gb.save_standard_btn

        self.ds_standard_filename_lbl = self.calibration_section.downstream_gb.standard_file_lbl
        self.us_standard_filename_lbl = self.calibration_section.upstream_gb.standard_file_lbl
        self.ds_temperature_txt = self.calibration_section.downstream_gb.temperature_txt
        self.us_temperature_txt = self.calibration_section.upstream_gb.temperature_txt

        self.load_setting_btn = self.settings_gb.load_setting_btn
        self.save_setting_btn = self.settings_gb.save_setting_btn

        self.save_data_btn = self.control_widget.output_gb.save_data_btn
        self.save_graph_btn = self.control_widget.output_gb.save_graph_btn
        self.data_history_btn = self.control_widget.file_gb.data_history_btn
        self.two_color_btn = self.control_widget.file_gb.two_color_btn

        self.settings_cb = self.settings_gb.settings_cb

        self.roi_img_item = self.roi_widget.img_widget.pg_img_item
        self.time_lapse_layout = self.temperature_spectrum_widget._pg_time_lapse_layout

        self.graph_mouse_pos_lbl = self.graph_status_bar.left_lbl
        self.graph_info_lbl = self.graph_status_bar.right_lbl

        self.setup_epics_pb = self.epics_gb. setup_epics_pb
        self.connect_to_epics_cb = self.epics_gb. connect_to_epics_cb
        self.connect_to_epics_datalog_cb = self.epics_gb.connect_to_epics_datalog_cb
        self.connect_to_ad_cb = self.epics_gb. connect_to_ad_cb


        self.browse_by_name_rb = self.control_widget.file_gb.browse_by_name_rb 
        self.browse_by_time_rb = self.control_widget.file_gb.browse_by_time_rb 

        self.temperature_function_plank_rb = self.t_function_type_section.plank_btn
        self.temperature_function_wien_rb = self.t_function_type_section.wien_btn

        self.use_backbround_data_cb = self.roi_widget.use_backbround_data_cb
        self.use_backbround_calibration_cb = self.roi_widget.use_backbround_calibration_cb


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


    def show_error_dialog(self, message_text:str, dialog_title:str):
        error_dialog = QtWidgets.QMessageBox()
        error_dialog.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        error_dialog.setText(message_text)
        error_dialog.setWindowTitle(dialog_title)
        error_dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)

        error_dialog.exec()
        

class TemperatureFileNavigation(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._layout = QtWidgets.QHBoxLayout()

        self.file_gb = FileGroupBox()
        self.output_gb = OutputGroupBox()
        
        self._layout.addWidget(self.file_gb)
        self._layout.addWidget(self.output_gb)
        self.setMaximumHeight(120)
        self.setMinimumHeight(120)

      
        self.setLayout(self._layout)
        


class EPICSGroupBox(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('EPICS')

        self._layout = QtWidgets.QGridLayout()
   
        self.setup_epics_pb = QtWidgets.QPushButton("Setup EPICS")
        self.connect_to_epics_cb = QtWidgets.QCheckBox("Connect to EPICS")
        self.connect_to_epics_datalog_cb = QtWidgets.QCheckBox("Connect to datalog")
        self.connect_to_ad_cb = QtWidgets.QCheckBox("Connect to AD")
        self.connect_to_epics_cb.setLayoutDirection(QtCore.Qt.LayoutDirection.RightToLeft)
        self.connect_to_ad_cb.setLayoutDirection(QtCore.Qt.LayoutDirection.RightToLeft)
        self._layout.addWidget(self.setup_epics_pb,0,0)
        self._layout.addWidget(self.connect_to_epics_cb,0,1)
        self._layout.addWidget(self.connect_to_ad_cb,1,1)
        
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
        self.load_setting_btn = QtWidgets.QPushButton("Load")
        load_setting_icon = QIcon()
        load_setting_icon.addFile(os.path.join(resources_path,'style','input.svg'))
        self.load_setting_btn.setIcon(load_setting_icon)

        self.save_setting_btn = QtWidgets.QPushButton("Save")
        save_setting_pixmap = QStyle.StandardPixmap.SP_DialogSaveButton
        save_setting_icon = self.style().standardIcon(save_setting_pixmap)  
        self.save_setting_btn.setIcon(save_setting_icon)

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

        self.downstream_gb = CalibrationGB('Downstream', 'rgba(255, 215, 0, 255)')
        self.upstream_gb = CalibrationGB('Upstream', 'rgba(255, 111, 97, 255)')

        self._layout.addWidget(self.downstream_gb)
        self._layout.addWidget(self.upstream_gb)

        #self._layout.addSpacerItem(VerticalSpacerItem())
        self.setLayout(self._layout)
        self.setMaximumWidth(300)

class TemperatureFitSettings(QtWidgets.QGroupBox):
    def __init__(self, *args, **kwargs):
        super().__init__('Temperature fit function')
        self._layout = QtWidgets.QHBoxLayout()

        self.plank_btn = QtWidgets.QRadioButton("Plank")
        self.wien_btn = QtWidgets.QRadioButton("Wien")
        

        self._layout.addWidget(self.plank_btn)
        self._layout.addWidget(self.wien_btn)
        self.plank_btn.setChecked(True)

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
        self.save_standard_btn = QtWidgets.QPushButton('Save Standard')

        self.start_frame_lbl = QtWidgets.QLabel('Start frame')
        self.end_frame_lbl = QtWidgets.QLabel('End frame')
        self.start_frame = IntegerTextField('1')
        self.end_frame = IntegerTextField('1')

        self._layout.addWidget(self.load_file_btn, 0, 0, 1, 3)
        self._layout.addWidget(self.file_lbl, 0, 3)
        self._layout.addWidget(self.temperature_txt, 1, 0, 1, 2)
        self._layout.addWidget(self.temperature_unit_lbl, 1, 2)
        self._layout.addWidget(self.temperature_rb, 1, 3)
        self._layout.addWidget(self.load_standard_btn, 2, 1, 1, 2)
        self._layout.addWidget(self.standard_rb, 2, 3)
        self._layout.addWidget(self.standard_file_lbl, 3, 3)
        self._layout.addWidget(self.start_frame_lbl, 4, 0, 1,2)
        self._layout.addWidget(self.start_frame, 4, 2, 1,2)
        self._layout.addWidget(self.end_frame_lbl, 5, 0, 1,2)
        self._layout.addWidget(self.end_frame,5, 2,1,2)
        self._layout.addWidget(self.save_standard_btn, 6, 0, 1, 2)

        self.setLayout(self._layout)
        self.style_widgets()
        self.set_stylesheet()

    def style_widgets(self):

        self.temperature_txt.setValidator(QtGui.QDoubleValidator())
        self.temperature_txt.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
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
        #self._style_widgets()

        self._connect_widgets()
        self.approved = False

    def _create_widgets(self):
        self.us_temp_lbl = QtWidgets.QLabel("US Temperature PV")
        self.us_temp_lbl.setMinimumWidth(200)
        self.ds_temp_lbl = QtWidgets.QLabel("DS Temperature PV")
     
        self.temperature_file_directory_pv_lbl = QtWidgets.QLabel("T File Directory PV")

        self.area_detector_pv_lbl = QtWidgets.QLabel("Area Detector PV")

        self.us_temp_txt = QtWidgets.QLineEdit()
        self.us_temp_txt.setMinimumWidth(200)
        self.ds_temp_txt = QtWidgets.QLineEdit()
   
        self.temperature_file_directory_pv_txt = QtWidgets.QLineEdit()
        self.area_detector_pv_txt = QtWidgets.QLineEdit()

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

        self._grid_layout.addWidget(self.area_detector_pv_lbl, 3, 0)
        self._grid_layout.addWidget(self.area_detector_pv_txt, 3, 1)

        self._grid_layout.addWidget(self.ok_btn, 4, 0)
        self._grid_layout.addWidget(self.cancel_btn, 4, 1)



        self.setLayout(self._grid_layout)

    '''def _style_widgets(self):
        self.ok_btn.setEnabled(False)
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)

        file = open(os.path.join(style_path, "stylesheet.qss"))
        stylesheet = file.read()
        self.setStyleSheet(stylesheet)
        file.close()'''

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

    @property
    def area_detector_pv(self):
        return str(self.area_detector_pv_txt.text())

    @area_detector_pv.setter
    def area_detector_pv(self, pv):
        self.area_detector_pv_txt.setText(pv)

    def exec(self):
        """
        Overwriting the dialog exec function to center the widget in the parent window before execution.
        """
        parent_center = self._parent.window().mapToGlobal(self._parent.window().rect().center())
        self.move(parent_center.x() - 101, parent_center.y() - 48)
        super(SetupEpicsDialog, self).exec()


