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
from PyQt6 import QtCore
import numpy as np
np.seterr(all = 'ignore')
import warnings
warnings.simplefilter("error")
from scipy.optimize import curve_fit
import h5py
import math
import datetime
import time
from .data_models.DataModel import DataModel
from .Spectrum import Spectrum
from .RoiData import RoiDataManager, Roi, get_roi_max, get_roi_sum, get_roi_img
from .data_models.SpeFile import SpeFile
from .data_models.H5File import H5File
from .helper import FileNameIterator
from .radiation import fit_linear, wien_pre_transform, m_to_T, m_b_wien
from .helper.HelperModule import get_partial_index, get_partial_value
from .TwoColor import calculate_2_color
from .helper.signal import Signal

from .helper.filter_oscillation import filter_oscillatory_component 


T_LOG_FILE = 'T_log'
LOG_HEADER = '# File\tFrame\tPath\tT_DS\tT_US\tT_DS_error\tT_US_error\tDetector\tExposure Time [sec]\tGain\tscaling_DS\tscaling_US\tcounts_DS\tcounts_US\n'


class TemperatureModelConfiguration(QtCore.QObject):
    
    def __init__(self):
        super().__init__()

        self.data_changed_signal = Signal()
        self.ds_calculations_changed = Signal()
        self.us_calculations_changed = Signal()

        self.log_file_loaded_signal = Signal()

        self.filename = None
        self.mtime = None
        self.data_img_file = None
        self._data_img = None
        self.log_file = None
        self.setting_filename = None
        self._setting_working_dir = ''

        self.ds_calibration_img_file = None
        self.us_calibration_img_file = None

        self.ds_calibration_filename = None
        self.us_calibration_filename = None

        self.use_insitu_data_background = True
        self.use_insitu_calibration_background = True

        self.x_calibration = None

        self.temperature_fit_function_str = 'plank'

        self._filename_iterator = FileNameIterator()

        self.roi_data_manager = RoiDataManager(4)

        self.current_frame = 0
        self.ds_temperature_model = SingleTemperatureModel(0, self.roi_data_manager)
        self.us_temperature_model = SingleTemperatureModel(1, self.roi_data_manager)

        self.us_temperatures = []
        self.us_temperatures_errors = []
        self.ds_temperatures = []
        self.ds_temperatures_errors = []

        self.error_limit = 200

        self.log_callback = None


    def set_log_callback(self, callback_method):
        self.log_callback = callback_method

    def data_changed_emit(self, frame):
        self.write_to_log(frame)
        self.data_changed_signal.emit()

    def ds_calculations_changed_emit(self):
        self.ds_calculations_changed.emit()

    def us_calculations_changed_emit(self):
        self.us_calculations_changed.emit()

    def write_to_log(self, frame):
        if self.log_file is not None:
            self.write_to_log_file(frame)
            

    def clear_log(self):
        if self.log_file is not None:
            self.log_file.truncate(0)
            self.log_file.seek(0)
            self.log_file.write(LOG_HEADER)
            #self.data_changed_emit(self.current_frame)

    def get_log_file_path(self):
        if self.log_file is not None:

            log_file_path = self.log_file.name
            return log_file_path
        else:
            return None

    def load_data_image_ad(self, area_detector):
        self.load_data_image(area_detector.record_name, area_detector=area_detector)

    # loading spe or h5 image files:
    #########################################################################
    def load_data_image(self, filename, area_detector=None):
        if area_detector == None:
            if not self.filename or not os.path.dirname(self.filename) == os.path.dirname(filename):

                lf = self.create_log_file(os.path.dirname(filename))
                if lf is not None:
                    self.log_file_loaded_signal.emit()
            self.filename = filename
            # Get the extension
            _, file_extension = os.path.splitext(filename)
        if area_detector!=None:
            area_detector.update_data()
            self.data_img_file = area_detector
        else:
            if file_extension == '.spe' or file_extension == '.SPE':
                self.data_img_file = SpeFile(filename)
            elif file_extension == '.h5':
                self.data_img_file = H5File(filename,self.x_calibration)


        if self.data_img_file.num_frames > 1:
            if not (self.current_frame >= 0 and self.current_frame< self.data_img_file.num_frames):
                self.current_frame = 0
            self._data_img = self.data_img_file.img[self.current_frame]
        else:
            self.current_frame = 0
            self._data_img = self.data_img_file.img
        self._update_temperature_models_data()
        self._filename_iterator.update_filename(filename)
        self.mtime = self.get_last_modified_time(filename)
        self.data_changed_emit(self.current_frame)

        
    
    def get_last_modified_time(self, file_path):
        # Get the modification time in seconds since the epoch
        if os.path.isfile(file_path):
            modification_time = os.path.getmtime(file_path)
            
            # Convert the modification time to a readable format
            modified_time = datetime.datetime.fromtimestamp(modification_time)
            
            return modified_time
        else:
            return None

    def load_next_data_image(self, mode):
        new_filename = self._filename_iterator.get_next_filename(mode)
        if new_filename is not None:
            self.load_data_image(new_filename)

    def load_previous_data_image(self, mode):
        new_filename = self._filename_iterator.get_previous_filename(mode)
        if new_filename is not None:
            self.load_data_image(new_filename)

    def load_next_img_frame(self):
        return self.set_img_frame_number_to(self.current_frame + 1)

    def load_previous_img_frame(self):
        return self.set_img_frame_number_to(self.current_frame - 1)
    
    def load_any_img_frame(self, num):
        
        return self.set_img_frame_number_to(num)

    def set_img_frame_number_to(self, frame_number):
        if self.current_frame == frame_number:
            return False
        if frame_number < 0 or frame_number >= self.data_img_file.num_frames:
            return False
        current_frame = frame_number
        if current_frame < 0:
            current_frame = 0
        self.current_frame = current_frame
        self._data_img = self.data_img_file.img[frame_number]
        self._update_temperature_models_data()
        self.data_changed_emit(self.current_frame)
        return True
    
    def get_filesystem_safe_datetime(self):
        # Format: YYYY-MM-DD_HH-MM-SS
        # This avoids characters like ":" which are not allowed on Windows.
        return datetime.datetime.now().strftime("%Y%m%d_%H-%M-%S")

    def create_log_file(self, file_path):
        if len(file_path):
            if self.log_file is not None:
                if hasattr(self.log_file, 'closed'):
                    if not self.log_file.closed:
                        self.log_file.close()
            norm_file_path = os.path.normpath(file_path)
            if os.access(norm_file_path, os.W_OK):
                fname = T_LOG_FILE + '.txt'
                log_file_path = os.path.normpath(os.path.join(file_path, fname))
                try: 
                    self.log_file = open(log_file_path, 'a')
                    self.log_file.write(LOG_HEADER)
                    return self.log_file
                except PermissionError:
                    self.log_file =  None
                    return None
        return None
        
    def close_log(self):
        if self.log_file != None:
            self.log_file. close()

  

    def write_to_log_file(self, frame):
        if not math.isnan(self.ds_temperature):
            ds_temp = str(int(self.ds_temperature))
        else:
            ds_temp = '0'
        if not math.isnan(self.us_temperature):
            us_temp = str(int(self.us_temperature))
        else:
            us_temp = '0'

        if not math.isnan(self.ds_temperature_error):
            ds_temperature_error = str(int(self.ds_temperature_error))
            if self.ds_temperature_error > self.error_limit:
                ds_temp= '0'
                ds_temperature_error = '0'
        else:
            ds_temperature_error = '0'
        if not math.isnan(self.us_temperature_error):
            us_temperature_error = str(int(self.us_temperature_error))
            if self.us_temperature_error > self.error_limit:
                us_temp= '0'
                us_temperature_error = '0'
        else:
            us_temperature_error = '0'

        if not math.isnan(self.ds_scaling):
            ds_scaling = format(self.ds_scaling, ".3e")
        else:
            ds_scaling = '0'
        if not math.isnan(self.us_scaling):
            us_scaling = format(self.us_scaling, ".3e")
        else:
            us_scaling = '0'    
        frame_s = str(frame + 1)
        log_data = (os.path.basename(self.filename), frame_s, os.path.dirname(self.filename), ds_temp, us_temp,
                    ds_temperature_error, us_temperature_error,
                    self.data_img_file.detector, str(self.data_img_file.exposure_time),str(self.data_img_file.gain), 
                    ds_scaling, us_scaling, 
                    format(self.ds_data_spectrum.counts, ".3e"), format(self.us_data_spectrum.counts, ".3e"))
        
        self.log_file.write('\t'.join(log_data) + '\n')
        self.log_file.flush()
        # Create a dictionary by zipping keys and values together
        keys = LOG_HEADER[:-1].split('\t')
        log_dict = dict(zip(keys, log_data))
        if self.log_callback is not None:
            self.log_callback(log_dict)
        time.sleep(0.01) # may help with not missing writes when batch processing
        

    def set_temperature_fit_function(self, function_type):
        if function_type == 'wien' or function_type == 'plank':
            self.temperature_fit_function_str = function_type
            self._update_temperature_models_data()
            self.data_changed_emit(self.current_frame)



    def set_use_insitu_background(self, use_data_background,use_calibration_background):
        if use_data_background != self.use_insitu_data_background or use_calibration_background !=self.use_insitu_calibration_background:
            self.use_insitu_data_background = use_data_background
            self.use_insitu_calibration_background = use_calibration_background
            self.ds_temperature_model.subtract_inistu_data_background = use_data_background
            self.us_temperature_model.subtract_inistu_data_background = use_data_background
            self.ds_temperature_model.subtract_inistu_calibration_background = use_calibration_background
            self.us_temperature_model.subtract_inistu_calibration_background = use_calibration_background
            self.ds_temperature_model._update_calibration_spectrum()
            self.us_temperature_model._update_calibration_spectrum()
            
            self._update_temperature_models_data()
            
            self.data_changed_emit(self.current_frame)

    def _update_temperature_models_data(self):

        self.ds_temperature_model.set_temperature_fit_function(self.temperature_fit_function_str)
        self.us_temperature_model.set_temperature_fit_function(self.temperature_fit_function_str)

        self.x_calibration = self.data_img_file.x_calibration
        self.ds_temperature_model.set_data(self._data_img,
                                           self.data_img_file.x_calibration)
        self.us_temperature_model.set_data(self._data_img,
                                           self.data_img_file.x_calibration)

    @property
    def data_img(self):
        return self._data_img

    @data_img.setter
    def data_img(self, value):
        self._data_img = value
        self._update_temperature_models_data()
        self.data_changed_emit(self.current_frame)

    def has_data(self):
        return self._data_img is not None

    @property
    def file_info(self):
        out = "Exp. Time: {}s | Grating: {} | Detector: {} ".format(round(float(self.data_img_file.exposure_time),6),
                                                                    self.data_img_file.grating,
                                                                    self.data_img_file.detector)
        if hasattr(self.data_img_file, 'EMIccd_gain'):
           out = out + f"| Gain: {self.data_img_file.EMIccd_gain}"
        return out

    # calibration image files:
    #########################################################################
    def load_ds_calibration_image(self, filename):
        # Get the extension
        _, file_extension = os.path.splitext(filename)
        if str.lower(file_extension) == '.spe':
            self.ds_calibration_img_file = SpeFile(filename)
        elif str.lower(file_extension)  == '.h5':
            self.ds_calibration_img_file = H5File(filename,self.x_calibration)

        #self.ds_calibration_img_file = SpeFile(filename)
        
        self.ds_calibration_filename = filename
        self.ds_set_calibration_data()
        self.ds_calculations_changed_emit()

    def ds_set_calibration_data(self):
        self.ds_temperature_model.set_calibration_data(self.ds_calibration_img_file,
                                                       self.ds_calibration_img_file.x_calibration)
        

    def load_us_calibration_image(self, filename):
        # Get the extension
        _, file_extension = os.path.splitext(filename)
        if str.lower(file_extension)  == '.spe':
            self.us_calibration_img_file = SpeFile(filename)
        elif str.lower(file_extension)  == '.h5':
            self.us_calibration_img_file = H5File(filename,self.x_calibration)

        #self.us_calibration_img_file = SpeFile(filename)
        self.us_calibration_filename = filename

        
        self.us_set_calibration_data()
        self.us_calculations_changed_emit()

    def us_set_calibration_data(self):
        self.us_temperature_model.set_calibration_data(self.us_calibration_img_file,
                                                       self.us_calibration_img_file.x_calibration)
        

    # setting standard interface
    #########################################################################
        
   

    def load_ds_standard_spectrum(self, filename):
        self.ds_temperature_model.load_standard_spectrum(filename)
        self.ds_calculations_changed_emit()

    def load_us_standard_spectrum(self, filename):
        self.us_temperature_model.load_standard_spectrum(filename)
        self.us_calculations_changed_emit()

    def save_ds_standard_spectrum(self, filename):
        self.ds_temperature_model.save_standard_spectrum(filename)

    def save_us_standard_spectrum(self, filename):
        self.us_temperature_model.save_standard_spectrum(filename)

    def set_ds_calibration_modus(self, modus):
        self.ds_temperature_model.set_calibration_modus(modus)
        self.ds_calculations_changed_emit()

    def set_us_calibration_modus(self, modus):
        self.us_temperature_model.set_calibration_modus(modus)
        self.us_calculations_changed_emit()

    def set_ds_calibration_temperature(self, temperature):
        self.ds_temperature_model.set_calibration_temperature(temperature)
        self.ds_calculations_changed_emit()

    def set_us_calibration_temperature(self, temperature):
        self.us_temperature_model.set_calibration_temperature(temperature)
        self.us_calculations_changed_emit()

    def save_setting(self, filename):
        f = h5py.File(filename, 'w')

        f.create_group('downstream_calibration')
        ds_group = f['downstream_calibration']
        if self.ds_calibration_img_file is not None:
            ds_group['image'] = self.ds_calibration_img_file.img
            ds_group['image'].attrs['filename'] = self.ds_calibration_img_file.filename
            ds_group['image'].attrs['x_calibration'] = self.ds_calibration_img_file.x_calibration
            ds_group['image'].attrs['subtract_bg'] = self.use_insitu_data_background
        else:
            if self.ds_temperature_model.calibration_img is not None:
                ds_group['image'] = self.ds_temperature_model.calibration_img
                ds_group['image'].attrs['filename'] = self.ds_calibration_filename
                ds_group['image'].attrs['x_calibration'] = self.ds_temperature_model._data_img_x_calibration
                ds_group['image'].attrs['subtract_bg'] = self.use_insitu_data_background

        ds_roi_list =  self.ds_roi.as_list()
        ds_group['roi'] = ds_roi_list
        ds_roi_bg_list = self.ds_roi_bg.as_list()
        ds_group['roi_bg'] = ds_roi_bg_list
        ds_group['modus'] = self.ds_temperature_model.calibration_parameter.modus
        ds_group['temperature'] = self.ds_temperature_model.calibration_parameter.temperature
        ds_group['standard_spectrum'] = self.ds_temperature_model.calibration_parameter.get_standard_spectrum().data
        ds_group['standard_spectrum'].attrs['filename'] = \
            self.ds_temperature_model.calibration_parameter.get_standard_filename()
        ds_group['standard_spectrum'].attrs['subtract_bg'] = self.use_insitu_calibration_background

        f.create_group('upstream_calibration')
        us_group = f['upstream_calibration']
        if self.us_calibration_img_file is not None:
            us_group['image'] = self.us_calibration_img_file.img
            us_group['image'].attrs['filename'] = self.us_calibration_img_file.filename
            us_group['image'].attrs['x_calibration'] = self.us_calibration_img_file.x_calibration
            us_group['image'].attrs['subtract_bg'] = self.use_insitu_data_background
        else:
            if self.us_temperature_model.calibration_img is not None:
                us_group['image'] = self.us_temperature_model.calibration_img
                us_group['image'].attrs['filename'] = self.us_calibration_filename
                us_group['image'].attrs['x_calibration'] = self.us_temperature_model._data_img_x_calibration
                us_group['image'].attrs['subtract_bg'] = self.use_insitu_data_background
        us_group['roi'] = self.us_roi.as_list()
        us_group['roi_bg'] = self.us_roi_bg.as_list()
        us_group['modus'] = self.us_temperature_model.calibration_parameter.modus
        us_group['temperature'] = self.us_temperature_model.calibration_parameter.temperature
        us_group['standard_spectrum'] = self.us_temperature_model.calibration_parameter.get_standard_spectrum().data
        us_group['standard_spectrum'].attrs['filename'] = \
            self.us_temperature_model.calibration_parameter.get_standard_filename()
        us_group['standard_spectrum'].attrs['subtract_bg'] = self.use_insitu_calibration_background

        f.close()

    
    def load_setting(self, filename):
        f = h5py.File(filename, 'r')
        ds_group = f['downstream_calibration']
        if 'image' in ds_group:
            ds_img = ds_group['image'][...]
            
            self.ds_calibration_filename = ds_group['image'].attrs['filename']
            if len(ds_img.shape) == 2:
                img_dimension = (ds_img.shape[1],
                                ds_img.shape[0])
            elif len(ds_img.shape) == 3:
                img_dimension = (ds_img.shape[2],
                                ds_img.shape[1])
            ds_group_roi = ds_group['roi'][...]
            ds_group_roi_bg = ds_group['roi_bg'][...]
            self.roi_data_manager.set_roi(0, img_dimension, ds_group_roi)
            self.roi_data_manager.set_roi(2, img_dimension, ds_group_roi_bg)
            x_calibration = ds_group['image'].attrs['x_calibration'][...] # this is a hack to be able to 
                                                                          # load h5 files later that don't 
                                                                          # have x_calibration
            if 'subtract_bg'in ds_group['image'].attrs:
                use_data_bg = bool(ds_group['image'].attrs['subtract_bg'])
                self.use_insitu_data_background = use_data_bg
                self.ds_temperature_model.subtract_inistu_data_background = use_data_bg
                self.us_temperature_model.subtract_inistu_data_background = use_data_bg

            self.x_calibration = x_calibration

            self.ds_calibration_img_file = DataModel()
            self.ds_calibration_img_file.img = ds_img
            self.ds_calibration_img_file.x_calibration = self.x_calibration
            self.ds_calibration_img_file.filename = self.ds_calibration_filename


            self.ds_temperature_model.set_calibration_data(ds_img, x_calibration)
            
            self.ds_temperature_model._update_all_spectra()


        else:
            self.ds_temperature_model.reset_calibration_data()
            self.ds_calibration_filename = None
            self.ds_roi = [0, 0, 0, 0]

        standard_data = ds_group['standard_spectrum'][...]
        self.ds_temperature_model.calibration_parameter.set_standard_spectrum(Spectrum(standard_data[0, :],
                                                                                     standard_data[1, :]))
        if 'subtract_bg'in ds_group['standard_spectrum'].attrs:
            use_calibration_bg = bool(ds_group['standard_spectrum'].attrs['subtract_bg'])
            self.use_insitu_calibration_background = use_calibration_bg

            
            self.ds_temperature_model.subtract_inistu_calibration_background = use_calibration_bg
            self.us_temperature_model.subtract_inistu_calibration_background = use_calibration_bg

        try:
            self.ds_temperature_model.calibration_parameter.standard_file_name = \
                ds_group['standard_spectrum'].attrs['filename']
        except AttributeError:
            self.ds_temperature_model.calibration_parameter.standard_file_name = \
                ds_group['standard_spectrum'].attrs['filename']

        modus = int(ds_group['modus'][...])
        self.ds_temperature_model.calibration_parameter.set_modus(modus)
        temperature = float(ds_group['temperature'][...])
        self.ds_temperature_model.calibration_parameter.set_temperature(temperature)

        us_group = f['upstream_calibration']
        if 'image' in us_group:
            us_img = us_group['image'][...]
            
            self.us_calibration_filename = us_group['image'].attrs['filename']
            if len(us_img.shape) == 2:
                img_dimension = (us_img.shape[1],
                                us_img.shape[0])
            elif len(us_img.shape) == 3:
                img_dimension = (us_img.shape[2],
                                us_img.shape[1])

            us_group_roi = us_group['roi'][...]
            us_group_roi_bg = us_group['roi_bg'][...]
            self.roi_data_manager.set_roi(1, img_dimension, us_group_roi)
            self.roi_data_manager.set_roi(3, img_dimension, us_group_roi_bg)
            self.us_temperature_model.set_calibration_data(us_img,
                                                           us_group['image'].attrs['x_calibration'][...])
            self.us_temperature_model._update_all_spectra()
        else:
            self.us_temperature_model.reset_calibration_data()
            self.us_calibration_filename = None
            self.us_roi = [0, 0, 0, 0]

        standard_data = us_group['standard_spectrum'][...]
        self.us_temperature_model.calibration_parameter.set_standard_spectrum(Spectrum(standard_data[0, :],
                                                                                     standard_data[1, :]))
        
        if 'subtract_bg'in us_group['image'].attrs:
                use_data_bg = bool(us_group['image'].attrs['subtract_bg'])
                self.use_insitu_data_background = use_data_bg

        try:
            self.us_temperature_model.calibration_parameter.standard_file_name = \
                us_group['standard_spectrum'].attrs['filename']
        except AttributeError:
            self.us_temperature_model.calibration_parameter.standard_file_name = \
                us_group['standard_spectrum'].attrs['filename']
            
        self.us_calibration_img_file = DataModel()
        self.us_calibration_img_file.img = us_img
        self.us_calibration_img_file.x_calibration = self.x_calibration
        self.us_calibration_img_file.filename = self.us_calibration_filename

        modus = int(us_group['modus'][...])
        self.us_temperature_model.calibration_parameter.set_modus(modus)
        temperature = float(us_group['temperature'][...])
        self.us_temperature_model.calibration_parameter.set_temperature(temperature)

        self.ds_temperature_model._update_all_spectra()
        self.us_temperature_model._update_all_spectra()

        self.ds_temperature_model.fit_data()
        self.us_temperature_model.fit_data()


        self.data_changed_emit(self.current_frame)

        self.setting_filename = filename


    

    def save_txt(self, filename):
        """
        Saves the fitted temperatures, original spectra and fitted spectra into a txt file
        Format:
            Header
                Downstream (K): temperatures...
                Upstream (K): temperatures...
            column names:
            wavelength(nm), DS_data, DS_fit, US_data, US_fit, ....

        if the original spe file contains several frames, all frames will be saved in order with always data and then
        fit.
        :param filename: path to save the file to
        :return:
        """

        # creating the header:
        header = "Fitted Temperatures:\n"
        header += "Downstream (K): {:.1f}\t{:.1f}\n".format(self.ds_temperature, self.ds_temperature_error)
        header += "Upstream (K): {:.1f}\t{:.1f}\n\n".format(self.us_temperature, self.us_temperature_error)
        header += "Datacolumns:\n"
        header_ds = header + "\t".join(("wavelength(nm)", "DS_data", "DS_fit"))
        header_us = header + "\t".join(("wavelength(nm)", "US_data", "US_fit"))

        ds_filename = filename.rsplit('.', 1)[0] + '_ds.txt'
        us_filename = filename.rsplit('.', 1)[0] + '_us.txt'

        if self.ds_fit_spectrum.y.size == self.ds_corrected_spectrum.y.size:
            output_matrix_ds = np.vstack((self.ds_data_spectrum.x,
                                        self.ds_corrected_spectrum.y, self.ds_fit_spectrum.y))
            
            np.savetxt(ds_filename, output_matrix_ds.T, header=header_ds)

        if self.us_corrected_spectrum.y.size ==  self.us_fit_spectrum.y.size:
            output_matrix_us = np.vstack((self.us_data_spectrum.x,
                                        self.us_corrected_spectrum.y, self.us_fit_spectrum.y))

            np.savetxt(us_filename, output_matrix_us.T, header=header_us)


    # updating wavelength range values
    @property
    def wl_range(self):
        try:
            ds_roi = self.roi_data_manager.get_roi(0, self.data_img_file.get_dimension())
            wl = self.x_calibration
            min_ind = int(round(ds_roi.x_min))
            max_ind = int(round(ds_roi.x_max))
            if min_ind < 0:
                min_ind = 0
            if max_ind >= len(wl):
                max_ind = len(wl)-1
            wl_start = int(round(wl[min_ind]))
            wl_end = int(round(wl[max_ind]))
            return [min(wl_start,wl_end),max(wl_start,wl_end)]
        except AttributeError:
            return [0, 0]

    @wl_range.setter
    def wl_range(self, wl_range):
        
        wl = self.x_calibration
        wl_max = max(wl)
        wl_min = min(wl)
        if wl_range[0] >= wl_min:
            if wl_range[0] <= wl_max:
                x_1 = int(round(get_partial_index(wl,wl_range[0])))
            else:
                x_1 = len(wl)-1
        else:
            x_1 = 0
        if wl_range[1] >= wl_min:
            if wl_range[1] <= wl_max:
                x_2 = int(round(get_partial_index(wl,wl_range[1])))
            else:
                x_2 = len(wl)-1
        else:
            x_2 = 0
        x_start = min(x_1,x_2)
        x_end = max(x_1,x_2)

        ds_limits = [0,0,0,0]
        us_limits = [0,0,0,0]
        ds_bg_limits = [0,0,0,0]
        us_bg_limits = [0,0,0,0]

        ds_limits[0] = x_start
        ds_limits[1] = x_end
        us_limits[0] = x_start
        us_limits[1] = x_end
        ds_bg_limits[0] = x_start
        ds_bg_limits[1] = x_end
        us_bg_limits[0] = x_start
        us_bg_limits[1] = x_end

        ds_roi = self.roi_data_manager.get_roi(0, self.data_img_file.get_dimension())
        us_roi = self.roi_data_manager.get_roi(1, self.data_img_file.get_dimension())
        ds_bg_roi = self.roi_data_manager.get_roi(2, self.data_img_file.get_dimension())
        us_bg_roi = self.roi_data_manager.get_roi(3, self.data_img_file.get_dimension())

        ds_limits[2] = ds_roi.y_min
        ds_limits[3] = ds_roi.y_max
        us_limits[2] = us_roi.y_min
        us_limits[3] = us_roi.y_max
        ds_bg_limits[2] = ds_bg_roi.y_min
        ds_bg_limits[3] = ds_bg_roi.y_max
        us_bg_limits[2] = us_bg_roi.y_min
        us_bg_limits[3] = us_bg_roi.y_max

        self.roi_data_manager.set_roi(0, self.data_img_file.get_dimension(), ds_limits)
        self.roi_data_manager.set_roi(1, self.data_img_file.get_dimension(), us_limits)
        self.roi_data_manager.set_roi(2, self.data_img_file.get_dimension(), ds_bg_limits)
        self.roi_data_manager.set_roi(3, self.data_img_file.get_dimension(), us_bg_limits)

        '''self.ds_temperature_model._update_all_spectra()
        self.ds_temperature_model.fit_data()
        self.ds_calculations_changed_emit()'''

    # updating roi values
    @property
    def ds_roi(self):
        try:
            dim = self.data_img_file.get_dimension()
            roi = self.roi_data_manager.get_roi(0, self.data_img_file.get_dimension())
            return roi
        except:
            return Roi([0, 0, 0, 0])
        

    @ds_roi.setter
    def ds_roi(self, ds_limits):
    
        self.roi_data_manager.set_roi(0, self.data_img_file.get_dimension(), ds_limits)
        self.ds_temperature_model._update_all_spectra()
        self.ds_temperature_model.fit_data()
        self.ds_calculations_changed_emit()

    @property
    def us_roi(self):
        try:
            dim = self.data_img_file.get_dimension()
            roi = self.roi_data_manager.get_roi(1, self.data_img_file.get_dimension())
            return roi
        except:
            return Roi([0, 0, 0, 0])

    @us_roi.setter
    def us_roi(self, us_limits):
        self.roi_data_manager.set_roi(1, self.data_img_file.get_dimension(), us_limits)
        self.us_temperature_model._update_all_spectra()
        self.us_temperature_model.fit_data()
        self.us_calculations_changed_emit()

    @property
    def ds_roi_bg(self):
        try:
            return self.roi_data_manager.get_roi(2, self.data_img_file.get_dimension())
        except AttributeError:
            return Roi([0, 0, 0, 0])

    @ds_roi_bg.setter
    def ds_roi_bg(self, ds_bg_limits):
        self.roi_data_manager.set_roi(2, self.data_img_file.get_dimension(), ds_bg_limits)
        self.ds_temperature_model._update_all_spectra()
        self.ds_temperature_model.fit_data()
        self.ds_calculations_changed_emit()

    @property
    def us_roi_bg(self):
        try:
            return self.roi_data_manager.get_roi(3, self.data_img_file.get_dimension())
        except:
            return Roi([0, 0, 0, 0])

    @us_roi_bg.setter
    def us_roi_bg(self, us_bg_limits):
        self.roi_data_manager.set_roi(3, self.data_img_file.get_dimension(), us_bg_limits)
        self.us_temperature_model._update_all_spectra()
        self.us_temperature_model.fit_data()
        self.us_calculations_changed_emit()

    @property
    def ds_filter_oscillation(self):
        return self.ds_temperature_model.filter_oscillation
    
    @ ds_filter_oscillation.setter
    def ds_filter_oscillation(self, apply_filter):
        self.ds_temperature_model.filter_oscillation = apply_filter
        self.ds_temperature_model._update_all_spectra()
        self.ds_temperature_model.fit_data()
        self.ds_calculations_changed_emit()

    @property
    def us_filter_oscillation(self):
        return self.ds_temperature_model.filter_oscillation
    
    @ us_filter_oscillation.setter
    def us_filter_oscillation(self, apply_filter):
        self.us_temperature_model.filter_oscillation = apply_filter
        self.us_temperature_model._update_all_spectra()
        self.us_temperature_model.fit_data()
        self.us_calculations_changed_emit()

    def set_rois(self, limits):
        self.us_roi = limits[1]
        self.ds_roi = limits[0]
        self.us_roi_bg = limits[3]
        self.ds_roi_bg = limits[2]


    def get_roi_data_list(self):
        ds_roi = self.ds_roi.as_list()
        us_roi = self.us_roi.as_list()
        ds_roi_bg = self.ds_roi_bg.as_list()
        us_roi_bg = self.us_roi_bg.as_list()
        return [ds_roi, us_roi, ds_roi_bg, us_roi_bg]
    


    # Spectrum interfaces
    #########################################################
    @property
    def ds_data_spectrum(self):
        return self.ds_temperature_model.data_spectrum

    @property
    def us_data_spectrum(self):
        return self.us_temperature_model.data_spectrum

    @property
    def ds_calibration_spectrum(self):
        return self.ds_temperature_model.calibration_spectrum

    @property
    def us_calibration_spectrum(self):
        return self.us_temperature_model.calibration_spectrum

    @property
    def ds_corrected_spectrum(self):
        return self.ds_temperature_model.corrected_spectrum

    @property
    def us_corrected_spectrum(self):
        return self.us_temperature_model.corrected_spectrum

    @property
    def ds_fit_spectrum(self):
        return self.ds_temperature_model.fit_spectrum

    @property
    def us_fit_spectrum(self):
        return self.us_temperature_model.fit_spectrum

    # temperature_properties

    @property
    def ds_temperature(self):
        return self.ds_temperature_model.temperature

    @property
    def us_temperature(self):
        return self.us_temperature_model.temperature

    @property
    def ds_scaling(self):
        return self.ds_temperature_model.scaling

    @property
    def us_scaling(self):
        return self.us_temperature_model.scaling
    


    @property
    def ds_temperature_error(self):
        return self.ds_temperature_model.temperature_error

    @property
    def us_temperature_error(self):
        return self.us_temperature_model.temperature_error

    @property
    def ds_standard_filename(self):
        return self.ds_temperature_model.calibration_parameter.standard_file_name

    @property
    def us_standard_filename(self):
        return self.us_temperature_model.calibration_parameter.standard_file_name

    @property
    def ds_roi_max(self):
        return self.ds_temperature_model.data_roi_max

    @property
    def us_roi_max(self):
        return self.us_temperature_model.data_roi_max
    
    @property
    def ds_2_color_temp(self):
        return self.ds_temperature_model.get2color()
    
    @property
    def us_2_color_temp(self):
        return self.us_temperature_model.get2color()
    


    # TODO: Think aboout refactoring this function away from here
    def get_wavelength_from(self, index):
        return self.data_img_file.get_wavelength_from(index)

    def get_index_from(self, wavelength):
        return self.data_img_file.get_index_from(wavelength)

    def get_x_limits(self):
        return np.array([self.data_img_file.x_calibration[0], self.data_img_file.x_calibration[-1]])

    def fit_all_frames(self):
        if self.data_img_file is None:
            return [], [], [], []

        if self.data_img_file.num_frames == 1:
            return [], [], [], []

        cur_frame = self.current_frame
        self.blockSignals(True)
        

        us_temperature = []
        ds_temperature = []

        us_temperature_error = []
        ds_temperature_error = []

        for frame_ind in range(self.data_img_file.num_frames):
            self.set_img_frame_number_to(frame_ind)
           
            us_counts = int(self.us_temperature_model.total_counts)
            ds_counts = int(self.ds_temperature_model.total_counts)
            max_counts = int(np.amax(np.asarray([us_counts,ds_counts])))
            us_sufficient_counts = us_counts > (0.075*max_counts)
            ds_sufficient_counts = ds_counts > (0.075*max_counts)

            if us_sufficient_counts and self.us_temperature_model.temperature_error<=self.error_limit:
                us_temperature.append(self.us_temperature_model.temperature)
                us_temperature_error.append(self.us_temperature_model.temperature_error)
            else:
                us_temperature.append(0)
                us_temperature_error.append(0)
            if ds_sufficient_counts and self.ds_temperature_model.temperature_error<=self.error_limit:
                ds_temperature.append(self.ds_temperature_model.temperature)
                ds_temperature_error.append(self.ds_temperature_model.temperature_error)
            else:
                ds_temperature.append(0)
                ds_temperature_error.append(0)

        self.set_img_frame_number_to(cur_frame)
        self.blockSignals(False)
        self.us_temperatures = us_temperature
        self.us_temperatures_errors = us_temperature_error
        self.ds_temperatures = ds_temperature
        self.ds_temperatures_errors = ds_temperature_error
        return us_temperature, us_temperature_error, ds_temperature, ds_temperature_error


