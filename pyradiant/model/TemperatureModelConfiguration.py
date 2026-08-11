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
import json
from .data_models.DataModel import DataModel
from .Spectrum import Spectrum
from .RoiData import RoiDataManager, Roi, get_roi_max, get_roi_sum, get_roi_img, validate_roi
from .data_models.SpeFile import SpeFile
from .data_models.H5File import H5File
from .data_models.TifFile import TifFile
from .helper import FileNameIterator
from .radiation import fit_linear, wien_pre_transform, m_to_T, m_b_wien
from .helper.HelperModule import get_partial_index, get_partial_value
from .TwoColor import calculate_2_color
from .helper.signal import Signal

from scipy.interpolate import interp1d

from .helper.filter_oscillation import filter_oscillatory_component
from .temperature_pipeline import pipeline, Stage


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

        # Background/dark subtraction mode. One setting governs both data and
        # calibration extraction.
        #   'insitu'      : sum a per-side ROI on the same frame (indices 2/3).
        #                    Default; matches historical behaviour.
        #   'prerecorded' : subtract a stored dark image per side, scaled by
        #                    ds/us_dark_frame_scale, from the SAME ROI as the
        #                    data extraction.
        #   'off'         : no background subtraction.
        self.background_mode = 'insitu'
        # Prerecorded dark frames per side. Image bytes are stored on the config
        # (and embedded in the .trs) so the config is self-contained after the
        # source file is gone. Scale is a multiplier applied before subtraction
        # (e.g. exposure-time compensation).
        self.ds_dark_frame_img = None
        self.us_dark_frame_img = None
        self.ds_dark_frame_filename = None
        self.us_dark_frame_filename = None
        self.ds_dark_frame_scale = 1.0
        self.us_dark_frame_scale = 1.0

        self.x_calibration = None

        # Wavelength calibration for TIFF files (Photron camera etc.).
        # Populated by load_photron_wavelength_calibration(); persisted in .trs.
        # Shape: {'polynomial_coeffs': [c0, c1, c2],  # ascending, zero-indexed
        #         'convention': 'ascending_zero_indexed',
        #         'source_filename': '/path/to/calibration.json',
        #         ...other metadata...}
        self.photron_wavelength_calibration = None

        self.temperature_fit_function_str = 'plank'

        # Measurement mode: 'dual' (downstream + upstream, classical DAC geometry)
        # or 'single' (one spectrum only, e.g. Photron on Acton). In 'single' the
        # us-side compute/UI/output is gated off; the us model stays instantiated
        # so the existing property surface and .trs schema remain intact.
        self.mode = 'dual'

        # True when this configuration has unsaved changes (data/calibration/ROI/etc.
        # loaded or modified since the last save_setting or load_setting call).
        # Consumed at app-close to prompt for saving.
        self.dirty = False

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

    # Photron TIFF wavelength calibration (explicit load; per-configuration)
    #########################################################################
    def load_photron_wavelength_calibration(self, json_filename):
        """Load a wavelength-calibration JSON produced by photron/calibrate.py.

        Validates presence of polynomial_coeffs and stores the dict on the
        configuration. The convention is ascending-order coefficients evaluated
        at zero-indexed pixel positions.

        Raises ValueError if the file is malformed.
        """
        with open(json_filename, 'r') as f:
            data = json.load(f)
        if 'polynomial_coeffs' not in data:
            raise ValueError(
                f"{json_filename}: missing required 'polynomial_coeffs' field"
            )
        coeffs = data['polynomial_coeffs']
        if not (isinstance(coeffs, list) and len(coeffs) >= 1
                and all(isinstance(c, (int, float)) for c in coeffs)):
            raise ValueError(
                f"{json_filename}: 'polynomial_coeffs' must be a list of numbers"
            )
        data['source_filename'] = os.path.abspath(json_filename)
        self.photron_wavelength_calibration = data
        self.dirty = True

    def clear_photron_wavelength_calibration(self):
        if self.photron_wavelength_calibration is not None:
            self.dirty = True
        self.photron_wavelength_calibration = None

    def _photron_coeffs(self):
        """Return the current wavelength-polynomial coefficients, or None."""
        if self.photron_wavelength_calibration is None:
            return None
        return self.photron_wavelength_calibration.get('polynomial_coeffs')

    # loading spe or h5 image files:
    #########################################################################
    def _load_raw_data(self, filename, area_detector=None):
        """Stage LOAD helper: file I/O → data_img_file, _data_img, x_calibration.

        Stores the image on each SingleTemperatureModel (store-only; no computation).
        Called by TemperaturePipeline._stage_load() for the normal file path, and
        also directly by load_data_image() for the area-detector path.
        """
        if area_detector is None:
            if not self.filename or not os.path.dirname(self.filename) == os.path.dirname(filename):
                lf = self.create_log_file(os.path.dirname(filename))
                if lf is not None:
                    self.log_file_loaded_signal.emit()
            self.filename = filename
            _, file_extension = os.path.splitext(filename)
            if file_extension == '.spe' or file_extension == '.SPE':
                self.data_img_file = SpeFile(filename)
            elif file_extension == '.h5':
                self.data_img_file = H5File(filename, self.x_calibration)
            elif file_extension.lower() in ('.tif', '.tiff'):
                self.data_img_file = TifFile(filename, self._photron_coeffs())
            self._filename_iterator.update_filename(filename)
            self.mtime = self.get_last_modified_time(filename)
            self.dirty = True
        else:
            area_detector.update_data()
            self.data_img_file = area_detector

        if self.data_img_file.num_frames > 1:
            if not (self.current_frame >= 0 and self.current_frame < self.data_img_file.num_frames):
                self.current_frame = 0
            self._data_img = self.data_img_file.img[self.current_frame]
        else:
            self.current_frame = 0
            self._data_img = self.data_img_file.img

        # Store image data on each model (store-only; pipeline handles computation).
        self._update_temperature_models_data()

    def load_data_image(self, filename, area_detector=None):
        """Load a data file and run the full calculation pipeline.

        Stages: LOAD (file I/O + store) → DATA_SPEC → CALIB_SPEC → CORRECT → FIT.
        Emits data_changed_signal when done.
        """
        if area_detector is None:
            pipeline.run(self, Stage.LOAD, filename)
        else:
            # Area-detector path: file I/O handled inline, then compute from DATA_SPEC.
            self._load_raw_data(filename, area_detector)
            pipeline.run(self, Stage.DATA_SPEC)
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
        self._update_temperature_models_data()   # store new frame image on each model
        pipeline.run(self, Stage.DATA_SPEC)      # re-extract spectra, correct, fit
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
        us_counts_str = format(self.us_data_spectrum.counts, ".3e")
        # In single-sided mode the us column set is meaningless; blank it out so
        # downstream log consumers see the fixed schema but with 0s for us.
        if self.mode == 'single':
            us_temp = '0'
            us_temperature_error = '0'
            us_scaling = '0'
            us_counts_str = '0'
        frame_s = str(frame + 1)
        log_data = (os.path.basename(self.filename), frame_s, os.path.dirname(self.filename), ds_temp, us_temp,
                    ds_temperature_error, us_temperature_error,
                    self.data_img_file.detector, str(self.data_img_file.exposure_time),str(self.data_img_file.gain),
                    ds_scaling, us_scaling,
                    format(self.ds_data_spectrum.counts, ".3e"), us_counts_str)
        
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
            self.ds_temperature_model.set_temperature_fit_function(function_type)
            self.us_temperature_model.set_temperature_fit_function(function_type)
            pipeline.run(self, Stage.FIT)
            # Set dirty BEFORE the emit so the button-refresh sees the new state.
            self.dirty = True
            self.data_changed_emit(self.current_frame)

    def set_mode(self, mode):
        if mode not in ('dual', 'single'):
            raise ValueError(f"mode must be 'dual' or 'single', got {mode!r}")
        if mode == self.mode:
            return
        self.mode = mode
        self.dirty = True
        # Re-run the pipeline so the plots reflect the new mode (us-side
        # results become stale/hidden in single; recomputed in dual).
        if self._data_img is not None:
            pipeline.run(self, Stage.DATA_SPEC)
        self.data_changed_emit(self.current_frame)



    def set_use_insitu_background(self, use_data_background, use_calibration_background):
        if use_data_background != self.use_insitu_data_background or use_calibration_background != self.use_insitu_calibration_background:
            self.use_insitu_data_background = use_data_background
            self.use_insitu_calibration_background = use_calibration_background
            self.ds_temperature_model.subtract_inistu_data_background = use_data_background
            self.us_temperature_model.subtract_inistu_data_background = use_data_background
            self.ds_temperature_model.subtract_inistu_calibration_background = use_calibration_background
            self.us_temperature_model.subtract_inistu_calibration_background = use_calibration_background
            # Background flag affects both data and calibration extraction.
            pipeline.run(self, Stage.DATA_SPEC)
            self.dirty = True
            self.data_changed_emit(self.current_frame)

    # -------- Background subtraction (mode + prerecorded dark) ----------------
    _VALID_BACKGROUND_MODES = ('insitu', 'prerecorded', 'off')

    def _propagate_dark_to_models(self):
        """Push the current mode + per-side dark image + scale to both single models."""
        for side, img, scale in (('ds', self.ds_dark_frame_img, self.ds_dark_frame_scale),
                                  ('us', self.us_dark_frame_img, self.us_dark_frame_scale)):
            model = self.ds_temperature_model if side == 'ds' else self.us_temperature_model
            model.background_mode = self.background_mode
            model.dark_frame_img = img
            model.dark_frame_scale = float(scale)

    def set_background_mode(self, mode):
        if mode not in self._VALID_BACKGROUND_MODES:
            raise ValueError(f"background mode must be one of {self._VALID_BACKGROUND_MODES}, got {mode!r}")
        if mode == self.background_mode:
            return
        self.background_mode = mode
        # Keep the legacy convenience bools in sync for any external readers
        # (log-file writer path, etc.) that still consult them.
        insitu = (mode == 'insitu')
        self.use_insitu_data_background = insitu
        self.use_insitu_calibration_background = insitu
        self.ds_temperature_model.subtract_inistu_data_background = insitu
        self.us_temperature_model.subtract_inistu_data_background = insitu
        self.ds_temperature_model.subtract_inistu_calibration_background = insitu
        self.us_temperature_model.subtract_inistu_calibration_background = insitu
        self._propagate_dark_to_models()
        pipeline.run(self, Stage.DATA_SPEC)
        self.dirty = True
        self.data_changed_emit(self.current_frame)

    def _load_dark_frame_file(self, filename):
        """Dispatch to SpeFile / H5File / TifFile — mirrors load_ds_calibration_image."""
        _, ext = os.path.splitext(filename)
        ext = ext.lower()
        if ext == '.spe':
            f = SpeFile(filename)
        elif ext == '.h5':
            f = H5File(filename, self.x_calibration)
        elif ext in ('.tif', '.tiff'):
            f = TifFile(filename, self._photron_coeffs())
        else:
            raise ValueError(f"Unsupported dark-frame extension: {ext}")
        img = f.img
        if isinstance(img, list):
            img = img[0]  # multi-frame → average or first? use first for now
        return np.asarray(img)

    def load_ds_dark_frame(self, filename):
        self.ds_dark_frame_img = self._load_dark_frame_file(filename)
        self.ds_dark_frame_filename = filename
        self._propagate_dark_to_models()
        pipeline.run_ds(self, Stage.DATA_SPEC)
        self.dirty = True
        self.ds_calculations_changed_emit()

    def load_us_dark_frame(self, filename):
        self.us_dark_frame_img = self._load_dark_frame_file(filename)
        self.us_dark_frame_filename = filename
        self._propagate_dark_to_models()
        pipeline.run_us(self, Stage.DATA_SPEC)
        self.dirty = True
        self.us_calculations_changed_emit()

    def clear_ds_dark_frame(self):
        if self.ds_dark_frame_img is None and self.ds_dark_frame_filename is None:
            return
        self.ds_dark_frame_img = None
        self.ds_dark_frame_filename = None
        self._propagate_dark_to_models()
        pipeline.run_ds(self, Stage.DATA_SPEC)
        self.dirty = True
        self.ds_calculations_changed_emit()

    def clear_us_dark_frame(self):
        if self.us_dark_frame_img is None and self.us_dark_frame_filename is None:
            return
        self.us_dark_frame_img = None
        self.us_dark_frame_filename = None
        self._propagate_dark_to_models()
        pipeline.run_us(self, Stage.DATA_SPEC)
        self.dirty = True
        self.us_calculations_changed_emit()

    def set_ds_dark_frame_scale(self, scale):
        scale = float(scale)
        if scale == self.ds_dark_frame_scale:
            return
        self.ds_dark_frame_scale = scale
        self.ds_temperature_model.dark_frame_scale = scale
        pipeline.run_ds(self, Stage.DATA_SPEC)
        self.dirty = True
        self.ds_calculations_changed_emit()

    def set_us_dark_frame_scale(self, scale):
        scale = float(scale)
        if scale == self.us_dark_frame_scale:
            return
        self.us_dark_frame_scale = scale
        self.us_temperature_model.dark_frame_scale = scale
        pipeline.run_us(self, Stage.DATA_SPEC)
        self.dirty = True
        self.us_calculations_changed_emit()

    def _update_temperature_models_data(self):

        self.ds_temperature_model.set_temperature_fit_function(self.temperature_fit_function_str)
        self.us_temperature_model.set_temperature_fit_function(self.temperature_fit_function_str)

        self.x_calibration = self.data_img_file.x_calibration
        self.ds_temperature_model.set_data(self._data_img,
                                           self.data_img_file.x_calibration)
        if self.mode == 'dual':
            self.us_temperature_model.set_data(self._data_img,
                                               self.data_img_file.x_calibration)

    @property
    def data_img(self):
        return self._data_img

    @data_img.setter
    def data_img(self, value):
        self._data_img = value
        self._update_temperature_models_data()
        pipeline.run(self, Stage.DATA_SPEC)
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
        elif str.lower(file_extension) in ('.tif', '.tiff'):
            self.ds_calibration_img_file = TifFile(filename, self._photron_coeffs())

        #self.ds_calibration_img_file = SpeFile(filename)
        
        self.ds_calibration_filename = filename
        self.ds_set_calibration_data()
        self.dirty = True
        self.ds_calculations_changed_emit()

    def ds_set_calibration_data(self):
        self.ds_temperature_model.set_calibration_data(self.ds_calibration_img_file,
                                                       self.ds_calibration_img_file.x_calibration)
        pipeline.run_ds(self, Stage.CALIB_SPEC)

    def load_us_calibration_image(self, filename):
        # Get the extension
        _, file_extension = os.path.splitext(filename)
        if str.lower(file_extension)  == '.spe':
            self.us_calibration_img_file = SpeFile(filename)
        elif str.lower(file_extension)  == '.h5':
            self.us_calibration_img_file = H5File(filename,self.x_calibration)
        elif str.lower(file_extension) in ('.tif', '.tiff'):
            self.us_calibration_img_file = TifFile(filename, self._photron_coeffs())

        #self.us_calibration_img_file = SpeFile(filename)
        self.us_calibration_filename = filename


        self.us_set_calibration_data()
        self.dirty = True
        self.us_calculations_changed_emit()

    def us_set_calibration_data(self):
        self.us_temperature_model.set_calibration_data(self.us_calibration_img_file,
                                                       self.us_calibration_img_file.x_calibration)
        pipeline.run_us(self, Stage.CALIB_SPEC)

    def clear_ds_calibration_image(self):
        """Drop the DS intensity calibration and switch to an identity transfer
        function (response = 1). Corrected spectrum falls back to raw data."""
        self.ds_calibration_img_file = None
        self.ds_calibration_filename = None
        self.ds_temperature_model.reset_calibration_data()
        self.ds_temperature_model._identity_calibration = True
        pipeline.run_ds(self, Stage.CORRECT)
        # Set dirty BEFORE emitting so the downstream button-refresh sees the new state.
        self.dirty = True
        self.ds_calculations_changed_emit()

    def clear_us_calibration_image(self):
        """Drop the US intensity calibration and switch to an identity transfer
        function (response = 1). Corrected spectrum falls back to raw data."""
        self.us_calibration_img_file = None
        self.us_calibration_filename = None
        self.us_temperature_model.reset_calibration_data()
        self.us_temperature_model._identity_calibration = True
        pipeline.run_us(self, Stage.CORRECT)
        self.dirty = True
        self.us_calculations_changed_emit()


    # setting standard interface
    #########################################################################
        
   

    def load_ds_standard_spectrum(self, filename):
        self.ds_temperature_model.load_standard_spectrum(filename)
        pipeline.run_ds(self, Stage.CORRECT)
        self.dirty = True
        self.ds_calculations_changed_emit()

    def load_us_standard_spectrum(self, filename):
        self.us_temperature_model.load_standard_spectrum(filename)
        pipeline.run_us(self, Stage.CORRECT)
        self.dirty = True
        self.us_calculations_changed_emit()

    def save_ds_standard_spectrum(self, filename):
        self.ds_temperature_model.save_standard_spectrum(filename)

    def save_us_standard_spectrum(self, filename):
        self.us_temperature_model.save_standard_spectrum(filename)

    def set_ds_calibration_modus(self, modus):
        self.ds_temperature_model.set_calibration_modus(modus)
        pipeline.run_ds(self, Stage.CORRECT)
        self.dirty = True
        self.ds_calculations_changed_emit()

    def set_us_calibration_modus(self, modus):
        self.us_temperature_model.set_calibration_modus(modus)
        pipeline.run_us(self, Stage.CORRECT)
        self.dirty = True
        self.us_calculations_changed_emit()

    def set_ds_calibration_temperature(self, temperature):
        self.ds_temperature_model.set_calibration_temperature(temperature)
        pipeline.run_ds(self, Stage.CORRECT)
        self.dirty = True
        self.ds_calculations_changed_emit()

    def set_us_calibration_temperature(self, temperature):
        self.us_temperature_model.set_calibration_temperature(temperature)
        pipeline.run_us(self, Stage.CORRECT)
        self.dirty = True
        self.us_calculations_changed_emit()

    def save_setting(self, filename):
        f = h5py.File(filename, 'w')

        f.attrs['mode'] = self.mode
        # Background subtraction mode (see set_background_mode). Legacy
        # subtract_bg attrs on ds/us image groups are also written below for
        # cross-version reads.
        f.attrs['background_mode'] = self.background_mode
        # Save the data image dimension so ROIs can be restored even for configs
        # that carry no intensity calibration image (e.g. single-sided TIFF setups).
        # The subsequent workspace data-file load uses the same dimension key,
        # so the stored ROIs get picked up automatically.
        if self.data_img_file is not None:
            xdim, ydim = self.data_img_file.get_dimension()
            f.attrs['data_img_xdim'] = int(xdim)
            f.attrs['data_img_ydim'] = int(ydim)

        # Prerecorded dark frames per side (only stored when present). The
        # image is embedded in the .trs so the config is self-contained even
        # if the original dark file is gone.
        if self.ds_dark_frame_img is not None:
            f['ds_dark_frame'] = np.asarray(self.ds_dark_frame_img)
            f['ds_dark_frame'].attrs['filename'] = str(self.ds_dark_frame_filename or '')
            f['ds_dark_frame'].attrs['scale'] = float(self.ds_dark_frame_scale)
        if self.us_dark_frame_img is not None:
            f['us_dark_frame'] = np.asarray(self.us_dark_frame_img)
            f['us_dark_frame'].attrs['filename'] = str(self.us_dark_frame_filename or '')
            f['us_dark_frame'].attrs['scale'] = float(self.us_dark_frame_scale)

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

        ds_group.attrs['identity_calibration'] = bool(
            self.ds_temperature_model._identity_calibration)
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
        us_group.attrs['identity_calibration'] = bool(
            self.us_temperature_model._identity_calibration)
        us_group['roi'] = self.us_roi.as_list()
        us_group['roi_bg'] = self.us_roi_bg.as_list()
        us_group['modus'] = self.us_temperature_model.calibration_parameter.modus
        us_group['temperature'] = self.us_temperature_model.calibration_parameter.temperature
        us_group['standard_spectrum'] = self.us_temperature_model.calibration_parameter.get_standard_spectrum().data
        us_group['standard_spectrum'].attrs['filename'] = \
            self.us_temperature_model.calibration_parameter.get_standard_filename()
        us_group['standard_spectrum'].attrs['subtract_bg'] = self.use_insitu_calibration_background

        if self.photron_wavelength_calibration is not None:
            wl_group = f.create_group('photron_wavelength_calibration')
            wl_group.attrs['polynomial_coeffs'] = np.asarray(
                self.photron_wavelength_calibration['polynomial_coeffs'], dtype=float
            )
            wl_group.attrs['convention'] = self.photron_wavelength_calibration.get(
                'convention', 'ascending_zero_indexed'
            )
            wl_group.attrs['source_filename'] = str(
                self.photron_wavelength_calibration.get('source_filename', '')
            )

        f.close()
        self.setting_filename = filename
        # Keep the working dir in sync with the file we just wrote so
        # workspace-save (which combines _setting_working_dir + basename(setting_filename))
        # can't record a mismatched path if a caller invoked save_setting
        # directly without going through the controller's save_setting_file.
        self._setting_working_dir = os.path.dirname(filename)
        self.dirty = False

    
    def _roi_dimension_key(self, f, side_group):
        """Pick the (xdim, ydim) dimension under which to key the restored ROIs.

        Priority:
          1. This side's calibration image shape (existing behavior when a cal
             image is saved — the ROI belongs to the calibration image).
          2. The data image dimension saved in root attrs — matches the
             dimension the workspace-restore data-file load will use, so the
             ROIs get picked up automatically once data lands.
          3. The already-loaded data_img_file's dimension (if any).
          4. None — skip ROI restore.
        """
        if 'image' in side_group:
            img = side_group['image']
            shape = img.shape
            if len(shape) == 2:
                return (shape[1], shape[0])
            elif len(shape) == 3:
                return (shape[2], shape[1])
        if 'data_img_xdim' in f.attrs and 'data_img_ydim' in f.attrs:
            return (int(f.attrs['data_img_xdim']), int(f.attrs['data_img_ydim']))
        if self.data_img_file is not None:
            return self.data_img_file.get_dimension()
        return None

    def load_setting(self, filename):
        f = h5py.File(filename, 'r')
        self.mode = str(f.attrs.get('mode', 'dual'))
        # Background subtraction: prefer the new attr; fall back to the legacy
        # per-group subtract_bg bool (True → 'insitu', False → 'off').
        if 'background_mode' in f.attrs:
            self.background_mode = str(f.attrs['background_mode'])
        else:
            legacy_bg = True
            if 'downstream_calibration' in f and 'image' in f['downstream_calibration']:
                a = f['downstream_calibration']['image'].attrs
                if 'subtract_bg' in a:
                    legacy_bg = bool(a['subtract_bg'])
            self.background_mode = 'insitu' if legacy_bg else 'off'
        # Prerecorded dark frames (optional; only present when saved).
        if 'ds_dark_frame' in f:
            self.ds_dark_frame_img = f['ds_dark_frame'][...]
            self.ds_dark_frame_filename = str(f['ds_dark_frame'].attrs.get('filename', ''))
            self.ds_dark_frame_scale = float(f['ds_dark_frame'].attrs.get('scale', 1.0))
        else:
            self.ds_dark_frame_img = None
            self.ds_dark_frame_filename = None
            self.ds_dark_frame_scale = 1.0
        if 'us_dark_frame' in f:
            self.us_dark_frame_img = f['us_dark_frame'][...]
            self.us_dark_frame_filename = str(f['us_dark_frame'].attrs.get('filename', ''))
            self.us_dark_frame_scale = float(f['us_dark_frame'].attrs.get('scale', 1.0))
        else:
            self.us_dark_frame_img = None
            self.us_dark_frame_filename = None
            self.us_dark_frame_scale = 1.0
        ds_group = f['downstream_calibration']
        # DS intensity calibration image (optional — single-sided/no-cal configs skip)
        if 'image' in ds_group:
            ds_img = ds_group['image'][...]

            self.ds_calibration_filename = ds_group['image'].attrs['filename']
            x_calibration = ds_group['image'].attrs['x_calibration'][...]
            if 'subtract_bg' in ds_group['image'].attrs:
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
        else:
            self.ds_temperature_model.reset_calibration_data()
            self.ds_calibration_filename = None
            self.ds_calibration_img_file = None

        # DS ROIs — saved independently of whether an intensity calibration image
        # is present. Key them on the calibration-image dimension when we have
        # one, otherwise on the data-image dimension saved in root attrs (which
        # matches the dimension the data file will load with).
        ds_dim = self._roi_dimension_key(f, ds_group)
        if ds_dim is not None and 'roi' in ds_group and 'roi_bg' in ds_group:
            self.roi_data_manager.set_roi(0, ds_dim, ds_group['roi'][...])
            self.roi_data_manager.set_roi(2, ds_dim, ds_group['roi_bg'][...])

        # Restore the identity-calibration flag (True = user cleared).
        self.ds_temperature_model._identity_calibration = bool(
            ds_group.attrs.get('identity_calibration', False))

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
        us_img = None
        if 'image' in us_group:
            us_img = us_group['image'][...]

            self.us_calibration_filename = us_group['image'].attrs['filename']
            self.us_temperature_model.set_calibration_data(us_img,
                                                           us_group['image'].attrs['x_calibration'][...])
        else:
            self.us_temperature_model.reset_calibration_data()
            self.us_calibration_filename = None

        # US ROIs — saved independently of whether an intensity calibration
        # image is present (same treatment as DS above).
        us_dim = self._roi_dimension_key(f, us_group)
        if us_dim is not None and 'roi' in us_group and 'roi_bg' in us_group:
            self.roi_data_manager.set_roi(1, us_dim, us_group['roi'][...])
            self.roi_data_manager.set_roi(3, us_dim, us_group['roi_bg'][...])

        # Restore the identity-calibration flag (True = user cleared).
        self.us_temperature_model._identity_calibration = bool(
            us_group.attrs.get('identity_calibration', False))

        standard_data = us_group['standard_spectrum'][...]
        self.us_temperature_model.calibration_parameter.set_standard_spectrum(Spectrum(standard_data[0, :],
                                                                                     standard_data[1, :]))
        
        if 'image' in us_group and 'subtract_bg' in us_group['image'].attrs:
            use_data_bg = bool(us_group['image'].attrs['subtract_bg'])
            self.use_insitu_data_background = use_data_bg

        try:
            self.us_temperature_model.calibration_parameter.standard_file_name = \
                us_group['standard_spectrum'].attrs['filename']
        except AttributeError:
            self.us_temperature_model.calibration_parameter.standard_file_name = \
                us_group['standard_spectrum'].attrs['filename']

        # Only stash a synthetic us_calibration_img_file when we actually loaded
        # an image; otherwise leave it None (fresh/single-sided/no-cal state).
        if 'image' in us_group:
            self.us_calibration_img_file = DataModel()
            self.us_calibration_img_file.img = us_img
            self.us_calibration_img_file.x_calibration = self.x_calibration
            self.us_calibration_img_file.filename = self.us_calibration_filename
        else:
            self.us_calibration_img_file = None

        modus = int(us_group['modus'][...])
        self.us_temperature_model.calibration_parameter.set_modus(modus)
        temperature = float(us_group['temperature'][...])
        self.us_temperature_model.calibration_parameter.set_temperature(temperature)

        if 'photron_wavelength_calibration' in f:
            wl_group = f['photron_wavelength_calibration']
            coeffs = wl_group.attrs['polynomial_coeffs'][...].tolist()
            convention = wl_group.attrs.get('convention', 'ascending_zero_indexed')
            source_filename = wl_group.attrs.get('source_filename', '')
            self.photron_wavelength_calibration = {
                'polynomial_coeffs': coeffs,
                'convention': str(convention),
                'source_filename': str(source_filename),
            }
        else:
            self.photron_wavelength_calibration = None

        # Propagate the loaded background mode + dark frames to the two single
        # temperature models before the pipeline re-runs, so extraction picks
        # them up on the very first pass after restore.
        insitu = (self.background_mode == 'insitu')
        for m in (self.ds_temperature_model, self.us_temperature_model):
            m.subtract_inistu_data_background = insitu
            m.subtract_inistu_calibration_background = insitu
        self._propagate_dark_to_models()

        pipeline.run(self, Stage.DATA_SPEC)

        self.data_changed_emit(self.current_frame)

        self.setting_filename = filename
        self._setting_working_dir = os.path.dirname(filename)
        self.dirty = False


    

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
        save_filtered = self.save_filtered_spectrum
        ds_filter_active = self.ds_temperature_model.filter_oscillation and \
                           self.ds_temperature_model.fringe_frequency is not None
        us_filter_active = self.us_temperature_model.filter_oscillation and \
                           self.us_temperature_model.fringe_frequency is not None

        header = "Fitted Temperatures:\n"
        header += "Downstream (K): {:.1f}\t{:.1f}\n".format(self.ds_temperature, self.ds_temperature_error)
        header += "Upstream (K): {:.1f}\t{:.1f}\n".format(self.us_temperature, self.us_temperature_error)

        if save_filtered and ds_filter_active:
            f = self.ds_temperature_model.fringe_frequency
            nd = self.ds_temperature_model.fringe_nd_um
            header += "DS Fringe frequency (cm): {:.4f}\n".format(f)
            header += "DS n*d (um): {:.2f}\n".format(nd)
        if save_filtered and us_filter_active:
            f = self.us_temperature_model.fringe_frequency
            nd = self.us_temperature_model.fringe_nd_um
            header += "US Fringe frequency (cm): {:.4f}\n".format(f)
            header += "US n*d (um): {:.2f}\n".format(nd)

        header += "\nDatacolumns:\n"

        if save_filtered and ds_filter_active:
            header_ds = header + "\t".join(("wavelength(nm)", "DS_data", "DS_filtered", "DS_fit"))
        else:
            header_ds = header + "\t".join(("wavelength(nm)", "DS_data", "DS_fit"))

        if save_filtered and us_filter_active:
            header_us = header + "\t".join(("wavelength(nm)", "US_data", "US_filtered", "US_fit"))
        else:
            header_us = header + "\t".join(("wavelength(nm)", "US_data", "US_fit"))

        ds_filename = filename.rsplit('.', 1)[0] + '_ds.txt'
        us_filename = filename.rsplit('.', 1)[0] + '_us.txt'

        if self.ds_fit_spectrum.y.size == self.ds_corrected_spectrum.y.size:
            if save_filtered and ds_filter_active:
                output_matrix_ds = np.vstack((self.ds_data_spectrum.x,
                                              self.ds_unfiltered_corrected_spectrum.y,
                                              self.ds_corrected_spectrum.y,
                                              self.ds_fit_spectrum.y))
            else:
                output_matrix_ds = np.vstack((self.ds_data_spectrum.x,
                                              self.ds_corrected_spectrum.y,
                                              self.ds_fit_spectrum.y))
            np.savetxt(ds_filename, output_matrix_ds.T, header=header_ds)

        if self.us_corrected_spectrum.y.size == self.us_fit_spectrum.y.size:
            if save_filtered and us_filter_active:
                output_matrix_us = np.vstack((self.us_data_spectrum.x,
                                              self.us_unfiltered_corrected_spectrum.y,
                                              self.us_corrected_spectrum.y,
                                              self.us_fit_spectrum.y))
            else:
                output_matrix_us = np.vstack((self.us_data_spectrum.x,
                                              self.us_corrected_spectrum.y,
                                              self.us_fit_spectrum.y))
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
        pipeline.run_ds(self, Stage.DATA_SPEC)
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
        pipeline.run_us(self, Stage.DATA_SPEC)
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
        pipeline.run_ds(self, Stage.DATA_SPEC)
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
        pipeline.run_us(self, Stage.DATA_SPEC)
        self.us_calculations_changed_emit()

    @property
    def ds_filter_oscillation(self):
        return self.ds_temperature_model.filter_oscillation
    
    @ ds_filter_oscillation.setter
    def ds_filter_oscillation(self, apply_filter):
        self.ds_temperature_model.filter_oscillation = apply_filter
        pipeline.run_ds(self, Stage.CORRECT)
        self.ds_calculations_changed_emit()

    @property
    def us_filter_oscillation(self):
        return self.ds_temperature_model.filter_oscillation
    
    @ us_filter_oscillation.setter
    def us_filter_oscillation(self, apply_filter):
        self.us_temperature_model.filter_oscillation = apply_filter
        pipeline.run_us(self, Stage.CORRECT)
        self.us_calculations_changed_emit()

    @property
    def filter_freq_min(self):
        return self.ds_temperature_model.filter_freq_min

    @filter_freq_min.setter
    def filter_freq_min(self, value):
        self.ds_temperature_model.filter_freq_min = value
        self.us_temperature_model.filter_freq_min = value
        pipeline.run(self, Stage.CORRECT)
        self.ds_calculations_changed_emit()
        self.us_calculations_changed_emit()

    @property
    def filter_freq_max(self):
        return self.ds_temperature_model.filter_freq_max

    @filter_freq_max.setter
    def filter_freq_max(self, value):
        self.ds_temperature_model.filter_freq_max = value
        self.us_temperature_model.filter_freq_max = value
        pipeline.run(self, Stage.CORRECT)
        self.ds_calculations_changed_emit()
        self.us_calculations_changed_emit()

    @property
    def save_filtered_spectrum(self):
        return self.ds_temperature_model.save_filtered_spectrum

    @save_filtered_spectrum.setter
    def save_filtered_spectrum(self, value):
        self.ds_temperature_model.save_filtered_spectrum = value
        self.us_temperature_model.save_filtered_spectrum = value

    @property
    def ds_fringe_frequency(self):
        return self.ds_temperature_model.fringe_frequency

    @property
    def ds_fringe_nd_um(self):
        return self.ds_temperature_model.fringe_nd_um

    @property
    def us_fringe_frequency(self):
        return self.us_temperature_model.fringe_frequency

    @property
    def us_fringe_nd_um(self):
        return self.us_temperature_model.fringe_nd_um

    def set_rois(self, limits):
        # Mark dirty before the setters — each roi setter emits ds/us_calculations_changed
        # synchronously, and the controller's refresh reads self.dirty from there.
        self.dirty = True
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
    def ds_unfiltered_corrected_spectrum(self):
        return self.ds_temperature_model.unfiltered_corrected_spectrum

    @property
    def us_corrected_spectrum(self):
        return self.us_temperature_model.corrected_spectrum

    @property
    def us_unfiltered_corrected_spectrum(self):
        return self.us_temperature_model.unfiltered_corrected_spectrum

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

        dual = (self.mode == 'dual')

        for frame_ind in range(self.data_img_file.num_frames):
            self.set_img_frame_number_to(frame_ind)

            ds_counts = int(self.ds_temperature_model.total_counts)
            us_counts = int(self.us_temperature_model.total_counts) if dual else 0
            max_counts = int(np.amax(np.asarray([us_counts, ds_counts])))
            ds_sufficient_counts = ds_counts > (0.075 * max_counts)
            us_sufficient_counts = dual and us_counts > (0.075 * max_counts)

            if us_sufficient_counts and self.us_temperature_model.temperature_error <= self.error_limit:
                us_temperature.append(self.us_temperature_model.temperature)
                us_temperature_error.append(self.us_temperature_model.temperature_error)
            else:
                us_temperature.append(0)
                us_temperature_error.append(0)
            if ds_sufficient_counts and self.ds_temperature_model.temperature_error <= self.error_limit:
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
        self.unfiltered_corrected_spectrum = Spectrum([], [])
        #self.within_limit = None
        self.response = Spectrum([],[])

        self.temperature_fit_function = fit_black_body_function_wien
        self.subtract_inistu_data_background = True
        self.subtract_inistu_calibration_background = True

        # New unified background subtraction (see TemperatureModelConfiguration
        # docstring on set_background_mode). background_mode is authoritative for
        # the extraction methods below; the subtract_inistu_* bools remain for
        # legacy external readers only.
        self.background_mode = 'insitu'
        self.dark_frame_img = None       # np.ndarray or None
        self.dark_frame_scale = 1.0

        self.filter_oscillation = False
        self.filter_freq_min = 0.0005  # cm — lower bound for fringe peak search
        self.filter_freq_max = 0.05    # cm — upper bound for fringe peak search
        self.save_filtered_spectrum = False
        self.fringe_frequency = None   # f_osc in cm
        self.fringe_nd_um = None       # optical half-path n·d in μm

        self._data_img = None
        self._data_img_x_calibration = None
        self._data_img_dimension = None

        # True after user explicitly clears the intensity calibration. Makes the
        # correction pipeline apply an identity transfer function (response = 1),
        # so corrected_spectrum == data_spectrum and the blackbody fit runs on
        # raw counts. Distinct from the fresh-config state (both flag False and
        # no image loaded) where the corrected spectrum stays empty.
        self._identity_calibration = False

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
        """Store raw image data. Computation is handled by TemperaturePipeline."""
        self._data_img = img_data
        self._data_img_x_calibration = x_calibration
        self._data_img_dimension = (img_data.shape[1], img_data.shape[0])

    @property
    def calibration_img(self):
        return self._calibration_img
    


    def set_calibration_data(self, img_data_file, x_calibration):
        # Loading a real calibration image cancels any prior identity/clear state.
        self._identity_calibration = False
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
        # Computation is handled by TemperaturePipeline (run_ds / run_us from CALIB_SPEC).

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
        """Load standard spectrum. Computation is handled by the caller via pipeline."""
        self.calibration_parameter.load_standard_spectrum(filename)

    

    def save_standard_spectrum(self, filename):
        self.calibration_parameter.set_standard_spectrum(self.corrected_spectrum)
        self.calibration_parameter.save_standard_spectrum(filename)
  

    def set_calibration_modus(self, modus):
        """Set calibration modus. Computation is handled by the caller via pipeline."""
        self.calibration_parameter.set_modus(modus)

    def set_calibration_temperature(self, temperature):
        """Set calibration temperature. Computation is handled by the caller via pipeline."""
        self.calibration_parameter.set_temperature(temperature)

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
            # Clamp ROI to image bounds so mask, x-slice, and data_y agree in size.
            # validate_roi swaps out-of-order pairs and clamps min>=0; here we also
            # clamp max to image extents (numpy would otherwise wrap negative starts).
            roi = validate_roi(roi)
            h, w = _data_img_as_array.shape
            if roi.x_max >= w:
                roi.x_max = w - 1
            if roi.y_max >= h:
                roi.y_max = h - 1
            mode = getattr(self, 'background_mode', 'insitu')
            if mode == 'insitu':
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
            if mode == 'insitu':
                data_y_bg = get_roi_sum(_data_img_as_array, roi_bg)
                data_y = data_y - data_y_bg
            elif mode == 'prerecorded' and self.dark_frame_img is not None:
                dark = np.asarray(self.dark_frame_img)
                if dark.shape == _data_img_as_array.shape:
                    data_y = data_y - get_roi_sum(dark, roi) * float(self.dark_frame_scale)
                # dimension-mismatch: silently skip (see mode 'prerecorded' docs)

            self.total_counts = np.sum(data_y)
            self.data_spectrum.data = data_x, data_y
            self.data_spectrum.mask = within_limit

    def _update_calibration_spectrum(self):
        if self.calibration_img is not None:
            roi = self.roi_data_manager.get_roi(self.ind, self._calibration_img_dimension)
            roi = validate_roi(roi)
            h, w = np.asarray(self.calibration_img).shape[-2:]
            if roi.x_max >= w:
                roi.x_max = w - 1
            if roi.y_max >= h:
                roi.y_max = h - 1
            mode = getattr(self, 'background_mode', 'insitu')
            if mode == 'insitu':
                roi_bg = self.roi_data_manager.get_roi(self.ind+2, self._calibration_img_dimension)
                roi_bg.x_max = roi.x_max
                roi_bg.x_min = roi.x_min

            calibration_x = self._calibration_img_x_calibration[int(roi.x_min):int(roi.x_max) + 1]
            calibration_y = get_roi_sum(self._calibration_img, roi)

            if mode == 'insitu':
                calibration_bg = get_roi_sum(self._calibration_img, roi_bg)
                calibration_y = calibration_y - calibration_bg
            elif mode == 'prerecorded' and self.dark_frame_img is not None:
                dark = np.asarray(self.dark_frame_img)
                cal_arr = np.asarray(self._calibration_img)
                if dark.shape == cal_arr.shape:
                    calibration_y = calibration_y - get_roi_sum(dark, roi) * float(self.dark_frame_scale)
            self.calibration_spectrum.data = calibration_x, calibration_y

    def _update_corrected_spectrum(self):
        if len(self.data_spectrum) == 0:
            self.corrected_spectrum = Spectrum([], [])
            return

        if self._identity_calibration:
            # User has cleared the intensity calibration: response = 1 everywhere,
            # so corrected_spectrum equals data_spectrum. The fit then runs on
            # the raw ROI counts.
            data_x, data_y = self.data_spectrum.data
            self.response = Spectrum(data_x, np.ones_like(data_y))
            self.corrected_spectrum = Spectrum(data_x, data_y.copy())
            self.corrected_spectrum.mask = self.data_spectrum.mask
            self.unfiltered_corrected_spectrum = Spectrum(data_x, data_y.copy())
            self.fringe_frequency = None
            self.fringe_nd_um = None
            return

        if len(self.calibration_spectrum._x) == len(self.data_spectrum._x):
            x, _ = self.data_spectrum.data
            lamp_spectrum = self.calibration_parameter.get_lamp_spectrum(x)
            filt_osc = self.filter_oscillation
            self.corrected_spectrum, self.unfiltered_corrected_spectrum, self.response, fringe_info = \
                calculate_real_spectrum(
                    self.data_spectrum,
                    self.calibration_spectrum,
                    lamp_spectrum,
                    filter_oscillation=filt_osc,
                    freq_min=self.filter_freq_min,
                    freq_max=self.filter_freq_max)
            self.corrected_spectrum.mask = self.data_spectrum.mask
            if fringe_info is not None:
                self.fringe_frequency = fringe_info['f_osc']
                self.fringe_nd_um = fringe_info['nd_um']
            else:
                self.fringe_frequency = None
                self.fringe_nd_um = None
        else:
            self.corrected_spectrum = Spectrum([], [])
            self.unfiltered_corrected_spectrum = Spectrum([], [])
            self.fringe_frequency = None
            self.fringe_nd_um = None

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



def calculate_real_spectrum(data_spectrum, calibration_spectrum, standard_spectrum, filter_oscillation=False,
                            freq_min=0.0005, freq_max=0.05):
    response_y = calibration_spectrum._y / standard_spectrum._y
    response_y[np.where(response_y == 0)] = np.nan
    response = Spectrum(data_spectrum._x, response_y)

    corrected_y = data_spectrum._y / response_y
    corrected_y = corrected_y / np.max(corrected_y) * np.max(data_spectrum._y)
    unfiltered_corrected = Spectrum(data_spectrum._x, corrected_y.copy())

    fringe_info = None
    if filter_oscillation:
        corrected_y, fringe_info = filter_oscillatory_component(data_spectrum._x, corrected_y,
                                                                freq_min=freq_min, freq_max=freq_max)
    return Spectrum(data_spectrum._x, corrected_y), unfiltered_corrected, response, fringe_info


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
