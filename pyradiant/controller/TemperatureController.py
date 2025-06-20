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

from PyQt6 import QtWidgets, QtCore

from ..widget.TemperatureWidget import TemperatureWidget, SetupEpicsDialog
from ..widget.ConfigurationWidget import ConfigurationWidget
from ..widget.Widgets import open_file_dialog, open_files_dialog, save_file_dialog
from ..model.TemperatureModel import TemperatureModel
from ..model.helper.FileNameIterator import get_file_and_extension
from ..model import epics_settings as eps
from .NewFileInDirectoryWatcher import NewFileInDirectoryWatcher
from ..model.data_models.ADWatcher import ADWatcher
import numpy as np
from ..model.helper.HelperModule import get_partial_index , get_partial_value
from .. widget.DataHistoryWidget import dataHistoryWidget
from ..model.helper.AppSettings import AppSettings
from natsort import natsorted
import json

from .. import EPICS_INSTALLED
if EPICS_INSTALLED:
    from epics import caput, camonitor, camonitor_clear, caget, PV
else:
    caput = None
    camonitor = None
    camonitor_clear = None
    caget = None
    PV = None


class TemperatureController(QtCore.QObject):

    temperature_folder_changed = QtCore.pyqtSignal()
    epics_datalog_file_changed = QtCore.pyqtSignal(str)

    def __init__(self, temperature_widget: TemperatureWidget, model: TemperatureModel, data_history_widget:dataHistoryWidget):
        """
        :param temperature_widget: reference to the temperature widget
        :type temperature_widget: TemperatureWidget
        :param temperature_model: reference to the global temperature model
        :type model: TemperatureModel
        :return:
        """
        super(TemperatureController, self).__init__()
        #self.widget: TemperatureWidget
        self.widget = temperature_widget
        #self.model: TemperatureModel
        self.model = model

        self.max_allowed_T = 20000
        self.min_allowed_T = 500

        #self.data_history_widget: dataHistoryWidget
        self.data_history_widget = data_history_widget
        self.data_history_widget.setWindowTitle('Temperature Log')
        self.data_history_widget.temperatures_plot_widget.update_time_lapse_ds_temperature_txt('Downstream')
        self.data_history_widget.temperatures_plot_widget.update_time_lapse_us_temperature_txt('Upstream')

        self._exp_working_dir = ''
        

        self.live_data = False # this is True when AD checkbox is checked and an area detector connection is established, otherwise it's False
        self._AD_watcher = None
        self.epics_available = False
        

        self.widget.frame_num_txt.clearFocus()
        self.widget.load_data_file_btn.setFocus()

        
        self._create_autoprocess_system()
        
        self.create_signals()

        if not EPICS_INSTALLED:
            self.widget.connect_to_epics_cb.setEnabled(False)
            self.widget.connect_to_epics_cb.setChecked(False)
            self.widget.connect_to_ad_cb.setEnabled(False)
            self.widget.connect_to_ad_cb.setChecked(False)
            
        # testing only: remove in production
        self.setup_epics_datalog_file_monitor()
        self.widget.connect_to_epics_datalog_cb.setChecked(True)

    def connect_epics(self):

        
        if  EPICS_INSTALLED:
            self.setup_epics_dialog = SetupEpicsDialog(self.widget)
        else:
            self.widget.epics_gb.hide()
        self.epics_available = False

        if EPICS_INSTALLED:
            
            if self.check_pv(eps.epics_settings['T_folder']):
                self.epics_available = True
                self.setup_temperature_file_folder_monitor()
                
            else:
                self.epics_available = False
                
                self.widget.connect_to_epics_cb.setChecked(False)
                self.widget.show_error_dialog("Couldn't connect to EPICS.", "Connection Error")
                
        
    def disconnect_epics(self):
        self.epics_available = False
        if eps.epics_settings['T_folder'] is not None \
                and eps.epics_settings['T_folder'] != 'None':
            try:
                camonitor_clear(eps.epics_settings['T_folder'])
            except:
                pass

    def connect_to_area_detector(self):
    
        if EPICS_INSTALLED:
            ad_on = self.widget.connect_to_ad_cb.isChecked()
            if ad_on:
                if self._AD_watcher is None:
                    x_cal = self.model.current_configuration.x_calibration
                    self._AD_watcher = ADWatcher(record_name=eps.epics_settings['area_detector'], x_calibration=x_cal)
                    if self._AD_watcher.initialized:
                        self._AD_watcher.file_added.connect(self.load_data_file_ad)
                        self._AD_watcher.activate()
                    else:
                        self.widget.show_error_dialog("Couldn't connect to Area Detector.", "Connection Error")
                        self.widget.connect_to_ad_cb.setChecked(False)
                        self._AD_watcher = None
                
    
    def disconnect_from_area_detector(self):
        if self._AD_watcher != None:
            if self._AD_watcher.initialized:
                self._AD_watcher.deactivate()
                self._AD_watcher.file_added.disconnect(self.load_data_file_ad)
                self._AD_watcher = None

    def create_signals(self):
        # File signals
        self.connect_click_function(self.widget.load_data_file_btn, self.load_data_file)
        self.widget.load_next_data_file_btn.clicked.connect(self.load_next_data_image)
        self.widget.load_previous_data_file_btn.clicked.connect(self.load_previous_data_image)
        self.widget.browse_by_name_rb.clicked.connect(self.toggle_browse_mode)
        self.widget.browse_by_time_rb.clicked.connect(self.toggle_browse_mode)
        self.widget.load_next_frame_btn.clicked.connect(self.load_next_img_frame_callback)
        self.widget.load_previous_frame_btn.clicked.connect(self.load_previous_img_frame_callback)
        self.widget.frame_num_txt.editingFinished.connect( self.frame_num_txt_callback)
        self.widget.autoprocess_cb.toggled.connect(self.auto_process_cb_toggled)

        self.connect_click_function(self.widget.save_data_btn, self.save_data_btn_clicked)
        self.connect_click_function(self.widget.save_graph_btn, self.save_graph_btn_clicked)

        self.temperature_folder_changed.connect(self.temperature_folder_changed_emitted)
        self.epics_datalog_file_changed.connect(self.epics_datalog_file_changed_emitted)

        # File drag and drop
        self.widget.file_dragged_in.connect(self.file_dragged_in) 

        # Calibration signals
        self.connect_click_function(self.widget.load_ds_calibration_file_btn, self.load_ds_calibration_file)
        self.connect_click_function(self.widget.load_us_calibration_file_btn, self.load_us_calibration_file)

        self.widget.us_calibration_start_frame.editingFinished.connect(self.us_calibration_frame_range_callback)
        self.widget.ds_calibration_start_frame.editingFinished.connect(self.ds_calibration_frame_range_callback)
        self.widget.us_calibration_end_frame.editingFinished.connect(self.us_calibration_frame_range_callback)
        self.widget.ds_calibration_end_frame.editingFinished.connect(self.ds_calibration_frame_range_callback)

        self.widget.ds_standard_rb.toggled.connect(self.model.current_configuration.set_ds_calibration_modus)
        self.widget.us_standard_rb.toggled.connect(self.model.current_configuration.set_us_calibration_modus)

        self.connect_click_function(self.widget.ds_load_standard_file_btn, self.load_ds_standard_file)
        self.connect_click_function(self.widget.us_load_standard_file_btn, self.load_us_standard_file)

        self.connect_click_function(self.widget.ds_save_standard_file_btn, self.save_ds_standard_file)
        self.connect_click_function(self.widget.us_save_standard_file_btn, self.save_us_standard_file)

        self.widget.ds_temperature_txt.editingFinished.connect(self.ds_temperature_txt_changed)
        self.widget.us_temperature_txt.editingFinished.connect(self.us_temperature_txt_changed)

        self.widget.temperature_function_plank_rb.clicked.connect(self.temperature_function_callback)
        self.widget.temperature_function_wien_rb.clicked.connect(self.temperature_function_callback)

        # Setting signals
        self.connect_click_function(self.widget.load_setting_btn, self.load_setting_file)
        self.connect_click_function(self.widget.save_setting_btn, self.save_setting_file)
        self.widget.settings_cb.currentIndexChanged.connect(self.settings_cb_changed)
        self.widget.setup_epics_pb.clicked.connect(self.setup_epics_pb_clicked)

        # model signals
        self.model.data_changed_signal.connect(self.data_changed_signal_callback)
        self.model.ds_calculations_changed.connect(self.ds_calculations_changed)
        self.model.us_calculations_changed.connect(self.us_calculations_changed)

        

        self.widget.roi_widget.rois_changed.connect(self.widget_rois_changed)
        self.widget.roi_widget.wl_range_changed.connect(self.widget_wl_range_changed_callback)

        # mouse moved signals
        self.widget.temperature_spectrum_widget.mouse_moved.connect(self.graph_mouse_moved)
        self.widget.roi_widget.img_widget.mouse_moved.connect(self.roi_mouse_moved)

        # data hitory signals
        self.connect_click_function(self.widget.data_history_btn, self.data_history_btn_callback)
        

        # epics stuff
        self.widget.connect_to_ad_cb.clicked.connect(self.connect_to_ad_cb_callback)
        self.widget.connect_to_epics_cb.clicked.connect(self.connect_to_epics_cb_callback)

        self.widget.use_backbround_data_cb.toggled.connect(self.use_backbround_cb_callback)
        self.widget.use_backbround_calibration_cb.toggled.connect(self.use_backbround_cb_callback)

        self.widget.two_color_btn.clicked.connect(self.two_color_display_toggle_callback)


    def load_next_img_frame_callback(self):
        self.model.current_configuration.load_next_img_frame()
        self.set_frame_text(str(self.model.current_configuration.current_frame + 1))

    def load_previous_img_frame_callback(self):
        self.model.current_configuration.load_previous_img_frame()
        self.set_frame_text(str(self.model.current_configuration.current_frame + 1))

    def two_color_display_toggle_callback(self):
        self.us_calculations_changed()
        self.ds_calculations_changed()

    def frame_num_txt_callback(self):
        self.widget.frame_num_txt.clearFocus()
        num =  int(self.widget.frame_num_txt.text()) -1
        

        if num >= 0 and num <= self.model.current_configuration.data_img_file.num_frames:
            self.model.current_configuration.load_any_img_frame(num)

    def connect_click_function(self, emitter, function):
        emitter.clicked.connect(function)
        
    def close_log(self):
        for conf in self.model.configurations:
            conf.close_log()

    def use_backbround_cb_callback(self):
        use_data_background = self.widget.use_backbround_data_cb.isChecked()
        use_calibration_background = self.widget.use_backbround_calibration_cb.isChecked()
        self.model.current_configuration.set_use_insitu_background(use_data_background,use_calibration_background)


    def connect_to_ad_cb_callback(self):
        if self.widget.connect_to_ad_cb.isChecked():
            self.connect_to_area_detector()
        else:
            self.disconnect_from_area_detector()

    def connect_to_epics_cb_callback(self):
        if self.widget.connect_to_epics_cb.isChecked():
            self.connect_epics()
        else:
            self.disconnect_epics()


    def process_multiframe(self):
        # hack, refactor later:
        # Since the entire timelapse calculation is costly,
        # it should only get recalculated when the actual file changes, 
        # or any setting changes
        # - not when only the frame number is updated.

        # for not this function is called from several places:
        # file is loaded
        # next or previous file is loaded
        # Area Detector data is updated

        if self.model.current_configuration.data_img_file is not None:
            num_frames = self.model.current_configuration.data_img_file.num_frames
            if num_frames>1:
                
                self.update_time_lapse()

    def load_data_file(self, filenames=None):
        if isinstance(filenames, str):
            filenames = [filenames]
        if filenames is None or filenames is False:
            filenames = open_files_dialog(self.widget, caption="Load Experiment SPE",
                                          directory=self._exp_working_dir)

        for filename in filenames:
            if filename != '':
                if os.path.isfile(filename):
                    self._exp_working_dir = os.path.dirname(str(filename))
                    self.model.current_configuration.load_data_image(str(filename))
                    self._directory_watcher.path = self._exp_working_dir
                    # hack, refactor later:
                    self.process_multiframe()
                    #print('Loaded File: ', filename)
                else:
                    pass
                    #print('file not found: ' + str(filename))

    def load_data_file_ad(self, filename=None):
        if isinstance(filename, str):
            self.model.current_configuration.load_data_image_ad(self._AD_watcher)
            # hack, refactor later:
            self.process_multiframe()

    def file_dragged_in(self,files):
        self.load_data_file(filenames=files)
                
    def load_next_data_image(self):
        
        if self.widget.browse_by_name_rb.isChecked():
            mode = 'number'
        else:
            mode = 'time'
        self.model.current_configuration.load_next_data_image(mode)
        
        # hack, refactor later:
        self.process_multiframe()

    def load_previous_data_image(self):
        
        if self.widget.browse_by_name_rb.isChecked():
            mode = 'number'
        else:
            mode = 'time'
        self.model.current_configuration.load_previous_data_image(mode)

        # hack, refactor later:
        self.process_multiframe()
        
    def toggle_browse_mode(self):
        
        time_mode = self.widget.browse_by_time_rb.isChecked()
        self.model.current_configuration._filename_iterator.create_timed_file_list = time_mode
        if time_mode:
            self.model.current_configuration._filename_iterator.update_file_list()
        

    def load_ds_calibration_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Downstream Calibration SPE",
                                        directory=self._exp_working_dir)

        if filename != '':
            self._exp_working_dir = os.path.dirname(filename)
            ds_start_frame = int(self.widget.ds_calibration_start_frame.text())
            ds_end_frame = int(self.widget.ds_calibration_end_frame.text())
            self.model.current_configuration.ds_temperature_model.calibration_frames = [ds_start_frame,ds_end_frame]
            self.model.current_configuration.load_ds_calibration_image(filename)

    def load_us_calibration_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Upstream Calibration SPE",
                                        directory=self._exp_working_dir)

        if filename != '':
            self._exp_working_dir = os.path.dirname(filename)

            us_start_frame = int(self.widget.us_calibration_start_frame.text())
            us_end_frame = int(self.widget.us_calibration_end_frame.text())
            self.model.current_configuration.us_temperature_model.calibration_frames = [us_start_frame,us_end_frame]

            self.model.current_configuration.load_us_calibration_image(filename)

    def us_calibration_frame_range_callback(self, *args):
        us_start_frame = int(self.widget.us_calibration_start_frame.text())
        us_end_frame = int(self.widget.us_calibration_end_frame.text())
        self.model.current_configuration.us_temperature_model.calibration_frames = [us_start_frame,us_end_frame]
        self.model.current_configuration.us_set_calibration_data()

    def ds_calibration_frame_range_callback(self, *args):
        ds_start_frame = int(self.widget.ds_calibration_start_frame.text())
        ds_end_frame = int(self.widget.ds_calibration_end_frame.text())
        self.model.current_configuration.ds_temperature_model.calibration_frames = [ds_start_frame,ds_end_frame]
       
        self.model.current_configuration.ds_set_calibration_data()


    def ds_temperature_txt_changed(self):
        new_temperature = float(str(self.widget.ds_temperature_txt.text()))
        self.model.current_configuration.set_ds_calibration_temperature(new_temperature)

    def us_temperature_txt_changed(self):
        new_temperature = float(str(self.widget.us_temperature_txt.text()))
        self.model.current_configuration.set_us_calibration_temperature(new_temperature)

    def temperature_function_callback(self):
        plank = self.widget.temperature_function_plank_rb.isChecked()
        if plank:
            function_type = 'plank'
        else:
            function_type = 'wien'
        self.model.current_configuration.set_temperature_fit_function(function_type)

    def load_ds_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Downstream Standard Spectrum",
                                        directory=self._exp_working_dir)

        if filename != '':
            self._exp_working_dir = os.path.dirname(filename)
            self.model.current_configuration.load_ds_standard_spectrum(filename)

    def load_us_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Upstream Standard Spectrum",
                                        directory=self._exp_working_dir)

        if filename != '':
            self._exp_working_dir = os.path.dirname(filename)
            self.model.current_configuration.load_us_standard_spectrum(filename)

    def save_ds_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(self.widget, caption="Save Downstream Standard Spectrum",
                                        directory=self._exp_working_dir)

        if filename != '':
            self._exp_working_dir = os.path.dirname(filename)
            self.model.current_configuration.save_ds_standard_spectrum(filename)

    def save_us_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(self.widget, caption="Save Upstream Standard Spectrum",
                                        directory=self._exp_working_dir)

        if filename != '':
            self._exp_working_dir = os.path.dirname(filename)
            self.model.current_configuration.save_us_standard_spectrum(filename)

    def save_setting_file(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(self.widget, caption="Save setting file",
                                        directory=self.model.current_configuration._setting_working_dir)

        if filename != '':
            self.model.current_configuration._setting_working_dir = os.path.dirname(filename)
            self.model.current_configuration.save_setting(filename)
            self.update_setting_combobox(filename)

    def load_setting_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load setting file",
                                        directory=self.model.current_configuration._setting_working_dir)

        if filename != '':
            self.model.current_configuration._setting_working_dir = os.path.dirname(filename)
            self.model.current_configuration.load_setting(filename)
            
            self.update_setting_combobox(filename)

    def save_data_btn_clicked(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(
                self.widget,
                caption="Save data in tabulated text format",
                directory=os.path.join(self._exp_working_dir,
                                       '.'.join(self.model.current_configuration.data_img_file.filename.split(".")[:-1]) + ".txt")
            )
        if filename != '':
            self.model.current_configuration.save_txt(filename)

    def save_graph_btn_clicked(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(
                self.widget,
                caption="Save displayed graph as vector graphics or image",
                directory=os.path.join(self._exp_working_dir,
                                       '.'.join(self.model.current_configuration.data_img_file.filename.split(".")[:-1]) + ".svg"),
                filter='Vector Graphics (*.svg);; Image (*.png)'
            )
        filename = str(filename)
        base_filename, extension = get_file_and_extension(filename)
        ds_filename = base_filename + "_ds." + extension
        us_filename = base_filename + "_us." + extension

        if filename != '':
            self.widget.temperature_spectrum_widget.save_graph(ds_filename, us_filename)

    def update_setting_combobox(self, filename):
        folder = os.path.split(filename)[0]
        self._settings_files_list = []
        self._settings_file_names_list = []
        try:
            files = os.listdir(folder)
            files = natsorted(files)
            for file in files:
                if file.endswith('.trs') and not file.startswith('.'):
                    self._settings_files_list.append(file)
                    name_for_list = file.split('.')[:-1][0]
                    self._settings_file_names_list.append(name_for_list)
        except:
            pass
        if not len(self._settings_files_list):
            self._settings_files_list.append(filename)
            name_for_list = filename.split('.')[:-1][0]
            self._settings_file_names_list.append(name_for_list)
        
        self.widget.settings_cb.blockSignals(True)
        self.widget.settings_cb.clear()
        self.widget.settings_cb.addItems(self._settings_file_names_list)

        selected_name = os.path.split(filename)[1].split('.')[:-1][0]
        ind = self._settings_file_names_list.index(selected_name)
        self.widget.settings_cb.setCurrentIndex(ind)
        self.widget.settings_cb.blockSignals(False)

    def settings_cb_changed(self):
        current_index = self.widget.settings_cb.currentIndex()
        new_file_name = os.path.join(self.model.current_configuration._setting_working_dir,
                                     self._settings_files_list[current_index])  # therefore also one has to be deleted
        self.load_setting_file(new_file_name)
        self.widget.settings_cb.blockSignals(True)
        self.widget.settings_cb.setCurrentIndex(current_index)
        self.widget.settings_cb.blockSignals(False)
        self.use_background_update()

    def use_background_update(self):
        self.widget.use_backbround_calibration_cb.blockSignals(True)
        self.widget.use_backbround_data_cb.blockSignals(True)
        self.widget.use_backbround_calibration_cb.setChecked (bool(self.model.current_configuration.use_insitu_calibration_background))
        self.widget.use_backbround_data_cb.setChecked (bool(self.model.current_configuration.use_insitu_data_background))
        self.widget.use_backbround_calibration_cb.blockSignals(False)
        self.widget.use_backbround_data_cb.blockSignals(False)


    def data_changed_signal_callback(self):
        self.widget.roi_widget.plot_img(self.model.current_configuration.data_img)
        if self.model.current_configuration.data_img_file is not None:
            if hasattr(self.model.current_configuration.data_img_file,'raw_ccd'):
                self.widget.roi_widget.plot_raw_ccd(self.model.current_configuration.data_img_file.raw_ccd)
        if self.model.current_configuration.x_calibration is not None and self.model.current_configuration.data_img is not None:
            wl_calibration = self.model.current_configuration.x_calibration
            #x_dim = self.model.current_configuration.data_img.shape[1]
            x = round(wl_calibration[0], 3)
            y = 0
            w = round(wl_calibration[-1]-wl_calibration[0],3)
            h = self.model.current_configuration.data_img.shape[0]
            
            self.widget.roi_widget.img_widget.set_wavelength_calibration((x,y,w,h))
        rois = self.model.current_configuration.get_roi_data_list()
        self.widget.roi_widget.set_rois(self.model.current_configuration.get_roi_data_list())
        self.widget.roi_widget.set_wl_range(self.model.current_configuration.wl_range)
        
        # update exp data widget
        #####################################

        if self.model.current_configuration.data_img_file is not None:
            if hasattr(self.model.current_configuration.data_img_file, 'filename') :
                self.model.current_configuration.data_img_file.filename = os.path.normpath(self.model.current_configuration.data_img_file.filename)
                fname = os.path.split(self.model.current_configuration.data_img_file.filename)[-1]
                dirname = os.path.sep.join(os.path.dirname(self.model.current_configuration.data_img_file.filename).split(os.path.sep)[-2:])
                joined = os.path.join(dirname,fname)
                self.widget.filename_lbl.setText(joined)

                #self.widget.dirname_lbl.setText(dirname)
            else:
                self.widget.filename_lbl.setText('')
                #self.widget.dirname_lbl.setText('')

            if self.model.current_configuration.data_img_file.num_frames > 1:
                self.widget.frame_widget.setVisible(True)
                self.widget.temperature_spectrum_widget.show_time_lapse_plot(True)
            else:
                self.widget.frame_widget.setVisible(False)
                self.widget.temperature_spectrum_widget.show_time_lapse_plot(False)
           
            self.set_frame_text(str(self.model.current_configuration.current_frame + 1))
       
            
            self.widget.graph_info_lbl.setText(self.model.current_configuration.file_info)
        else:
            self.widget.filename_lbl.setText('Select File...')
            #self.widget.dirname_lbl.setText('')
            self.widget.frame_widget.setVisible(False)
            self.widget.temperature_spectrum_widget.show_time_lapse_plot(False)

        self.use_background_update()

        self.ds_calculations_changed()
        self.us_calculations_changed()

        self.widget.temperature_spectrum_widget.normalize_range()
        
        mtime = self.model.current_configuration.mtime
        self.widget.mtime.setText('Timestamp: '+ str(mtime))

        settings_filename = self.model.current_configuration.setting_filename
        if settings_filename:
            self.update_setting_combobox(settings_filename)
        
    def set_frame_text(self, txt):
        self.widget.frame_num_txt.blockSignals(True)
        self.widget.frame_num_txt.setText(txt)
        self.widget.frame_num_txt.clearFocus()
        self.widget.frame_num_txt.blockSignals(False)

    def ds_calculations_changed(self):
        curr_frame = self.model.current_configuration.current_frame
        ds_fit_ok = True
        if hasattr(self.model, 'ds_temperatures'):
            ds_temperatures = self.model.current_configuration.ds_temperatures
            if curr_frame>=0 and curr_frame<len(ds_temperatures):
                ds_fit_ok = ds_temperatures[curr_frame] > 0
      

        if self.model.current_configuration.ds_calibration_filename is not None:
            self.widget.ds_calibration_filename_lbl.setText(str(os.path.basename(self.model.current_configuration.ds_calibration_filename)))
        else:
            self.widget.ds_calibration_filename_lbl.setText('Select File...')

        self.widget.ds_standard_filename_lbl.setText(str(os.path.basename(self.model.current_configuration.ds_standard_filename)))
        self.widget.ds_standard_rb.setChecked(self.model.current_configuration.ds_temperature_model.calibration_parameter.modus)
        self.widget.ds_temperature_txt.setText(str(self.model.current_configuration.ds_temperature_model.calibration_parameter.temperature))

        if len(self.model.current_configuration.ds_corrected_spectrum):
            ds_plot_spectrum = self.model.current_configuration.ds_corrected_spectrum
        else:
            ds_plot_spectrum = self.model.current_configuration.ds_data_spectrum

        if self.widget.two_color_btn.isChecked() and self.model.current_configuration.ds_temperature != 0 and ds_fit_ok:
            lam, temp = self.model.current_configuration.ds_2_color_temp
            self.widget.temperature_spectrum_widget.plot_ds_data(lam, temp)
        else:
            self.widget.temperature_spectrum_widget.plot_ds_data(*ds_plot_spectrum.data,mask=ds_plot_spectrum.mask)
            self.widget.temperature_spectrum_widget.plot_ds_masked_data(*ds_plot_spectrum.data,mask=ds_plot_spectrum.mask)
        if self.model.current_configuration.ds_temperature != 0 and ds_fit_ok and self.model.current_configuration.ds_temperature_error <= self.model.current_configuration.error_limit:
            if not self.widget.two_color_btn.isChecked() :
                self.widget.temperature_spectrum_widget.plot_ds_fit(*self.model.current_configuration.ds_fit_spectrum.data)
            else:
                self.widget.temperature_spectrum_widget.plot_ds_fit([],[])
            self.widget.temperature_spectrum_widget.update_ds_temperature_txt(self.model.current_configuration.ds_temperature,
                                                           self.model.current_configuration.ds_temperature_error)
           
            
        else:
            self.widget.temperature_spectrum_widget.plot_ds_fit([],[])
            self.widget.temperature_spectrum_widget.update_ds_temperature_txt(0,
                                                           0)
        self.widget.roi_widget.specra_widget.plot_ds_data(*self.model.current_configuration.ds_temperature_model.data_spectrum.data)

        
        self.widget.temperature_spectrum_widget.update_ds_roi_max_txt(self.model.current_configuration.ds_temperature_model.data_roi_max)

        if self.widget.connect_to_epics_cb.isChecked():
            if self.epics_available:
                ds_temp_pv = eps.epics_settings['ds_last_temp']
                if ds_temp_pv is not None and not ds_temp_pv == '' and not ds_temp_pv == 'None':
                    caput(ds_temp_pv, self.model.current_configuration.ds_temperature)
                

    def us_calculations_changed(self):
        curr_frame = self.model.current_configuration.current_frame
        us_fit_ok = True
        if hasattr(self.model, 'us_temperatures'):
            us_temperatures = self.model.current_configuration.us_temperatures
            if curr_frame>=0 and curr_frame<len(us_temperatures):
                us_fit_ok = us_temperatures[curr_frame] > 0
 
        if self.model.current_configuration.us_calibration_filename is not None:
            self.widget.us_calibration_filename_lbl.setText(str(os.path.basename(self.model.current_configuration.us_calibration_filename)))
        else:
            self.widget.us_calibration_filename_lbl.setText('Select File...')

        self.widget.us_standard_filename_lbl.setText(str(os.path.basename(self.model.current_configuration.us_standard_filename)))
        self.widget.us_standard_rb.setChecked(self.model.current_configuration.us_temperature_model.calibration_parameter.modus)
        self.widget.us_temperature_txt.setText(str(self.model.current_configuration.us_temperature_model.calibration_parameter.temperature))

        if len(self.model.current_configuration.us_corrected_spectrum):
            us_plot_spectrum = self.model.current_configuration.us_corrected_spectrum
        else:
            us_plot_spectrum = self.model.current_configuration.us_data_spectrum
        if self.widget.two_color_btn.isChecked() and self.model.current_configuration.us_temperature != 0 and us_fit_ok:
            lam, temp = self.model.current_configuration.us_2_color_temp
            self.widget.temperature_spectrum_widget.plot_us_data(lam, temp)
        else:
            self.widget.temperature_spectrum_widget.plot_us_data(*us_plot_spectrum.data,mask=us_plot_spectrum.mask)
            self.widget.temperature_spectrum_widget.plot_us_masked_data(*us_plot_spectrum.data,mask=us_plot_spectrum.mask)
        if self.model.current_configuration.us_temperature != 0 and us_fit_ok and self.model.current_configuration.us_temperature_error <= self.model.current_configuration.error_limit:
            if not self.widget.two_color_btn.isChecked() :
                
                self.widget.temperature_spectrum_widget.plot_us_fit(*self.model.current_configuration.us_fit_spectrum.data)
            else:
                self.widget.temperature_spectrum_widget.plot_us_fit([],[])
            self.widget.temperature_spectrum_widget.update_us_temperature_txt(self.model.current_configuration.us_temperature,
                                                           self.model.current_configuration.us_temperature_error)
            
            lam, temp = self.model.current_configuration.us_2_color_temp

        else:
            self.widget.temperature_spectrum_widget.plot_us_fit([],[])
            self.widget.temperature_spectrum_widget.update_us_temperature_txt(0,
                                                           0)
        self.widget.roi_widget.specra_widget.plot_us_data(*self.model.current_configuration.us_temperature_model.data_spectrum.data)

        
        self.widget.temperature_spectrum_widget.update_us_roi_max_txt(self.model.current_configuration.us_temperature_model.data_roi_max)

        if self.widget.connect_to_epics_cb.isChecked():
            if self.epics_available:
                us_temp_pv = eps.epics_settings['us_last_temp']
                if us_temp_pv is not None and not us_temp_pv =='' and not us_temp_pv == 'None':
                    caput(us_temp_pv, self.model.current_configuration.us_temperature)
                

    def update_time_lapse(self):
        # this actually fits all the frames so be careful calling this willy nilly
        us_temperature, us_temperature_error, ds_temperature, ds_temperature_error = self.model.current_configuration.fit_all_frames()

        out = np.nan, np.nan
        if len(ds_temperature):
            ds_temperature_arr = np.array(ds_temperature)
            ds_temperature_error_arr = np.array(ds_temperature_error)
            select_ds  = (ds_temperature_arr > self.min_allowed_T) & (ds_temperature_arr < self.max_allowed_T) & (ds_temperature_error_arr < self.model.current_configuration.error_limit)
            #print(f'select_ds {select_ds}')
            ds_t = ds_temperature_arr[select_ds]
            #print(f'ds_t {ds_t}')
            if len(ds_t):
                out = np.mean(ds_t), np.std(ds_t)
            ds_temperature_plot_data = ds_temperature_arr[:]
            ds_temperature_plot_data[~select_ds] = 0
        self.widget.temperature_spectrum_widget.plot_ds_time_lapse(range(1, len(ds_temperature_plot_data)+1), ds_temperature_plot_data)
        self.widget.temperature_spectrum_widget.update_time_lapse_ds_temperature_txt(*out)
            
        out = np.nan, np.nan
        if len(us_temperature):
            us_temperature_arr = np.array(us_temperature)
            us_temperature_error_arr = np.array(us_temperature_error)
            select_us  = (us_temperature_arr > self.min_allowed_T) & (us_temperature_arr < self.max_allowed_T) & (us_temperature_error_arr < self.model.current_configuration.error_limit)
            us_t = us_temperature_arr[select_us]
            if len(us_t):
                out = np.mean(us_t), np.std(us_t)
            us_temperature_plot_data = us_temperature_arr[:]
            us_temperature_plot_data[~select_us] = 0
        self.widget.temperature_spectrum_widget.plot_us_time_lapse(range(1, len(us_temperature_plot_data)+1), us_temperature_plot_data)
        self.widget.temperature_spectrum_widget.update_time_lapse_us_temperature_txt(*out)

        if len(ds_t) or len(us_t):
            conc = np.concatenate((ds_t, us_t))
            conc = conc[~np.isnan(conc).all()]
            out = np.mean(conc), np.std(conc)
        else:
            out = np.nan, np.nan
        self.widget.temperature_spectrum_widget.update_time_lapse_combined_temperature_txt(*out )

    def data_history_btn_callback(self):
        self.data_history_widget.raise_widget()

    

    def widget_rois_changed(self, roi_list):
        if self.model.current_configuration.has_data():
            
            
            self.model.current_configuration.set_rois(roi_list)
            wl_range = self.model.current_configuration.wl_range
            self.widget.roi_widget.set_wl_range(wl_range)


    def widget_wl_range_changed_callback(self, wl_range):
        if self.model.current_configuration.has_data():
            self.model.current_configuration.wl_range = wl_range
            rois = self.model.current_configuration.get_roi_data_list()
            self.model.current_configuration.set_rois(rois)
            self.widget.roi_widget.set_rois(rois)

    def graph_mouse_moved(self, x, y):
        self.widget.graph_mouse_pos_lbl.setText("X: {:8.2f}  Y: {:8.2f}".format(x, y))

    def roi_mouse_moved(self, x, y):
        x = int(np.floor(x))
        y = int(np.floor(y))
        try:
            
            if self.model.current_configuration.data_img is not None:
                s = self.model.current_configuration.data_img.shape
                if int(y)< s[0] and int(x)< s[1] and int(x) >= 0 and int(y) >= 0:
                    self.widget.roi_widget.pos_lbl.setText("X: {:5.0f}  Y: {:5.0f}    Int: {:6.0f}    Wavelength: {:5.2f} nm".
                                                    format(x, y,
                                                            self.model.current_configuration.data_img[int(y), int(x)],
                                                            self.model.current_configuration.data_img_file.x_calibration[int(x)]))
        except (IndexError, TypeError):
            pass

    def save_settings(self, settings):
        settings : AppSettings

        # Save
        
        configs = []
        for conf in self.model.configurations:
            if not conf.setting_filename is None:
                set_fname =  os.path.split(conf.setting_filename)[-1]
                name_for_list = set_fname.split('.')[:-1][0]
                conf_dict = {}
                if conf.data_img_file:
                    data_file = conf.data_img_file.filename
                    conf_dict["temperature data file"]=conf.data_img_file.filename

                conf_dict["temperature settings directory"]=conf._setting_working_dir
                conf_dict["temperature settings file"]=name_for_list
                configs.append(conf_dict)

        config_txt = json.dumps(configs)
        
        settings.set("temperature configurations",
                          config_txt)
        
        configuration_ind = self.model.configuration_ind
        settings.set("temperature configuration_ind",
                          configuration_ind)


        settings.set("temperature autoprocessing",
                          self.widget.autoprocess_cb.isChecked())

        settings.set("temperature epics connected",
                          self.widget.connect_to_epics_cb.isChecked())
        settings.dump()

    def load_conf_settings(self, conf):
        settings_file_path = os.path.join(str(conf["temperature settings directory"]),
                                          str(conf["temperature settings file"]) + ".trs")
        if os.path.exists(settings_file_path):
            self.load_setting_file(settings_file_path)
        if 'temperature data file' in conf:
            temperature_data_path = str(conf["temperature data file"])
            if os.path.exists(temperature_data_path) and len(temperature_data_path)>4:
                self.load_data_file(temperature_data_path)

    def load_settings(self, settings):
        settings : AppSettings
        settings.dump()

        # Load
        conf_list = json.loads(settings.get("temperature configurations", "[]"))
        
        if len(conf_list):
            conf = conf_list[0]

            self.load_conf_settings(conf)
            

            more_configurations = len(conf_list)-1
            for n in range(more_configurations):
                self.model.add_configuration()
                self.model.select_configuration(n+1)
                conf = conf_list[n+1]
                self.load_conf_settings(conf)

            configuration_ind = settings.get("temperature configuration_ind", 0)
            self.model.select_configuration(configuration_ind)

        try_epics = str.lower(str(settings.get("temperature epics connected") )) == 'true'
        if try_epics:
            self.connect_epics()
            if not self.epics_available:
                settings.set("temperature epics connected",
                          False)
                self.widget.connect_to_epics_cb.setChecked(False)
            else:
                self.widget.connect_to_epics_cb.setChecked(True)
        temperature_autoprocessing = str.lower(str(settings.get("temperature autoprocessing")) )== 'true'
        if temperature_autoprocessing:
            self.widget.autoprocess_cb.setChecked(True)

    def auto_process_cb_toggled(self):

        connect_to_ad = self.widget.connect_to_ad_cb.isChecked()

        if connect_to_ad:
            if self.widget.autoprocess_cb.isChecked():
                self.widget.autoprocess_lbl.setText('AD')
                self._AD_watcher.activate()
                
            else:
                self._AD_watcher.deactivate()
                self.widget.autoprocess_lbl.setText('')

            self._directory_watcher.deactivate()
        else:
            if self.widget.autoprocess_cb.isChecked():
                self.widget.autoprocess_lbl.setText('FS')
                self._directory_watcher.activate()
            else:
                self._directory_watcher.deactivate()
                self.widget.autoprocess_lbl.setText('')

            if self._AD_watcher != None:
                self._AD_watcher.deactivate()

    def _create_autoprocess_system(self):
        self._directory_watcher = NewFileInDirectoryWatcher(file_types=['.spe'])
        self._directory_watcher.file_added.connect(self.load_data_file)

        
        

    def check_pv(self, pv_name):
        try:
            value = caget(pv_name, timeout=0.2)  # Short timeout for quick response
        except:
            value = None
        return value is not None
 
    def setup_epics_datalog_file_monitor(self):
        if eps.epics_settings['epics_datalog'] is not None \
                and eps.epics_settings['epics_datalog'] != 'None':
            
            if camonitor_clear is not None:
                camonitor_clear(eps.epics_settings['epics_datalog'])
                camonitor(eps.epics_settings['epics_datalog'], callback=self.epics_datalog_changed)

    def setup_temperature_file_folder_monitor(self):
        if eps.epics_settings['T_folder'] is not None \
                and eps.epics_settings['T_folder'] != 'None':
  
            if camonitor_clear is not None:
                camonitor_clear(eps.epics_settings['T_folder'])
                camonitor(eps.epics_settings['T_folder'], callback=self.temperature_file_folder_changed)

  
    def epics_datalog_changed(self, *args, **kwargs):
        
        if 'value' in kwargs:
            value = kwargs['value']
            print(value)
            if self.widget.connect_to_epics_datalog_cb.isChecked() and self.widget.autoprocess_cb.isChecked():
                self.epics_datalog_file_changed.emit(value)

    def temperature_file_folder_changed(self, *args, **kwargs):
        
        if self.widget.connect_to_epics_cb.isChecked() and self.widget.autoprocess_cb.isChecked():
            self.temperature_folder_changed.emit()

    def temperature_folder_changed_emitted(self):
        if  self.epics_available:
            self._exp_working_dir = caget(eps.epics_settings['T_folder'], as_string=True)
            self._directory_watcher.path = self._exp_working_dir

    def epics_datalog_file_changed_emitted(self, filename):
        
        print(f'epics_datalog_file_changed_emitted {filename}')
        
        current_file = self.model.current_configuration.data_img_file.filename
        print(f'current_file {current_file}')
        current_folder = os.path.split(current_file)[0]
        new_file = os.path.join(current_folder, filename)
        exists = os.path.isfile(new_file)
        if exists:
            print(f'{new_file} exists -> loading')
            self.load_data_file(new_file)
        

    def setup_epics_pb_clicked(self):
        self.setup_epics_dialog.ok_btn.setEnabled(True)
        self.setup_epics_dialog.us_temp_pv = eps.epics_settings['us_last_temp']
        self.setup_epics_dialog.ds_temp_pv = eps.epics_settings['ds_last_temp']

        self.setup_epics_dialog.temperature_file_folder_pv = eps.epics_settings['T_folder']
        self.setup_epics_dialog.area_detector_pv = eps.epics_settings['area_detector']
        self.setup_epics_dialog.exec()
        if self.setup_epics_dialog.approved:
            with open('pyradiant/model/epics_settings.py', 'w') as outfile:
                outfile.write('epics_settings = {\n')
                outfile.write("    'us_last_temp': '" + self.setup_epics_dialog.us_temp_pv + "',\n")
                outfile.write("    'ds_last_temp': '" + self.setup_epics_dialog.ds_temp_pv + "',\n")
              
                outfile.write("    'T_folder': '" + self.setup_epics_dialog.temperature_file_folder_pv + "',\n")
                outfile.write("    'area_detector': '" + self.setup_epics_dialog.area_detector_pv + "',\n")
                outfile.write("}\n")
            eps.epics_settings['us_last_temp'] = self.setup_epics_dialog.us_temp_pv
            eps.epics_settings['ds_last_temp'] = self.setup_epics_dialog.ds_temp_pv

            eps.epics_settings['T_folder'] = self.setup_epics_dialog.temperature_file_folder_pv
            eps.epics_settings['area_detector'] = self.setup_epics_dialog.area_detector_pv
