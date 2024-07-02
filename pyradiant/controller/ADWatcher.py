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

from PyQt6 import QtCore
from epics import PV 

class ADWatcher(QtCore.QObject):
    """
    This class watches the Area Detector PV if a new file is available.

    Typical usage::
        def callback_fcn(path):
            print(path)

        watcher = ADWatcher(record_name, file_type = '.spe') #file_type parameter not implemented yet)
        watcher.file_added.connect(callback_fcn)

    """
    file_added = QtCore.pyqtSignal(str)

    def __init__(self, record_name,  file_type=None, activate=False):
        """
        :record_name: Area Detector PV name, e.g. '16LF1'.
        :param file_type: file type which will be watched for, e.g. '.spe'. (not implemented yet)
        :param activate: whether or not the Watcher will already emit signals
        """
        super().__init__()
        self.file_path_monitor = None
        self.file_type = file_type
        self.record_name = record_name

        if activate:
            self.activate()


    @property
    def record_name(self):
        return self._record_name

    @record_name.setter
    def record_name(self, new_record_name):
        
        ## monitor epics pvs
        file_path_pv_name = new_record_name + ':cam1:FullFileName_RBV'
   
        self.pvs = {}
        self.pvs['file_path'] = PV(file_path_pv_name)

        if self.file_path_monitor != None:
            self.file_path_monitor.unSetPVmonitor()
        self.file_path_monitor = epicsMonitor(self.pvs['file_path'], self.handle_AD_callback, autostart=False)
        self._record_name = new_record_name

    def activate(self):
        """
        activates the watcher to emit signals when new data is available
        """
        if self.file_path_monitor != None:
            self.file_path_monitor.SetPVmonitor()

    def deactivate(self):
        """
        deactivates the watcher so it will not emit a signal when new data is available
        """
        if self.file_path_monitor != None:
            self.file_path_monitor.unSetPVmonitor()


    def handle_AD_callback(self, Status):
       
        if Status == 'Done':
           
            self.file_added.emit('test')



class epicsMonitor(QtCore.QObject):
    callback_triggered = QtCore.pyqtSignal(str)

    def __init__(self, pv, callback, autostart=False):
        super().__init__()
        self.mcaPV = pv
        self.monitor_On = False
        self.emitted_timestamp = None
        


        self.callback_triggered.connect(callback)
        if autostart:
            self.SetPVmonitor()
            

    def SetPVmonitor(self):
        if self.monitor_On == False:
            self.mcaPV.clear_callbacks()
            self.mcaPV.add_callback(self.onPVChange)
            self.monitor_On = True
    def unSetPVmonitor(self):
        if self.monitor_On == True:
            self.mcaPV.clear_callbacks()
            self.monitor_On = False

    def onPVChange(self, pvname=None, char_value=None, **kws):

        
        self.callback_triggered.emit(char_value)

