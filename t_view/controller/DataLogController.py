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

import os, importlib
import time
from PyQt5 import QtWidgets, QtCore

from ..widget.Widgets import open_file_dialog, open_files_dialog, save_file_dialog

import numpy as np
from ..model.helper.HelperModule import get_partial_index , get_partial_value
from .. widget.TemperatureSpectrumWidget import dataHistoryWidget
from ..model.TemperatureModel import TemperatureModel
from .NewFileInDirectoryWatcher import NewFileInDirectoryWatcher

from ..model.DatalogModel import DatalogModel

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

        self._exp_working_dir = ''
        self._setting_working_dir = ''

        self.create_signals()

    def create_signals(self):
        # File signals
        self.connect_click_function(self.widget.load_data_log_file_btn, self.load_data_log_file)


        # File drag and drop
        #self.widget.file_dragged_in.connect(self.file_dragged_in) 

        # model signals
        self.temperature_model.data_changed.connect(self.data_changed_callback)
        self.temperature_model.ds_calculations_changed.connect(self.ds_calculations_changed_callback)
        self.temperature_model.us_calculations_changed.connect(self.us_calculations_changed_callback)


    def connect_click_function(self, emitter, function):
        emitter.clicked.connect(function)
        
    def close_log(self):
        self.temperature_model.close_log()

    def load_data_log_file(self, filename=None):
        
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Experiment Log",
                                          directory=self._exp_working_dir)

        
        if filename != '':
            #now = time.time()
            self._exp_working_dir = os.path.dirname(str(filename))
            records = self.model.load_last_n_records(str(filename),DATALOG_LENGTH)
            self.model.data_records_groups = [records]
            T_DS, T_US = self.model.get_temperatures_by_group(0)
            #T_DS = T_DS[-200:]
            #T_US = T_US[-200:]
            T_DS[T_DS<=MIN_TEMPERATURE] = np.nan
            T_US[T_US<=MIN_TEMPERATURE] = np.nan
            T_DS[T_DS >MAX_TEMPERATURE] = np.nan
            T_US[T_US >MAX_TEMPERATURE] = np.nan
            
            x_DS = np.arange(T_DS.shape[0])
            x_US = np.arange(T_US.shape[0])

            self.widget.plot_ds_time_lapse(x_DS, T_DS)
            self.widget.plot_us_time_lapse(x_US, T_US)

            self.widget.load_data_log_file_lbl.setText(str(filename) )
            #later  = time.time()
            #print ('T log update time = ' + str(later-now))

    def file_dragged_in(self,files):
        pass

    def update_log_display(self):
        log_file = self.temperature_model.get_log_file_path()
        if log_file != None:
            if os.path.exists(log_file):
                self.load_data_log_file(filename=log_file)

    def data_changed_callback(self):
        self.update_log_display()

    def ds_calculations_changed_callback(self):
        self.update_log_display()
                

    def us_calculations_changed_callback(self):
        self.update_log_display()
                
    def show_widget(self):
        self.widget.raise_widget()



    def _create_autoprocess_system(self):
        self._directory_watcher = NewFileInDirectoryWatcher(file_types=['.txt'])
        self._directory_watcher.file_added.connect(self.load_data_file)
        self.setup_temperature_file_folder_monitor()

    def setup_temperature_file_folder_monitor(self):
        pass

    def temperature_file_folder_changed(self, *args, **kwargs):
        pass

    def temperature_folder_changed_emitted(self):
        pass