class SingleTemperatureModel(QtCore.QObject):
    #data_changed_stm = QtCore.pyqtSignal()

    def __init__(self, ind, roi_data_manager):
        super(SingleTemperatureModel, self).__init__()
        self.ind = ind

        self.data_spectrum = Spectrum([], [])
        self.calibration_spectrum = Spectrum([], [])
        self.corrected_spectrum = Spectrum([], [])
        #self.within_limit = None
        self.response = Spectrum([],[])

        self.temperature_fit_function = fit_black_body_function_wien
        self.subtract_inistu_data_background = True
        self.subtract_inistu_calibration_background = True

        self.filter_oscillation = False

        self._data_img = None
        self._data_img_x_calibration = None
        self._data_img_dimension = None

        self.data_roi_max = 0

        self.roi_data_manager = roi_data_manager

        self._calibration_img = None
        self._calibration_img_x_calibration = None
        self._calibration_img_dimension = None

        self.calibration_parameter = CalibrationParameter()

        self.temperature = np.nan
        self.temperature_error = np.nan
        self.scaling = np.nan
        self.fit_spectrum = Spectrum([], [])

        self.calibration_frames=[None,None]

    @property
    def data_img(self):
        return self._data_img

    def set_data(self, img_data, x_calibration):
        self._data_img = img_data
        self._data_img_x_calibration = x_calibration
        self._data_img_dimension = (img_data.shape[1], img_data.shape[0])


        self._update_data_spectrum()
        self._update_corrected_spectrum()
        self.fit_data()
        #self.data_changed_stm.emit()

    @property
    def calibration_img(self):
        return self._calibration_img
    


    def set_calibration_data(self, img_data_file, x_calibration):
        calibration_frames=self.calibration_frames

        if hasattr(img_data_file,'img'):
            img_data = img_data_file.img
        else:
            img_data =  img_data_file
       
        
        self._calibration_img_x_calibration = x_calibration
        if type(img_data) == list:
            if calibration_frames[0] is not None and calibration_frames[1] is not None:

                img_data_selected = img_data[calibration_frames[0]:calibration_frames[1]+1]
            else:
                img_data_selected = img_data[:]
            # Stack and average along the first dimension of the list
            average_array = np.mean(img_data_selected, axis=0)
            self._calibration_img = average_array
        else:
            if len(img_data.shape) == 3:
                if calibration_frames[0] is not None and calibration_frames[1] is not None:
                    img_data_selected = img_data[calibration_frames[0]:calibration_frames[1]+1]
                else:
                    img_data_selected = img_data[:]
                # Stack and average along the first dimension of the list
                average_array = np.mean(img_data_selected, axis=0)
                self._calibration_img = average_array

            else:
                self._calibration_img = img_data    

        self._calibration_img_dimension = (self._calibration_img.shape[1], self._calibration_img.shape[0])
       

        self._update_calibration_spectrum()
        self._update_corrected_spectrum()
        self.fit_data()
        #self.data_changed_stm.emit()

    def set_temperature_fit_function(self, function_type_str:str):
        if function_type_str == 'wien':
            self.temperature_fit_function = fit_black_body_function_wien
        elif function_type_str == 'plank':
            self.temperature_fit_function = fit_black_body_function

    def reset_calibration_data(self):
        self._calibration_img = None
        self._calibration_img_x_calibration = None
        self._calibration_img_dimension = None

        self.calibration_spectrum = Spectrum([], [])
        self.corrected_spectrum = Spectrum([], [])
        self.fit_spectrum = Spectrum([], [])

        self.temperature = np.nan
        self.temperature_error = np.nan
       
    # setting standard interface
    #########################################################################
    def load_standard_spectrum(self, filename):
        self.calibration_parameter.load_standard_spectrum(filename)
        self._update_all_spectra()
        self.fit_data()

    

    def save_standard_spectrum(self, filename):
        self.calibration_parameter.set_standard_spectrum(self.corrected_spectrum)
        self.calibration_parameter.save_standard_spectrum(filename)
  

    def set_calibration_modus(self, modus):
        self.calibration_parameter.set_modus(modus)
        self._update_all_spectra()
        self.fit_data()

    def set_calibration_temperature(self, temperature):
        self.calibration_parameter.set_temperature(temperature)
        self._update_all_spectra()
        self.fit_data()

    # Spectrum calculations
    #########################################################################

    def columns_within_limit(self, array, limit=65534):
        # Check if any element in each column is above the limit
        above_limit = np.any(array > limit, axis=0)
        return ~above_limit

    def count_columns_above_limit(self, array, limit=65534):
        # Check if any element in each column is above the limit
        above_limit = np.any(array > limit, axis=0)
        # Check if all elements in each column are below or equal to the limit
        below_limit = ~np.any(array > limit, axis=0)
        # Count the number of True values (columns with values above limit)
        above_limit_count = np.sum(above_limit)
        # Count the number of True values (columns with all values below or equal to limit)
        below_limit_count = np.sum(below_limit)
        return above_limit_count, below_limit_count

 

    def _update_data_spectrum(self):
        if self._data_img is not None:
            _data_img_as_array = np.asarray(self._data_img)
            roi = self.roi_data_manager.get_roi(self.ind, self._data_img_dimension)
            if self.subtract_inistu_data_background:
                roi_bg = self.roi_data_manager.get_roi(self.ind+2, self._data_img_dimension)
                roi_bg.x_max = roi.x_max
                roi_bg.x_min = roi.x_min

            roi_img = get_roi_img(_data_img_as_array, roi)
            within_limit = self.columns_within_limit(roi_img)
            if np.any(roi_img > 65534):
                above_limit_count, below_limit_count = self.count_columns_above_limit(roi_img)
                #print("saturated columns = " + str(above_limit_count))

            data_x = self._data_img_x_calibration[int(roi.x_min):int(roi.x_max) + 1]
            data_y = get_roi_sum(_data_img_as_array, roi)
            
            
            self.data_roi_max = get_roi_max(_data_img_as_array, roi)
            if self.subtract_inistu_data_background:
                data_y_bg = get_roi_sum(_data_img_as_array, roi_bg)
                data_y = data_y - data_y_bg
            
            self.total_counts = np.sum(data_y)
            self.data_spectrum.data = data_x, data_y
            self.data_spectrum.mask = within_limit

    def _update_calibration_spectrum(self):
        if self.calibration_img is not None:
            roi = self.roi_data_manager.get_roi(self.ind, self._calibration_img_dimension)
            if self.subtract_inistu_calibration_background:
                roi_bg = self.roi_data_manager.get_roi(self.ind+2, self._calibration_img_dimension)
                roi_bg.x_max = roi.x_max
                roi_bg.x_min = roi.x_min

            calibration_x = self._calibration_img_x_calibration[int(roi.x_min):int(roi.x_max) + 1]
            calibration_y = get_roi_sum(self._calibration_img, roi)
            

            if self.subtract_inistu_calibration_background:
                calibration_bg = get_roi_sum(self._calibration_img, roi_bg)
                calibration_y = calibration_y - calibration_bg
            self.calibration_spectrum.data = calibration_x, calibration_y 

    def _update_corrected_spectrum(self):
        if len(self.data_spectrum) == 0:
            self.corrected_spectrum = Spectrum([], [])
            return

        if len(self.calibration_spectrum._x) == len(self.data_spectrum._x):
            x, _ = self.data_spectrum.data
            lamp_spectrum = self.calibration_parameter.get_lamp_spectrum(x)
            filt_osc = self.filter_oscillation
            self.corrected_spectrum, self.response = calculate_real_spectrum(self.data_spectrum,
                                                              self.calibration_spectrum,
                                                              lamp_spectrum,
                                                              filter_oscillation=filt_osc)
            self.corrected_spectrum.mask = self.data_spectrum.mask
        else:
            self.corrected_spectrum = Spectrum([], [])

    def _update_all_spectra(self):
        self._update_data_spectrum()
        self._update_calibration_spectrum()
        self._update_corrected_spectrum()

    # finally the fitting function
    ##################################################################
    def fit_data(self):
        okay = False
        if self.corrected_spectrum.x.shape[0]>0 and self.corrected_spectrum.y.shape[0]>0:
            if self.corrected_spectrum.mask is not None:
                count_true = np.count_nonzero(self.corrected_spectrum.mask)
                #print('count_true '+ str(count_true))
                if count_true > 20:
                    counts = self.data_spectrum.data[1]
                    average_counts = sum(counts)/len(counts)
                    #print(average_counts)
                    if len(self.corrected_spectrum):
                        
                        if average_counts >3 :
                            #now = time.time()
                            self.temperature, self.temperature_error, self.fit_spectrum, self.scaling = \
                                self.temperature_fit_function(self.corrected_spectrum)
                            okay = True
                            #later = time.time()
                            #elapsed = later - now
                            #print('fit time = ' + str(elapsed))

        if not okay:
            self.temperature = 0
            self.temperature_error = 0
            self.fit_spectrum = Spectrum([],[])
                
    def get2color(self):
        temp = None
        if self.corrected_spectrum.x.shape[0]>0 and self.corrected_spectrum.y.shape[0]>0:
            if self.corrected_spectrum.mask is not None:
                count_true = np.count_nonzero(self.corrected_spectrum.mask)
                #print('count_true '+ str(count_true))
                if count_true > 20:
                    wav = self.corrected_spectrum.x
                    spec = self.corrected_spectrum.y
                    lam1, temp = calculate_2_color(wav,spec)

        return lam1, temp
    
