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
import time, copy
from PyQt6 import QtCore
from epics import PV 


ColorMode = {
    0:'Mono' ,
    1:'Bayer' ,
    2:'RGB1',
    3:'RGB2' ,
    4:'RGB3' ,
    5:'YUV444' ,
    6:'YUV422' ,
    7:'YUV421' 
}

DataType= {
    0:'Int8',
    1:'UInt8' ,
    2:'Int16' ,
    3:'UInt16' ,
    4:'Int32'    ,   
    5:'UInt32'  ,
    6:'Int64' ,
    7:'UInt64' ,
    8:'Float32' ,
    9:'Float64' 
}

Grating = {
    0:'750nm, 300',
    1:'800nm, 150'
}

ExitPort = {
    0:'Side',
    1:'Front'
}

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

    def __init__(self, record_name, x_calibration, file_type=None, activate=False, debug = False):
        """
        :record_name: Area Detector PV name, e.g. '16LF1'.
       
        :param activate: whether or not the Watcher will already emit signals
        """
        super().__init__()
        self.file_path_monitor = None
        self.file_type = file_type
        self.n_detectors = 1 # may support multiple detectors in the future, only 1 is implemented for now
        self.record_name = record_name

        pvs = {'cam1': 
                    {
                    'Manufacturer_RBV': None,
                    'Model_RBV' : None,
                    'LFGrating_RBV' : None,
                    'LFGratingWL_RBV' : None,
                    'AcquireTime_RBV': None,
                    'LFGain_RBV': None,
                    'LFExitPort_RBV': None,
                    'LFIntensifierGain_RBV': None,
                    'TemperatureActual_RBV': None},
         
                'image1' : {'ArrayData': None,
                            'ArraySize0_RBV': None,
                            'ArraySize1_RBV': None,
                            'ArraySize2_RBV': None,
                            'DataType_RBV': None,
                            'NDimensions_RBV': None,
                            'ColorMode_RBV': None   
                            },
               }
        
        self.pvs = []
        for i in range(self.n_detectors):
            self.pvs.append(copy.deepcopy(pvs))
            for group in self.pvs[i].keys():
                for pv in self.pvs[i][group].keys():
                    name = self.record_name+':'+str(group[:-1])+str(i+1) + ':' + pv
                    self.pvs[i][group][pv] = PV(name)
        
        self.detector = 'NA'
        self.exposure_time = 0
        self.num_frames = 1
        self.grating = 'NA'
        self._xdim = 0
        self._ydim = 0
        self.debug = debug

        self.gain = 1
        self.image1File = ''
        self.image2File = ''

        self.update_data()
        self.num_frames = 1

        self.x_calibration = x_calibration

        if activate:
            self.activate()

    def update_data(self): 
        ArrayData = self.pvs[0]['image1']['ArrayData'].get()
        ArraySize0 = self.pvs[0]['image1']['ArraySize0_RBV'].get()
        ArraySize1 = self.pvs[0]['image1']['ArraySize1_RBV'].get()
        NDimensions = self.pvs[0]['image1']['NDimensions_RBV'].get()
        if NDimensions == 2 and ArraySize0 != 0 and ArraySize1 != 0:
            reshaped_arr = ArrayData.reshape((ArraySize0, ArraySize1))
            self.img = reshaped_arr
            [self._ydim, self._xdim] = self.img.shape
            self.detector = self.pvs[0]['cam1']['Model_RBV'].get(as_string=True)
            self.exposure_time = self.pvs[0]['cam1']['AcquireTime_RBV'].get()
            g = self.pvs[0]['cam1']['LFGrating_RBV'].get()
            if g in Grating:
                self.grating = Grating[g]



    def get_dimension(self):
        """Returns (xdim, ydim)"""
        return (self._xdim, self._ydim)

    @property
    def record_name(self):
        return self._record_name

    @record_name.setter
    def record_name(self, new_record_name):
        
        ## monitor epics pvs
        file_path_pv_name = new_record_name + ':cam1:FullFileName_RBV'
        image_pv_name = new_record_name + ':image1:ArrayData'
   
        self.pvs = {}
        self.pvs['file_path'] = PV(file_path_pv_name)
        self.pvs['image'] = PV(image_pv_name)

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
       
        if len(Status):
            
            self.file_added.emit(Status)
            



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

