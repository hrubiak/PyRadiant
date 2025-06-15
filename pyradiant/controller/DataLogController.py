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
import time
from PyQt6 import QtWidgets, QtCore

from ..widget.Widgets import open_file_dialog, open_files_dialog, save_file_dialog

import numpy as np
from ..model.helper.HelperModule import get_partial_index , get_partial_value
from .. widget.DataHistoryWidget import dataHistoryWidget
from ..model.TemperatureModel import TemperatureModel
from .NewFileInDirectoryWatcher import NewFileInDirectoryWatcher

from ..model.DatalogModel import DatalogModel
from ..model.DatalogModelStatic import StaticRecordManager

DATALOG_LENGTH = 200
MIN_TEMPERATURE = 100
MAX_TEMPERATURE = 12000

class DataLogController(QtCore.QObject):

    temperature_point_selected = QtCore.pyqtSignal(list)

    def __init__(self, model: TemperatureModel, data_history_widget:dataHistoryWidget):
        """
        :param temperature_model: reference to the global temperature model
        :type model: TemperatureModel
        :param data_history_widget: reference to the temperature log widget
        :type data_history_widget: dataHistoryWidget
        :return:
        """
        super().__init__()
        #self.widget: dataHistoryWidget
        self.widget = data_history_widget
        #self.temperature_model: TemperatureModel
        self.temperature_model = model
        self.model = DatalogModel()
        self.model_static = StaticRecordManager()
        

        self._exp_working_dir = ''
        self._setting_working_dir = ''

        self.create_signals()



    def create_signals(self):
        
        self.connect_models()
        self.connect_click_function(self.widget.clear_data_log_file_btn, self.clear_data_log_file_btn_callback)
        

    def disconnect_models(self):
        """
        Disconnects signals of the currently selected configuration.
        """
        self.temperature_model.log_file_loaded_signal.disconnect(self.load_log_from_file)
        self.temperature_model.current_configuration.set_log_callback(None)

    def connect_models(self):
        """
        Connects signals of the currently selected configuration
        """
        
        self.temperature_model.log_file_loaded_signal.connect(self.load_log_from_file)
        # model signals
        self.temperature_model.current_configuration.set_log_callback(self.log_file_updated_callback)

   
    def clear_data_log_file_btn_callback(self):
        self.temperature_model.current_configuration.clear_log()
        self.clear_log_display()
    
    def clear_log_display(self):
        self.model.clear_log()
        self.widget.temperatures_plot_widget.plot_ds_time_lapse([],[] )
        self.widget.temperatures_plot_widget.plot_us_time_lapse([], [])


    def connect_click_function(self, emitter, function):
        emitter.clicked.connect(function)
        
    '''def close_log(self):
        self.temperature_model.current_configuration.close_log()'''

    def load_data_log_file(self, filename=None):
        
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Experiment Log",
                                          directory=self._exp_working_dir)

        
        if filename != '':
            
            #now = time.time()
            self._exp_working_dir = os.path.dirname(str(filename))
            self.model_static.initialize_records(self._exp_working_dir)
            self.model_static.update_records_from_log(filename)

            records = self.model.load_last_n_records(str(filename),DATALOG_LENGTH)
            
            self.model.data_records=records
            T_DS, T_US = self.model.get_temperatures()
            #T_DS = T_DS[-200:]
            #T_US = T_US[-200:]
            T_DS[T_DS<=MIN_TEMPERATURE] = np.nan
            T_US[T_US<=MIN_TEMPERATURE] = np.nan
            T_DS[T_DS >MAX_TEMPERATURE] = np.nan
            T_US[T_US >MAX_TEMPERATURE] = np.nan
            
            x_DS = np.arange(T_DS.shape[0])
            x_US = np.arange(T_US.shape[0])

            self.widget.temperatures_plot_widget.plot_ds_time_lapse(x_DS, T_DS)
            self.widget.temperatures_plot_widget.plot_us_time_lapse(x_US, T_US)

            T_DS, T_US = self.model_static.get_temperatures(DATALOG_LENGTH)
            #T_DS = T_DS[-200:]
            #T_US = T_US[-200:]
            T_DS[T_DS<=MIN_TEMPERATURE] = np.nan
            T_US[T_US<=MIN_TEMPERATURE] = np.nan
            T_DS[T_DS >MAX_TEMPERATURE] = np.nan
            T_US[T_US >MAX_TEMPERATURE] = np.nan
            
            x_DS = np.arange(T_DS.shape[0])
            x_US = np.arange(T_US.shape[0])

            self.widget.static_temperature_plot_widget.plot_ds_time_lapse(x_DS, T_DS)
            self.widget.static_temperature_plot_widget.plot_us_time_lapse(x_US, T_US)

            fname = os.path.split(filename)[-1]
            dirname = os.path.sep.join(os.path.dirname(filename).split(os.path.sep)[-2:])
            joined = os.path.join(dirname,fname)
            self.widget.load_data_log_file_lbl.setText(joined)
            #later  = time.time()
            #print ('T log update time = ' + str(later-now))

    def file_dragged_in(self,files):
        pass

    def load_log_from_file(self):
        log_file = self.temperature_model.current_configuration.get_log_file_path()
        if log_file != None:
            if os.path.exists(log_file):
                self.load_data_log_file(filename=log_file)

    def log_file_updated_callback(self, log_dict):
        self.model.add_record(log_dict)
        self.model_static.update_record(**log_dict)

        spe_file = log_dict.get('# File')
        spe_file_static_index = self.model_static.get_record_index(spe_file) # for moving the cursor, etc
       
        T_DS, T_US = self.model.get_temperatures()
        #T_DS = T_DS[-200:]
        #T_US = T_US[-200:]
        T_DS[T_DS<=MIN_TEMPERATURE] = np.nan
        T_US[T_US<=MIN_TEMPERATURE] = np.nan
        T_DS[T_DS >MAX_TEMPERATURE] = np.nan
        T_US[T_US >MAX_TEMPERATURE] = np.nan
        
        x_DS = np.arange(T_DS.shape[0])
        x_US = np.arange(T_US.shape[0])

        self.widget.temperatures_plot_widget.plot_ds_time_lapse(x_DS, T_DS)
        self.widget.temperatures_plot_widget.plot_us_time_lapse(x_US, T_US)
        self.widget.temperatures_plot_widget.cursor_item.setPos(len(x_US))

        T_DS, T_US = self.model_static.get_temperatures(DATALOG_LENGTH)
        #T_DS = T_DS[-200:]
        #T_US = T_US[-200:]
        T_DS[T_DS<=MIN_TEMPERATURE] = np.nan
        T_US[T_US<=MIN_TEMPERATURE] = np.nan
        T_DS[T_DS >MAX_TEMPERATURE] = np.nan
        T_US[T_US >MAX_TEMPERATURE] = np.nan
        
        x_DS = np.arange(T_DS.shape[0])
        x_US = np.arange(T_US.shape[0])

        self.widget.static_temperature_plot_widget.plot_ds_time_lapse(x_DS, T_DS)
        self.widget.static_temperature_plot_widget.plot_us_time_lapse(x_US, T_US)
        if spe_file_static_index:
            self.widget.static_temperature_plot_widget.cursor_item.setPos(spe_file_static_index)

                
    def show_widget(self):
        self.widget.raise_widget()



    def setup_temperature_file_folder_monitor(self):
        pass

    def temperature_file_folder_changed(self, *args, **kwargs):
        pass

    def temperature_folder_changed_emitted(self):
        pass