# HELPER FUNCTIONS
###############################################
###############################################



def calculate_real_spectrum(data_spectrum, calibration_spectrum, standard_spectrum, filter_oscillation=False):
    response_y = calibration_spectrum._y / standard_spectrum._y
    response_y[np.where(response_y == 0)] = np.nan
    response = Spectrum(data_spectrum._x, response_y)
    
    corrected_y = data_spectrum._y / response_y
    corrected_y = corrected_y / np.max(corrected_y) * np.max(data_spectrum._y)

    if filter_oscillation:
        corrected_y = filter_oscillatory_component(data_spectrum._x, corrected_y)

    return Spectrum(data_spectrum._x, corrected_y), response


def fit_black_body_function(spectrum):
    data = spectrum.data_masked
    _x = data[0]
    _y = data[1]
    try:
        param, cov = curve_fit(black_body_function, _x, _y, p0=[2500, 1e-11])
        T = param[0]
        scaling = param[1]
        T_err = np.sqrt(cov[0, 0])

        return T, T_err, Spectrum(spectrum._x, black_body_function(spectrum._x, param[0], param[1])), scaling
    except Exception as e:
        #print(f"Fit failed with error: {e}")
        return np.nan, np.nan, Spectrum([], []), np.nan
    
def fit_black_body_function_wien(spectrum):
    data = spectrum.data_masked
    _x = data[0]
    _y = data[1]
    _y [_y <0] = 0.1
    x, y = wien_pre_transform(_x * 1e-9, _y)
    
    #av = np.average(y)
    m, b, m_std_dev_res = fit_linear(x,y, True)
    T, T_std_dev = m_to_T(m, m_std_dev_res)
    
    
    wavelength, best_fit = m_b_wien(_x * 1e-9, m, b)
    sp = Spectrum(wavelength *1e9, best_fit)
    sp.mask = spectrum.mask
    
    return T, T_std_dev, sp, np.nan
    

def black_body_function(wavelength, temp, scaling):
    wavelength = np.array(wavelength) * 1e-9
    c1 = 3.7418e-16
    c2 = 0.014388
    return scaling * c1 * wavelength ** -5 / (np.exp(c2 / (wavelength * temp)) - 1)




class CalibrationParameter(object):
    def __init__(self, modus=0):
        self.modus = modus
        # modi: 0 - given temperature
        # 1 - standard spectrum

        self.temperature = 2000
        self.standard_spectrum_func = None
        self._standard_x = np.array([])
        self._standard_y = np.array([])
        self.standard_file_name = 'Select File...'

    def set_modus(self, modus):
        modus = int(modus)
        self.modus = modus

    def set_temperature(self, temperature):
        self.temperature = temperature

    

    def load_standard_spectrum(self, filename):
        try:
            data = np.loadtxt(filename, delimiter=',')
        except ValueError:
            try:
                data = np.loadtxt(filename, delimiter=' ')
            except ValueError:
                try:
                    data = np.loadtxt(filename, delimiter=';')
                except ValueError:
                    data = np.loadtxt(filename, delimiter='\t')
        self._standard_x = data.T[0]
        self._standard_y = data.T[1]

        self.standard_file_name = filename

    def save_standard_spectrum(self, filename):
        spectrum = self.get_standard_spectrum()
        data = np.transpose(np.asarray([spectrum.x,spectrum.y]))
        np.savetxt(filename,data)

    def get_lamp_y(self, wavelength):
        if self.modus == 0:
            y = black_body_function(wavelength, self.temperature, 1)
            return y / max(y)
        elif self.modus == 1:
            try:
                # return self.standard_spectrum_func(wavelength)
                # not used because scipy.interpolate is supported by pyinstaller...
                return np.interp(wavelength, self._standard_x, self._standard_y)
            except ValueError:
                return np.ones(np.size(wavelength))

    def get_lamp_spectrum(self, wavelength):
        return Spectrum(wavelength, self.get_lamp_y(wavelength))

    def get_standard_filename(self):
        return self.standard_file_name

    def set_standard_filename(self, filename):
        self.standard_file_name = filename

    def get_standard_spectrum(self):
        return Spectrum(self._standard_x, self._standard_y)

    def set_standard_spectrum(self, spectrum):
        try:
            self._standard_x = spectrum.x
            self._standard_y = spectrum.y
        except AttributeError:
            pass
