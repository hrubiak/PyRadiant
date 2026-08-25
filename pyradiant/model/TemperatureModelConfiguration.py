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
from dataclasses import dataclass, field
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


@dataclass
class FrameRecord:
    """Per-frame, per-side snapshot of everything the display code needs.

    Populated by TemperatureModelConfiguration._rebuild_records_cache with the
    RAW output of the extract/correct/fit pipeline — no display filters, no
    zero-sentinels. T / T_err are NaN when the fit could not run or produced
    no result; display gates (counts filter, error_limit, T-range) are applied
    at read time by frame_is_displayable.
    """
    data_spectrum: object = field(default_factory=lambda: Spectrum([], []))
    corrected_spectrum: object = field(default_factory=lambda: Spectrum([], []))
    fit_spectrum: object = field(default_factory=lambda: Spectrum([], []))
    T: float = float('nan')
    T_err: float = float('nan')
    counts: float = 0.0
    roi_max: float = 0.0


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

        # Kinetics readout mode of the loaded data file. Diagnostic/UI-facing
        # in this pass; downstream extraction is unchanged (SpeFile._read_frame
        # already invisibly reconstructs each strip as a full-sensor canvas
        # for interleaved; non-interleaved frames come back at (window_height,
        # sensor_width) with no padding).
        #   'off'                  : normal full-frame or unknown readout.
        #   'kinetics-interleaved' : PI-MAX4 kinetics with DS and US on the same
        #                            sensor, temporally offset by row-shift time.
        #                            Only a subset of strip indices have DS+US
        #                            exposure overlap.
        #   'kinetics'             : single-sided kinetics. Covers two physical
        #                            shapes handled by the same code path:
        #                              (a) window_height == 1 — one row per
        #                                  strip, no in-frame bg possible;
        #                                  user picks 'prerecorded' or
        #                                  'kinetics_trend' bg mode.
        #                              (b) window_height  > 1 — a strip window
        #                                  holds one side's signal ROI plus a
        #                                  bg ROI on different rows; the
        #                                  existing 'insitu' bg subtraction
        #                                  (bg row-band from the same frame)
        #                                  handles it with no special-case
        #                                  code.
        # kinetics_info carries geometry (window_height, n_strips, sensor) now
        # and will be extended with timing fields (shift_time_per_row,
        # readout_edge, strip_timestamps) when time-unscrambling lands.
        self.kinetics_mode = 'off'
        self.kinetics_info = {}
        # User-forced kinetics mode. When set (via set_kinetics_mode by a UI
        # selector), _sync_kinetics_from_file uses this instead of the
        # window_height heuristic. Persists in .trs so a session's user choice
        # survives reload. None = use auto-detect. Ross: add a UI dropdown
        # (Off / Kinetics / Kinetics-interleaved) that flips this override; the
        # extraction/UI is already threaded through cfg.kinetics_mode.
        self.kinetics_mode_override = None

        # Per-side mask-slot offsets (frame-slot units). When set, these take
        # precedence over the value derived from cross_mode_cal_info in
        # _q_side. Populated by import_slots_from_trs or by load_setting
        # when the source .trs has q_ds_slot / q_us_slot attrs. Kept separate
        # from cal-derived values so a subsequent full-chip cal load can win.
        self.q_ds_override = None
        self.q_us_override = None

        # Shared-bg convention flag: when True and kinetics_mode ==
        # 'kinetics-interleaved', editing one side's bg-ROI mirrors to the
        # other. Captured at .trs save time from the current DS_bg == US_bg
        # equality and restored on load. False in non-kinetics or when the
        # two bg-ROIs deliberately differ.
        self.bg_shared_ds_us = False
        # Re-entry guard for the DS/US bg mirror (see _maybe_mirror_bg).
        self._bg_mirror_active = False

        # True when this configuration has unsaved changes (data/calibration/ROI/etc.
        # loaded or modified since the last save_setting or load_setting call).
        # Consumed at app-close to prompt for saving.
        self.dirty = False

        self._filename_iterator = FileNameIterator()

        self.roi_data_manager = RoiDataManager(4)

        self.current_frame = 0
        # Per-side readout indices (used by sync-frame / lab-time modes so
        # DS and US spectra can come from different readout frames of the
        # same physical exposure). None means that side has no valid readout
        # for the current coincident k. In legacy frame mode both equal
        # current_frame.
        self.current_frame_ds = 0
        self.current_frame_us = 0
        self.ds_temperature_model = SingleTemperatureModel(0, self.roi_data_manager)
        self.us_temperature_model = SingleTemperatureModel(1, self.roi_data_manager)
        # Back-ref so the pipeline's 'kinetics_trend' branch can reach the
        # shared bg-matrix cache and geometry.
        self.ds_temperature_model._parent_config = self
        self.us_temperature_model._parent_config = self

        # Per-side (N × W_bg) column-mean bg cache for the 'kinetics_trend'
        # background-subtraction mode. None means "needs (re)compute";
        # invalidated on file load, bg-ROI move, or dim change.
        self._bg_matrix_cache = {'ds': None, 'us': None}

        # Per-frame, per-side cache of raw pipeline output. Length matches
        # data_img_file.num_frames after the first ensure_records_cache().
        # A None slot means "not yet computed for this frame"; a FrameRecord
        # with NaN T means "computed, but fit failed / had no signal".
        self._ds_records = []
        self._us_records = []
        # True when mutation of pipeline-relevant state has invalidated the
        # cache and a rebuild is required before any display reads it.
        self._records_dirty = True
        # Re-entry guard for _rebuild_records_cache. The rebuild loop drives
        # set_img_frame_number_to, which emits data_changed_signal, which
        # calls back into the controller, which reads the cache — the read
        # path must NOT trigger another rebuild while we're mid-rebuild.
        self._rebuilding_records = False

        # Display-time gates. Kept on the configuration so both the spectrum
        # window and the history plot apply the same filter policy.
        self.error_limit = 200
        self.min_allowed_T = 0.0
        self.max_allowed_T = 1.0e6
        self.apply_counts_filter = True  # 7.5%-of-max rule, applied uniformly

        # Backwards-compat scalar caches — populated as shims from the record
        # store. Kept so pre-refactor readers keep working during migration.
        self.us_temperatures = []
        self.us_temperatures_errors = []
        self.ds_temperatures = []
        self.ds_temperatures_errors = []

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

    def saturation_limit(self, array=None):
        """Per-array saturation threshold from source dtype.
        Any integer dtype → iinfo(dtype).max - 1 (headroom of one count
        below true max, matching the historic uint16=65534 convention).
        Float → np.inf (detection disabled — summed/float data has no
        physically-meaningful full-scale limit).
        Mirror of SingleTemperatureModel.saturation_limit for use at the
        top-level cfg (e.g. from the controller for the intensity gauge)."""
        if array is None:
            array = getattr(self, 'data_img', None)
        if array is None:
            return np.inf
        dtype = np.asarray(array).dtype
        if np.issubdtype(dtype, np.integer):
            return int(np.iinfo(dtype).max) - 1
        return np.inf

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

        # New file (or reload) → bg-matrix cache from the old file is stale.
        self._invalidate_bg_matrix()

        self._sync_kinetics_from_file()
        self._sync_cross_mode_rois()

        # Store image data on each model (store-only; pipeline handles computation).
        self._update_temperature_models_data()

    def _sync_kinetics_from_file(self):
        """Update kinetics_mode/kinetics_info from the currently-loaded reader.

        Downstream extraction is agnostic for both kinetics modes: frames
        come back at (window_height, sensor_width). Only the interpretation
        of the strip differs — interleaved packs DS+US bands into one frame;
        non-interleaved contains a single side's strip per frame.

        The SPE XML uses the same 'Kinetics' string for both. We heuristically
        distinguish non-interleaved by window_height==1 (one physical row per
        frame can only hold a single strip). Anything larger is treated as
        interleaved to preserve behavior for existing PI-MAX4 files at HPCAT
        (which use window_height ≈ 64 for DS+US on the same strip). This is a
        heuristic; if users encounter tall non-interleaved windows they can
        override via [future UI].
        """
        reader = self.data_img_file
        mode_str = str(getattr(reader, 'readout_mode', '') or '').lower()
        if reader is not None and mode_str == 'kinetics':
            sensor_h = int(getattr(reader, 'sensor_height', 0) or 0)
            sensor_w = int(getattr(reader, 'sensor_width', 0) or 0)
            win_h = int(getattr(reader, 'kinetics_window_height', 0) or 0)
            # User override wins over the heuristic; else fall back to a
            # window_height==1 signal for non-interleaved (single strip row
            # can only hold one side's signal).
            if self.kinetics_mode_override in ('kinetics', 'kinetics-interleaved'):
                self.kinetics_mode = self.kinetics_mode_override
            elif win_h == 1:
                self.kinetics_mode = 'kinetics'
            else:
                self.kinetics_mode = 'kinetics-interleaved'
            self.kinetics_info = {
                'window_height': win_h,
                'n_strips': int(getattr(reader, 'num_frames', 0) or 0),
                'sensor_height': sensor_h,
                'sensor_width': sensor_w,
                'window_y': int(getattr(reader, 'kinetics_window_y', 0) or 0),
            }
        else:
            self.kinetics_mode = 'off'
            self.kinetics_info = {}

    def set_kinetics_mode(self, mode):
        """User-forced kinetics mode. Overrides the auto-detect heuristic and
        persists in .trs. Pass None to clear the override.

        Valid values: None, 'kinetics', 'kinetics-interleaved'. 'off' cannot
        be forced — a non-kinetics data file always resolves to 'off' regardless
        of the override."""
        if mode not in (None, 'kinetics', 'kinetics-interleaved'):
            raise ValueError(
                f"kinetics_mode override must be None | 'kinetics' | "
                f"'kinetics-interleaved', got {mode!r}")
        if mode == self.kinetics_mode_override:
            return
        self.kinetics_mode_override = mode
        # Re-sync from the current file so kinetics_mode reflects the new
        # override immediately. _sync_cross_mode_rois picks up the change.
        if self.data_img_file is not None:
            self._sync_kinetics_from_file()
            self._sync_cross_mode_rois()
        self.dirty = True
        self.data_changed_emit(self.current_frame)

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
        self.current_frame_ds = current_frame
        self.current_frame_us = current_frame
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
    # 'hybrid' behaves like 'prerecorded' but auto-scales the dark subtraction by
    # mean(bg-ROI on current image) / mean(same bg-ROI on dark image) — combining
    # the stability of a prerecorded master dark with in-situ exposure-tracking.
    # 'kinetics_trend' interpolates bg at the signal-ROI y by canvas-y-blending
    # the current strip's bg-ROI with an adjacent strip's bg-ROI (kinetics only;
    # falls back to 'insitu' for single-frame data or at endpoint strips).
    _VALID_BACKGROUND_MODES = ('insitu', 'prerecorded', 'hybrid', 'kinetics_trend', 'off')

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

    # -------- Kinetics-trend background (canvas-y bg interpolation) -----------
    def _invalidate_bg_matrix(self, side=None):
        """Drop the cached per-column bg matrix so it recomputes on next use.
        side='ds'|'us' clears one; side=None clears both. Cheap; safe to
        over-call. Callers: file load, bg-ROI setter."""
        if side is None:
            self._bg_matrix_cache = {'ds': None, 'us': None}
        else:
            self._bg_matrix_cache[side] = None

    def _compute_bg_matrix(self, side):
        """Build the (N × W_bg) column-mean bg matrix for one side by
        extracting the bg-ROI from every kinetics frame. Returns the ndarray
        or None if not applicable (single-frame data, no image loaded)."""
        if self.data_img_file is None:
            return None
        n = int(getattr(self.data_img_file, 'num_frames', 0) or 0)
        if n <= 1:
            return None
        first = np.asarray(self.data_img_file.img[0])
        h, w = first.shape
        dim = (w, h)
        roi_idx = 2 if side == 'ds' else 3
        roi = validate_roi(self.roi_data_manager.get_roi(roi_idx, dim))
        if roi.x_max >= w:
            roi.x_max = w - 1
        if roi.y_max >= h:
            roi.y_max = h - 1
        w_roi = int(roi.x_max) - int(roi.x_min) + 1
        if w_roi <= 0:
            return None
        B = np.empty((n, w_roi), dtype=float)
        for f in range(n):
            B[f, :] = get_roi_sum(np.asarray(self.data_img_file.img[f]), roi)
        return B

    def get_bg_matrix(self, side):
        """Cached accessor for the (N × W_bg) per-column bg matrix.
        None means the mode isn't applicable to the current data."""
        cached = self._bg_matrix_cache.get(side)
        if cached is not None:
            return cached
        B = self._compute_bg_matrix(side)
        self._bg_matrix_cache[side] = B
        return B

    def compute_bg_stack_image(self, side):
        """Diagnostic: vstack of the raw bg-ROI slice from every frame.
        Row-band f (rows [f·H_bg, (f+1)·H_bg)) is frame f's bg-ROI region.
        Height ≈ N · bg_roi_height, width = bg_roi_width. Returns None on
        non-kinetics data or when no data image is loaded. Not cached —
        called only on data / ROI change (cheap for typical N≤64)."""
        if self.data_img_file is None:
            return None
        n = int(getattr(self.data_img_file, 'num_frames', 0) or 0)
        if n <= 1:
            return None
        first = np.asarray(self.data_img_file.img[0])
        h_img, w_img = first.shape
        dim = (w_img, h_img)
        roi_idx = 2 if side == 'ds' else 3
        roi = validate_roi(self.roi_data_manager.get_roi(roi_idx, dim))
        if roi.x_max >= w_img:
            roi.x_max = w_img - 1
        if roi.y_max >= h_img:
            roi.y_max = h_img - 1
        w_roi = int(roi.x_max) - int(roi.x_min) + 1
        h_roi = int(roi.y_max) - int(roi.y_min) + 1
        if w_roi <= 0 or h_roi <= 0:
            return None
        slabs = [get_roi_img(np.asarray(self.data_img_file.img[f]), roi)
                 for f in range(n)]
        return np.vstack(slabs)

    def _bg_interp_for_signal_roi(self, side, signal_roi, current_frame):
        """Return per-column interpolated bg for the given signal ROI at
        the given readout-frame index, sliced to match the signal ROI's x
        extent. Returns None when the caller should fall back to insitu:
          * non-kinetics data
          * endpoint frame with missing neighbor
          * bg-ROI x-range does not cover the signal-ROI x-range

        Interpolation axis is canvas-y on the vstacked RAW (frame f
        occupies rows [f·H, (f+1)·H) where H = per-frame height). For a
        signal row at canvas-y (f·H + y_sig_center), the two bracketing
        bg samples are frame f itself and one neighbor (f-1 or f+1)
        chosen by the sign of (y_bg_center - y_sig_center). Linear blend
        by canvas-y distance.
        """
        B = self.get_bg_matrix(side)
        if B is None:
            return None
        n = int(B.shape[0])
        f = int(current_frame)
        if f < 0 or f >= n:
            return None
        dim = self._effective_roi_dimension()
        if dim is None:
            return None
        w_img, h_img = int(dim[0]), int(dim[1])
        bg_roi_idx = 2 if side == 'ds' else 3
        bg_roi = validate_roi(self.roi_data_manager.get_roi(bg_roi_idx, dim))
        sig_roi = validate_roi(signal_roi)
        # Clamp to image extents (mirror _update_data_spectrum).
        if bg_roi.y_max >= h_img:
            bg_roi.y_max = h_img - 1
        if sig_roi.y_max >= h_img:
            sig_roi.y_max = h_img - 1
        if bg_roi.x_max >= w_img:
            bg_roi.x_max = w_img - 1
        if sig_roi.x_max >= w_img:
            sig_roi.x_max = w_img - 1
        # x-range of the bg-ROI must cover the signal-ROI's x-range.
        x0_off = int(sig_roi.x_min) - int(bg_roi.x_min)
        x1_off = x0_off + (int(sig_roi.x_max) - int(sig_roi.x_min) + 1)
        if x0_off < 0 or x1_off > B.shape[1]:
            return None
        # Canvas-y centers within a per-frame image (all frames share layout).
        y_bg_c = 0.5 * (int(bg_roi.y_min) + int(bg_roi.y_max))
        y_sig_c = 0.5 * (int(sig_roi.y_min) + int(sig_roi.y_max))
        delta = y_bg_c - y_sig_c   # >0 if bg below signal (higher row index)
        H = float(h_img)           # per-frame stride on the canvas
        own = B[f, x0_off:x1_off]
        if delta == 0.0:
            return own
        if delta > 0:
            # Neighbor bg (frame f-1) sits above signal on canvas.
            if f - 1 < 0:
                return None
            neighbor = B[f - 1, x0_off:x1_off]
            w_own = (H - delta) / H
            w_nb = delta / H
        else:
            # Neighbor bg (frame f+1) sits below signal on canvas.
            if f + 1 >= n:
                return None
            neighbor = B[f + 1, x0_off:x1_off]
            adelta = -delta
            w_own = (H - adelta) / H
            w_nb = adelta / H
        return w_own * own + w_nb * neighbor

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
        self._sync_cross_mode_rois()
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

        self._sync_cross_mode_rois()
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

        # Kinetics readout state. Persisted so a reopened .trs restores the
        # badge and strip-counter labelling even though the raw reader isn't
        # persisted with the file.
        f.attrs['kinetics_mode'] = self.kinetics_mode
        # User-forced override (Ross's "user picks the mode" requirement).
        # Absent when None so legacy readers don't stumble on the attr.
        if self.kinetics_mode_override is not None:
            f.attrs['kinetics_mode_override'] = self.kinetics_mode_override
        if self.kinetics_info:
            kg = f.create_group('kinetics_info')
            for k, v in self.kinetics_info.items():
                kg.attrs[k] = v

        # Persist DS/US mask-slot offsets so a later session can restore
        # sync-frame / lab-time alignment without needing a companion
        # full-chip cal. Deterministic in kinetics mode: fall back to a
        # value computed directly from the current signal ROI + window_y
        # when neither cross-mode cal nor a prior override supplies q.
        # Also hoist sensor geometry as top-level attrs so future readers
        # can consume it without opening the kinetics_info subgroup.
        if self.kinetics_mode == 'kinetics-interleaved':
            def _slot_out(side):
                q = self._q_side(side)
                if q:
                    return int(q)
                override = self.q_ds_override if side == 'ds' else self.q_us_override
                if override is not None:
                    return int(override)
                sc = self._slot_from_current(side)
                if sc is not None:
                    return int(sc)
                return 0
            f.attrs['q_ds_slot'] = _slot_out('ds')
            f.attrs['q_us_slot'] = _slot_out('us')
            ki = self.kinetics_info or {}
            sw = int(ki.get('sensor_width', 0) or 0)
            sh = int(ki.get('sensor_height', 0) or 0)
            if sw:
                f.attrs['sensor_width'] = sw
            if sh:
                f.attrs['sensor_height'] = sh
            # Shared-bg convention: True when DS_bg and US_bg limits match at
            # save time. Consumers restore mirror behavior on load.
            try:
                shared = (self.ds_roi_bg.as_list() == self.us_roi_bg.as_list())
            except Exception:
                shared = False
            f.attrs['bg_shared_ds_us'] = bool(shared)

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
        # Save ROIs keyed to the *cal image* dimension when a cal image is
        # present — that's the dim the loader uses (_roi_dimension_key) to
        # restore them. Otherwise cross-mode kinetics sessions would write
        # small kinetics-dim limits under the full-chip cal-image key, and
        # on reload the loader would apply those tiny limits to the full-chip
        # cal → near-zero cal spectrum → NaN corrected fit.
        ds_cal_dim = self._cal_image_dim('ds')
        if ds_cal_dim is not None:
            ds_roi_out = self.roi_data_manager.get_roi(0, ds_cal_dim)
            ds_bg_out = self.roi_data_manager.get_roi(2, ds_cal_dim)
        else:
            ds_roi_out = self.ds_roi
            ds_bg_out = self.ds_roi_bg
        ds_group['roi'] = ds_roi_out.as_list()
        ds_group['roi_bg'] = ds_bg_out.as_list()
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
        us_cal_dim = self._cal_image_dim('us')
        if us_cal_dim is not None:
            us_roi_out = self.roi_data_manager.get_roi(1, us_cal_dim)
            us_bg_out = self.roi_data_manager.get_roi(3, us_cal_dim)
        else:
            us_roi_out = self.us_roi
            us_bg_out = self.us_roi_bg
        us_group['roi'] = us_roi_out.as_list()
        us_group['roi_bg'] = us_bg_out.as_list()
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

    
    def _recover_stripsaved_rois(self, side, key_dim, roi_saved, bg_saved):
        """Repair ROIs from .trs files saved by the old buggy save_setting
        that wrote kinetics-strip ROI limits under the full-chip cal image
        key (see save_setting fix). If the loaded ROI y-extent looks like a
        kinetics strip (small) while the key dim is full-chip and kinetics
        window params + slot are known from the same .trs, reconstruct the
        physical full-chip y position:

            Y_cal = win_y + q_slot * h + y_strip

        Returns the possibly-repaired (roi, bg) lists. If any input is
        missing (no kinetics_info, no q override), returns the originals.
        """
        h = int((self.kinetics_info or {}).get('window_height', 0) or 0)
        win_y = int((self.kinetics_info or {}).get('window_y', 0) or 0)
        if h <= 0:
            return roi_saved, bg_saved
        q = self.q_ds_override if side == 'ds' else self.q_us_override
        if q is None:
            return roi_saved, bg_saved
        key_h = int(key_dim[1])
        # Recovery only makes sense when the key is a FULL-CHIP cal image.
        # If key_h == h (kinetics-strip key, e.g. kinetics-mode cal), the
        # saved ROIs are already correctly in strip coords and must NOT
        # be shifted — doing so would project them into full-chip Y and
        # then clamp to key_h-1, collapsing the ROI.
        if key_h <= h:
            return roi_saved, bg_saved
        y_min = int(roi_saved[2]); y_max = int(roi_saved[3])
        by_min = int(bg_saved[2]); by_max = int(bg_saved[3])
        # Heuristic: cal image is much taller than the loaded ROI (typical
        # cross-mode symptom is ~14 rows tall under a 1024-row cal).
        if y_max >= h or (y_max - y_min) > (key_h // 4):
            return roi_saved, bg_saved
        # Reconstruct physical y for both signal + bg. The old buggy code
        # saved the same coordinate frame for both, so apply the same
        # shift to both.
        shift = win_y + q * h
        new_y_min = y_min + shift
        new_y_max = y_max + shift
        new_by_min = by_min + shift
        new_by_max = by_max + shift
        # Clamp to cal image extents.
        new_y_max = min(new_y_max, key_h - 1)
        new_by_max = min(new_by_max, key_h - 1)
        roi_out = [int(roi_saved[0]), int(roi_saved[1]), new_y_min, new_y_max]
        bg_out = [int(bg_saved[0]), int(bg_saved[1]), new_by_min, new_by_max]
        return roi_out, bg_out

    def _cal_image_dim(self, side):
        """Return (xdim, ydim) of the side's cal image, or None if absent.

        Used at save time so ROIs are persisted under the same key the
        loader will use (_roi_dimension_key priority 1). Handles both 2D
        full-chip cal and 3D kinetics cal stacks.
        """
        cal_file = (self.ds_calibration_img_file if side == 'ds'
                    else self.us_calibration_img_file)
        if cal_file is None or getattr(cal_file, 'img', None) is None:
            return None
        shape = np.asarray(cal_file.img).shape
        if len(shape) == 2:
            return (shape[1], shape[0])
        if len(shape) == 3:
            return (shape[2], shape[1])
        return None

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
            loaded_bg = str(f.attrs['background_mode'])
            if loaded_bg in self._VALID_BACKGROUND_MODES:
                self.background_mode = loaded_bg
            else:
                # Unknown/deprecated mode name in the .trs — fall back to
                # 'insitu' so bg subtraction still runs instead of silently
                # no-op'ing downstream.
                self.background_mode = 'insitu'
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
        # Kinetics readout state (backward compat: absent → 'off', empty info;
        # legacy 'interleaved' string → 'kinetics-interleaved').
        self.kinetics_mode = str(f.attrs.get('kinetics_mode', 'off'))
        if self.kinetics_mode == 'interleaved':
            self.kinetics_mode = 'kinetics-interleaved'
        # User-forced override (may be absent). Restored BEFORE the reader-sync
        # below so it takes precedence over the heuristic.
        if 'kinetics_mode_override' in f.attrs:
            self.kinetics_mode_override = str(f.attrs['kinetics_mode_override'])
        else:
            self.kinetics_mode_override = None
        if 'kinetics_info' in f:
            self.kinetics_info = {k: (v.item() if hasattr(v, 'item') else v)
                                  for k, v in f['kinetics_info'].attrs.items()}
        else:
            self.kinetics_info = {}
        # Restore any DS/US mask-slot overrides captured on save. Absent attrs
        # → clear so we don't inherit stale values from a prior session.
        self.q_ds_override = int(f.attrs['q_ds_slot']) if 'q_ds_slot' in f.attrs else None
        self.q_us_override = int(f.attrs['q_us_slot']) if 'q_us_slot' in f.attrs else None
        # Shared-bg convention. Default False for legacy .trs — preserves
        # pre-change behavior. Newer saves capture the DS_bg == US_bg
        # equality explicitly so the mirror can be reinstated on load.
        self.bg_shared_ds_us = bool(f.attrs.get('bg_shared_ds_us', False))
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
            _ds_roi_saved = ds_group['roi'][...]
            _ds_bg_saved = ds_group['roi_bg'][...]
            _ds_roi_saved, _ds_bg_saved = self._recover_stripsaved_rois(
                'ds', ds_dim, _ds_roi_saved, _ds_bg_saved)
            self.roi_data_manager.set_roi(0, ds_dim, _ds_roi_saved)
            self.roi_data_manager.set_roi(2, ds_dim, _ds_bg_saved)

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
            _us_roi_saved = us_group['roi'][...]
            _us_bg_saved = us_group['roi_bg'][...]
            _us_roi_saved, _us_bg_saved = self._recover_stripsaved_rois(
                'us', us_dim, _us_roi_saved, _us_bg_saved)
            self.roi_data_manager.set_roi(1, us_dim, _us_roi_saved)
            self.roi_data_manager.set_roi(3, us_dim, _us_bg_saved)

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

        # The .trs's saved kinetics_mode reflects the session it was saved
        # from, but extraction operates on the currently-loaded data file.
        # If a data file is already present, let it be authoritative — this
        # keeps things consistent when the user switches .trs files (e.g.
        # from a kinetics-cal .trs to a full-chip-cal .trs) without touching
        # the loaded kinetics data.
        if self.data_img_file is not None:
            self._sync_kinetics_from_file()

        self._sync_cross_mode_rois()

        # bg-ROI keys may have been restored via direct set_roi calls above
        # (bypassing the property setters that normally invalidate). Ensure
        # the kinetics-trend bg-matrix cache is rebuilt on next use.
        self._invalidate_bg_matrix()

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
            dim = self._effective_roi_dimension()
            wl = self.x_calibration
            if dim is None or wl is None or len(wl) == 0:
                return [0, 0]
            ds_roi = self.roi_data_manager.get_roi(0, dim)
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

        dim = self._effective_roi_dimension()
        if dim is None:
            return
        ds_roi = self.roi_data_manager.get_roi(0, dim)
        us_roi = self.roi_data_manager.get_roi(1, dim)
        ds_bg_roi = self.roi_data_manager.get_roi(2, dim)
        us_bg_roi = self.roi_data_manager.get_roi(3, dim)

        ds_limits[2] = ds_roi.y_min
        ds_limits[3] = ds_roi.y_max
        us_limits[2] = us_roi.y_min
        us_limits[3] = us_roi.y_max
        ds_bg_limits[2] = ds_bg_roi.y_min
        ds_bg_limits[3] = ds_bg_roi.y_max
        us_bg_limits[2] = us_bg_roi.y_min
        us_bg_limits[3] = us_bg_roi.y_max

        self.roi_data_manager.set_roi(0, dim, ds_limits)
        self.roi_data_manager.set_roi(1, dim, us_limits)
        self.roi_data_manager.set_roi(2, dim, ds_bg_limits)
        self.roi_data_manager.set_roi(3, dim, us_bg_limits)

        '''self.ds_temperature_model._update_all_spectra()
        self.ds_temperature_model.fit_data()
        self.ds_calculations_changed_emit()'''

    def cross_mode_cal_info(self, side):
        """If `side` ('ds' or 'us') is in cross-mode (full-chip 2D cal +
        kinetics data of different dim), return dict with cal image shape,
        cal-dim signal ROI limits, and kinetics window params. Else None.

        Applies to both 'kinetics-interleaved' and 'kinetics' (non-interleaved)
        — in both cases the kinetics data lives on a smaller per-frame canvas
        than a full-chip cal image, and the cal-dim ROI needs its own storage."""
        if self.kinetics_mode not in ('kinetics-interleaved', 'kinetics'):
            return None
        if self.data_img_file is None:
            return None
        cal_file = (self.ds_calibration_img_file if side == 'ds'
                    else self.us_calibration_img_file)
        if cal_file is None or getattr(cal_file, 'img', None) is None:
            return None
        cal_shape = np.asarray(cal_file.img).shape
        if len(cal_shape) != 2:
            return None
        cal_dim = (cal_shape[1], cal_shape[0])
        try:
            data_dim = self.data_img_file.get_dimension()
        except Exception:
            return None
        if cal_dim == data_dim:
            return None
        idx = 0 if side == 'ds' else 1
        bg_idx = 2 if side == 'ds' else 3
        cal_roi = self.roi_data_manager.get_roi(idx, cal_dim)
        cal_bg = self.roi_data_manager.get_roi(bg_idx, cal_dim)
        return {
            'cal_shape': cal_shape,
            'cal_dim': cal_dim,
            'signal_idx': idx,
            'signal_roi_limits': [int(cal_roi.x_min), int(cal_roi.x_max),
                                  int(cal_roi.y_min), int(cal_roi.y_max)],
            'bg_idx': bg_idx,
            'bg_roi_limits': [int(cal_bg.x_min), int(cal_bg.x_max),
                              int(cal_bg.y_min), int(cal_bg.y_max)],
            'win_y': int(self.kinetics_info.get('window_y', 0) or 0),
            'window_height': int(self.kinetics_info.get('window_height', 0) or 0),
        }

    def set_cal_dim_signal_roi(self, side, limits):
        """Set the cal-dim signal ROI (idx 0 for DS, 1 for US) from a
        cal-viewer drag. Used only in cross-mode. After updating cal-dim,
        _sync_cross_mode_rois re-derives the kinetics-dim ROI."""
        info = self.cross_mode_cal_info(side)
        if info is None:
            return
        cal_dim = info['cal_dim']
        idx = info['signal_idx']
        x_min, x_max, y_min, y_max = (int(v) for v in limits)
        cal_h = info['cal_shape'][0]
        y_min = max(0, min(cal_h - 1, y_min))
        y_max = max(0, min(cal_h - 1, y_max))
        if y_min > y_max:
            return
        self.roi_data_manager.set_roi(idx, cal_dim,
                                      [x_min, x_max, y_min, y_max])
        self._sync_cross_mode_rois()

    def set_cal_dim_bg_roi(self, side, limits):
        """Set the cal-dim bg ROI (idx 2 for DS, 3 for US) from a cal-viewer
        drag. Only meaningful in cross-mode: the cal-dim bg is what applies
        to the full-chip cal image itself, and stays decoupled from the
        kinetics-data bg (which is per-strip and has its own extractor)."""
        info = self.cross_mode_cal_info(side)
        if info is None:
            return
        cal_dim = info['cal_dim']
        bg_idx = info['bg_idx']
        x_min, x_max, y_min, y_max = (int(v) for v in limits)
        cal_h = info['cal_shape'][0]
        y_min = max(0, min(cal_h - 1, y_min))
        y_max = max(0, min(cal_h - 1, y_max))
        if y_min > y_max:
            return
        self.roi_data_manager.set_roi(bg_idx, cal_dim,
                                      [x_min, x_max, y_min, y_max])

    def _sync_cross_mode_rois(self):
        """Derive kinetics-dim ROIs from full-chip cal-dim ROIs using the
        modular geometry of PI-MAX4 kinetics readout: charge shifts up by
        h = window_height rows per frame, so a DS/US band at physical cal
        row Y appears at row ((Y - win_y) mod h) of every kinetics frame.
        Idempotent. Applies to both interleaved and non-interleaved kinetics
        (for non-interleaved with h=1 the modular result collapses to 0,
        which is the sole strip row — still correct)."""
        if self.kinetics_mode not in ('kinetics-interleaved', 'kinetics'):
            return
        if self.data_img_file is None:
            return
        win_y = int(self.kinetics_info.get('window_y', 0) or 0)
        h = int(self.kinetics_info.get('window_height', 0) or 0)
        if h <= 0:
            return
        try:
            data_dim = self.data_img_file.get_dimension()
        except Exception:
            return
        for side, cal_file in (('ds', self.ds_calibration_img_file),
                               ('us', self.us_calibration_img_file)):
            if cal_file is None or getattr(cal_file, 'img', None) is None:
                continue
            cal_shape = np.asarray(cal_file.img).shape
            if len(cal_shape) != 2:
                continue  # 3D kinetics cal stack already lives at data-dim
            cal_dim = (cal_shape[1], cal_shape[0])
            if cal_dim == data_dim:
                continue
            # Only signal ROIs (idx 0=DS, 1=US) get the modular mapping —
            # their positions are fixed by a permanent physical mask on the
            # CCD, so the geometry carries over. Background ROIs (idx 2, 3)
            # are chosen for local darkness; a "dark" full-chip row is not
            # necessarily dark in the shifted/interleaved kinetics stack.
            # Backgrounds stay per-dim independent.
            idx = 0 if side == 'ds' else 1
            cal_roi = self.roi_data_manager.get_roi(idx, cal_dim)
            y_min_shifted = int(cal_roi.y_min) - win_y
            y_max_shifted = int(cal_roi.y_max) - win_y
            new_y_min = y_min_shifted % h
            new_y_max = y_max_shifted % h
            if new_y_min > new_y_max:
                # Band straddles a kinetics-frame boundary — split across
                # two frames. Refuse to auto-adapt this side.
                continue
            self.roi_data_manager.set_roi(idx, data_dim,
                [int(cal_roi.x_min), int(cal_roi.x_max),
                 new_y_min, new_y_max])

    def _mirror_roi_to_cal_dim(self, idx, data_dim, limits):
        """When the user drags a signal ROI in the kinetics view, update
        the cal-dim ROI to reflect the same physical sensor row. The user
        changes the row-within-frame; the frame-index quotient
        (Y_cal - win_y) // h is preserved from the pre-drag cal-dim ROI
        so the physical DS/US band position stays consistent.

        Background ROIs (idx 2, 3) are not mirrored — dark regions are
        chosen independently for each readout mode."""
        if self.kinetics_mode not in ('kinetics-interleaved', 'kinetics'):
            return
        if idx not in (0, 1):
            return
        win_y = int(self.kinetics_info.get('window_y', 0) or 0)
        h = int(self.kinetics_info.get('window_height', 0) or 0)
        if h <= 0:
            return
        cal_file = self.ds_calibration_img_file if idx == 0 \
            else self.us_calibration_img_file
        if cal_file is None or getattr(cal_file, 'img', None) is None:
            return
        cal_shape = np.asarray(cal_file.img).shape
        if len(cal_shape) != 2:
            return
        cal_dim = (cal_shape[1], cal_shape[0])
        if cal_dim == data_dim:
            return
        x_min, x_max, ky_min, ky_max = (int(v) for v in limits)
        prev = self.roi_data_manager.get_roi(idx, cal_dim)
        q_min = (int(prev.y_min) - win_y) // h
        q_max = (int(prev.y_max) - win_y) // h
        new_cal_y_min = q_min * h + win_y + ky_min
        new_cal_y_max = q_max * h + win_y + ky_max
        cal_h = cal_shape[0]
        new_cal_y_min = max(0, min(cal_h - 1, new_cal_y_min))
        new_cal_y_max = max(0, min(cal_h - 1, new_cal_y_max))
        if new_cal_y_min > new_cal_y_max:
            return
        self.roi_data_manager.set_roi(idx, cal_dim,
            [x_min, x_max, new_cal_y_min, new_cal_y_max])

    def _effective_roi_dimension(self):
        """Dimension to key ROI lookups on when there's no data image loaded.

        Priority:
          1. data_img_file dim — normal path.
          2. DS calibration image shape (transposed to xdim, ydim) — allows a
             .trs loaded before its data file to still show ROIs on the DS/US
             Cal 2D tabs.
          3. US calibration image shape — same, if DS cal isn't present.
          4. None — no dimension known; callers return a zero ROI.
        """
        if self.data_img_file is not None:
            try:
                return self.data_img_file.get_dimension()
            except Exception:
                pass
        for cal in (self.ds_calibration_img_file, self.us_calibration_img_file):
            if cal is not None and getattr(cal, 'img', None) is not None:
                shape = cal.img.shape
                if len(shape) == 2:
                    return (shape[1], shape[0])
                if len(shape) == 3:
                    return (shape[2], shape[1])
        return None

    # updating roi values
    @property
    def ds_roi(self):
        dim = self._effective_roi_dimension()
        if dim is None:
            return Roi([0, 0, 0, 0])
        try:
            return self.roi_data_manager.get_roi(0, dim)
        except Exception:
            return Roi([0, 0, 0, 0])

    @ds_roi.setter
    def ds_roi(self, ds_limits):
        dim = self._effective_roi_dimension()
        if dim is None:
            return
        self.roi_data_manager.set_roi(0, dim, ds_limits)
        self._mirror_roi_to_cal_dim(0, dim, ds_limits)
        pipeline.run_ds(self, Stage.DATA_SPEC)
        self.ds_calculations_changed_emit()

    @property
    def us_roi(self):
        dim = self._effective_roi_dimension()
        if dim is None:
            return Roi([0, 0, 0, 0])
        try:
            return self.roi_data_manager.get_roi(1, dim)
        except Exception:
            return Roi([0, 0, 0, 0])

    @us_roi.setter
    def us_roi(self, us_limits):
        dim = self._effective_roi_dimension()
        if dim is None:
            return
        self.roi_data_manager.set_roi(1, dim, us_limits)
        self._mirror_roi_to_cal_dim(1, dim, us_limits)
        pipeline.run_us(self, Stage.DATA_SPEC)
        self.us_calculations_changed_emit()

    @property
    def ds_roi_bg(self):
        dim = self._effective_roi_dimension()
        if dim is None:
            return Roi([0, 0, 0, 0])
        try:
            return self.roi_data_manager.get_roi(2, dim)
        except Exception:
            return Roi([0, 0, 0, 0])

    @ds_roi_bg.setter
    def ds_roi_bg(self, ds_bg_limits):
        dim = self._effective_roi_dimension()
        if dim is None:
            return
        self.roi_data_manager.set_roi(2, dim, ds_bg_limits)
        self._mirror_roi_to_cal_dim(2, dim, ds_bg_limits)
        self._invalidate_bg_matrix('ds')
        pipeline.run_ds(self, Stage.DATA_SPEC)
        self.ds_calculations_changed_emit()
        self._maybe_mirror_bg('ds', ds_bg_limits)

    @property
    def us_roi_bg(self):
        dim = self._effective_roi_dimension()
        if dim is None:
            return Roi([0, 0, 0, 0])
        try:
            return self.roi_data_manager.get_roi(3, dim)
        except Exception:
            return Roi([0, 0, 0, 0])

    @us_roi_bg.setter
    def us_roi_bg(self, us_bg_limits):
        dim = self._effective_roi_dimension()
        if dim is None:
            return
        self.roi_data_manager.set_roi(3, dim, us_bg_limits)
        self._mirror_roi_to_cal_dim(3, dim, us_bg_limits)
        self._invalidate_bg_matrix('us')
        pipeline.run_us(self, Stage.DATA_SPEC)
        self.us_calculations_changed_emit()
        self._maybe_mirror_bg('us', us_bg_limits)

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

    def get_frame_time_axis(self):
        """Return an array of times in seconds, one per frame, with t=0 at
        the first kinetics frame. Uses num_frames · exposure_time; no
        physical-mask unscrambling. Returns None if there's no multi-frame
        data or exposure_time is unknown/zero."""
        reader = self.data_img_file
        if reader is None:
            return None
        n = int(getattr(reader, 'num_frames', 0) or 0)
        if n <= 1:
            return None
        t_exp = float(getattr(reader, 'exposure_time', 0) or 0.0)
        if t_exp <= 0.0:
            return None
        return np.arange(n) * t_exp

    def get_side_frame_time_axis(self, side):
        """Per-side lab-time axis for the temperature-history plot.

        In interleaved kinetics, DS and US charge at the same readout frame
        f can come from different physical exposures k because the two mask
        holes sit at different frame slots q_side = (Y_side - win_y) // h
        on the sensor. Empirically (Aug 2026, PI-MAX4 at HPCAT), readout
        runs in forward exposure order and the per-side offset acts with
        opposite sign relative to a naive assumption: the exposure that
        populated `side` in readout frame f is k_side(f) = f - q_side + 1
        (1-indexed). Anchoring t = 0 at exposure #1 gives
        t_side(f) = (k_side(f) - 1) · t_exp = (f - q_side) · t_exp — so DS
        and US points from the same physical exposure land at the same x.

        Returns (times, valid) where times is length-N in seconds and
        valid[f] is True iff k_side(f) is in [1, N]. Frames with invalid
        k have no real exposure (mask charge would come from outside the
        acquisition window) and should be dropped by the caller.

        q_side comes from the cal-dim signal ROI when cross-mode is active
        (a full-chip cal is loaded); otherwise q=0 and both sides share the
        same axis (no per-side shift). Returns None if not multi-frame
        kinetics or exposure_time is unknown.
        """
        reader = self.data_img_file
        if reader is None:
            return None
        n = int(getattr(reader, 'num_frames', 0) or 0)
        if n <= 1:
            return None
        t_exp = float(getattr(reader, 'exposure_time', 0) or 0.0)
        if t_exp <= 0.0:
            return None
        q = self._q_side(side)
        # Global offset = max q across both sides so the earliest displayed
        # frame across DS+US lands at t=0 while preserving per-side sync
        # (both sides get the same shift, so relative offsets don't change).
        q_max = max(self._q_side('ds'), self._q_side('us'))
        f = np.arange(n)
        k = f - q + 1
        valid = (k >= 1) & (k <= n)
        times = (k - 1 + q_max).astype(float) * t_exp
        return times, valid

    def _q_side(self, side):
        """Mask-slot offset for a side (frame-slot units). Prefers the derived
        value from a full-chip cross-mode calibration when available; falls
        back to a manually imported override (set via import_slots_from_trs
        or restored from .trs). Returns 0 when neither source is available."""
        info = self.cross_mode_cal_info(side)
        if info is not None:
            h = int(info.get('window_height', 0) or 0)
            if h > 0:
                y_min = int(info['signal_roi_limits'][2])
                win_y = int(info.get('win_y', 0) or 0)
                return (y_min - win_y) // h
        override = self.q_ds_override if side == 'ds' else self.q_us_override
        if override is not None:
            return int(override)
        return 0

    def _signal_roi_fullchip_y(self, side):
        """(y_min_fullchip, y_max_fullchip) for the given side's signal ROI,
        or None if geometry is unavailable. For full-chip data the current
        ROI y values are already full-chip. For kinetics data (interleaved
        or non-interleaved), add window_y to project the strip-relative y
        onto the physical chip."""
        try:
            roi = self.ds_roi if side == 'ds' else self.us_roi
        except Exception:
            return None
        if roi is None:
            return None
        y_min = int(roi.y_min)
        y_max = int(roi.y_max)
        if self.kinetics_mode in ('kinetics-interleaved', 'kinetics'):
            win_y = int((self.kinetics_info or {}).get('window_y', 0) or 0)
            return win_y + y_min, win_y + y_max
        return y_min, y_max

    def _slot_from_current(self, side):
        """Slot index q for `side` computed from the current signal ROI +
        kinetics_info. Deterministic; independent of q_*_override and
        cross_mode_cal_info. Returns None when data isn't interleaved
        kinetics or window_height is 0. Non-interleaved kinetics has no
        slot concept — every frame is the same strip, just in time."""
        if self.kinetics_mode != 'kinetics-interleaved':
            return None
        h = int((self.kinetics_info or {}).get('window_height', 0) or 0)
        if h <= 0:
            return None
        fc = self._signal_roi_fullchip_y(side)
        if fc is None:
            return None
        win_y = int((self.kinetics_info or {}).get('window_y', 0) or 0)
        return (fc[0] - win_y) // h

    def _maybe_mirror_bg(self, from_side, limits):
        """When bg_shared_ds_us is True and we're in kinetics-interleaved
        mode, mirror the just-set bg-ROI to the other side. Re-entry
        guarded so the reciprocal setter call doesn't recurse. Not applied
        for single-sided 'kinetics' — there's no other side to mirror to."""
        if self._bg_mirror_active:
            return
        if not self.bg_shared_ds_us:
            return
        if self.kinetics_mode != 'kinetics-interleaved':
            return
        self._bg_mirror_active = True
        try:
            if from_side == 'ds':
                self.us_roi_bg = limits
            else:
                self.ds_roi_bg = limits
        finally:
            self._bg_mirror_active = False

    def import_slots_from_trs(self, filename):
        """Read DS/US mask-slot offsets from another .trs and set them as
        overrides on this config. First tries the explicit q_ds_slot /
        q_us_slot attrs written by newer saves; falls back to deriving
        them from the embedded full-chip cal image + cal-dim ROI +
        kinetics_info for older .trs files. Returns (q_ds, q_us, source)
        where source is 'attrs', 'derived', or 'none'."""
        with h5py.File(filename, 'r') as f:
            q_ds = int(f.attrs['q_ds_slot']) if 'q_ds_slot' in f.attrs else None
            q_us = int(f.attrs['q_us_slot']) if 'q_us_slot' in f.attrs else None
            source = 'attrs' if (q_ds is not None or q_us is not None) else 'none'
            if q_ds is None and q_us is None:
                q_ds, q_us = self._derive_slots_from_trs(f)
                if q_ds is not None or q_us is not None:
                    source = 'derived'
        if q_ds is not None:
            self.q_ds_override = q_ds
        if q_us is not None:
            self.q_us_override = q_us
        return q_ds, q_us, source

    def _derive_slots_from_trs(self, f):
        """Compute (q_ds, q_us) from an open .trs h5 handle using the
        embedded full-chip cal image, the cal-dim signal ROI (index 2
        y_min in the stored ROI list), and kinetics_info (prefer the
        file's own; fall back to the current session's if the file
        predates kinetics_info persistence). If a side's cal has the
        same dimensions as the current data image it's a kinetics-cal
        entry — skip that side (returns None for it)."""
        # Geometry: window_y and window_height.
        if 'kinetics_info' in f:
            ki = {k: (v.item() if hasattr(v, 'item') else v)
                  for k, v in f['kinetics_info'].attrs.items()}
        else:
            ki = self.kinetics_info or {}
        win_y = int(ki.get('window_y', 0) or 0)
        h_win = int(ki.get('window_height', 0) or 0)
        if h_win <= 0:
            return None, None
        # Current data dim (used to skip kinetics-cal entries in the .trs).
        try:
            data_dim = self.data_img_file.get_dimension()
        except Exception:
            data_dim = None

        def _q(side):
            grp = 'downstream_calibration' if side == 'ds' else 'upstream_calibration'
            if grp not in f:
                return None
            g = f[grp]
            if 'image' not in g or 'roi' not in g:
                return None
            cal_shape = g['image'].shape
            if len(cal_shape) != 2:
                return None
            cal_dim_local = (cal_shape[1], cal_shape[0])
            if data_dim is not None and cal_dim_local == data_dim:
                # Not cross-mode relative to current data — no q to derive.
                return None
            roi_arr = np.asarray(g['roi'])
            try:
                y_min = int(roi_arr[2])
            except (IndexError, TypeError, ValueError):
                return None
            return (y_min - win_y) // h_win

        return _q('ds'), _q('us')

    def get_coincident_frame_range(self):
        """Range of 1-indexed coincident-exposure frame values k covering
        every readout frame on either side. Aligned with the 'sync_frame'
        x-axis so that k_side = f - q_side + q_max + 1.

        Returns (k_min, k_max) or None if no multi-frame data."""
        if self.data_img_file is None:
            return None
        n = int(getattr(self.data_img_file, 'num_frames', 0) or 0)
        if n <= 1:
            return None
        q_ds = self._q_side('ds')
        q_us = self._q_side('us')
        q_max = max(q_ds, q_us)
        q_min = min(q_ds, q_us)
        # k_side(f) = f - q_side + q_max + 1; union of both sides:
        # min k = 0 - q_max + q_max + 1 = 1
        # max k = (n-1) - q_min + q_max + 1 = n + (q_max - q_min)
        return 1, n + (q_max - q_min)

    def coincident_to_readout(self, k):
        """Map coincident-exposure frame k (1-indexed, sync-frame units) to
        (f_ds, f_us) readout-frame indices (0-indexed). Each side's f is
        None if k falls outside that side's [0, N-1] readout range."""
        if self.data_img_file is None:
            return None, None
        n = int(getattr(self.data_img_file, 'num_frames', 0) or 0)
        if n <= 0:
            return None, None
        q_ds = self._q_side('ds')
        q_us = self._q_side('us')
        q_max = max(q_ds, q_us)
        # k = f - q_side + q_max + 1  →  f = k - 1 + q_side - q_max
        f_ds = int(k) - 1 + q_ds - q_max
        f_us = int(k) - 1 + q_us - q_max
        if not (0 <= f_ds < n):
            f_ds = None
        if not (0 <= f_us < n):
            f_us = None
        return f_ds, f_us

    def set_img_frame_numbers(self, f_ds, f_us):
        """Load per-side readout frames — DS from img[f_ds], US from img[f_us]
        — so both sides display the same physical exposure. Pass None for a
        side that has no valid readout frame at the requested coincident k;
        that side is cleared. Runs the pipeline for both sides."""
        if self.data_img_file is None:
            return False
        n = int(self.data_img_file.num_frames)
        if f_ds is not None and not (0 <= int(f_ds) < n):
            f_ds = None
        if f_us is not None and not (0 <= int(f_us) < n):
            f_us = None
        if f_ds is None and f_us is None:
            return False
        ds_img = self.data_img_file.img[int(f_ds)] if f_ds is not None else None
        us_img = self.data_img_file.img[int(f_us)] if f_us is not None else None
        # _data_img feeds the 2D viewer; prefer DS's frame, fall back to US.
        self._data_img = ds_img if ds_img is not None else us_img
        # Per-side readout indices (None when that side is blank).
        self.current_frame_ds = f_ds if f_ds is not None else None
        self.current_frame_us = f_us if f_us is not None else None
        # current_frame is a scalar used by legacy call sites (labels, logs).
        # Keep it pointing at DS's readout frame when available, else US's.
        self.current_frame = int(f_ds if f_ds is not None else f_us)
        self.ds_temperature_model.set_temperature_fit_function(self.temperature_fit_function_str)
        self.us_temperature_model.set_temperature_fit_function(self.temperature_fit_function_str)
        self.x_calibration = self.data_img_file.x_calibration
        # Feed per-side data. When a side has no valid readout for this
        # coincident k, feed a NaN-filled array of the same shape so the
        # pipeline propagates NaN through the ROI/spectrum/fit and the
        # plots go blank rather than showing bogus data from the other side.
        blank = np.full_like(self._data_img, np.nan, dtype=float)
        self.ds_temperature_model.set_data(
            ds_img if ds_img is not None else blank,
            self.data_img_file.x_calibration)
        if self.mode == 'dual':
            self.us_temperature_model.set_data(
                us_img if us_img is not None else blank,
                self.data_img_file.x_calibration)
        pipeline.run_ds(self, Stage.DATA_SPEC)
        if self.mode == 'dual':
            pipeline.run_us(self, Stage.DATA_SPEC)
        self.data_changed_emit(self.current_frame)
        return True

    # ------------------------------------------------------------------
    # Per-frame record cache — single source of truth for the spectrum
    # window and the history plot.
    # ------------------------------------------------------------------
    def mark_records_dirty(self):
        """Mark the per-frame record cache stale.

        Called from every mutator that changes pipeline output (ROI, dark,
        calibration, filter, fit function, mode, data image). Display code
        that reads records first calls ensure_records_cache(), which no-ops
        while _records_dirty is False and rebuilds otherwise.
        """
        self._records_dirty = True

    def ensure_records_cache(self):
        """Rebuild the per-frame record cache iff it is dirty.

        Idempotent and cheap when clean. Multi-frame datasets get a full
        pipeline pass over all frames per side. Single-frame datasets are
        always re-snapshotted from the live model — cheap, and side-steps
        the need to invalidate on every mutation until the full Step 2
        dirty-flag wiring lands.

        Re-entry guard: if a callback fired during rebuild lands us back
        here, we return immediately. The rebuild loop's own signal traffic
        (from set_img_frame_number_to) must not trigger nested rebuilds.
        """
        if self._rebuilding_records:
            return
        if self.data_img_file is None:
            self._ds_records = []
            self._us_records = []
            self._records_dirty = False
            return
        n = int(self.data_img_file.num_frames)
        if n == 1:
            self._ds_records = [self._record_from_live('ds')]
            self._us_records = [self._record_from_live('us')]
            self._records_dirty = False
            return
        if not self._records_dirty:
            return
        self._rebuild_records_cache()

    def _record_from_live(self, side):
        """Snapshot the live SingleTemperatureModel into a FrameRecord.

        RAW output — no filter, no zeroing. Fit failures land as NaN so the
        display gate (frame_is_displayable) can distinguish them from cold
        real fits.
        """
        m = self.ds_temperature_model if side == 'ds' else self.us_temperature_model
        T = float(m.temperature) if m.temperature is not None else float('nan')
        T_err = float(m.temperature_error) if m.temperature_error is not None else float('nan')
        # Legacy code sets temperature = 0 on fit-failure; convert to NaN so
        # the display-time gate can tell "failed fit" from "cold physical T".
        if T == 0.0 and (T_err == 0.0 or not np.isfinite(T_err)):
            T = float('nan'); T_err = float('nan')
        try:
            counts = float(m.total_counts)
        except (AttributeError, TypeError):
            counts = 0.0
        try:
            roi_max = float(m.data_roi_max)
        except (AttributeError, TypeError):
            roi_max = 0.0
        return FrameRecord(
            data_spectrum=m.data_spectrum,
            corrected_spectrum=m.corrected_spectrum,
            fit_spectrum=m.fit_spectrum,
            T=T, T_err=T_err, counts=counts, roi_max=roi_max,
        )

    def _rebuild_records_cache(self):
        """Full pipeline pass; populate _ds_records / _us_records.

        Preserves the user's current frame so navigation-state is unchanged
        after this call. Sets the re-entry guard so callbacks fired by the
        pipeline runs inside the loop don't recursively re-enter here.
        Dirty flag is cleared FIRST so nested display reads see a fresh
        (albeit still-empty until populated) cache and don't retrigger.
        """
        assert self.data_img_file is not None
        n = int(self.data_img_file.num_frames)
        dual = (self.mode == 'dual')
        cur_frame = self.current_frame
        cur_frame_ds = self.current_frame_ds
        cur_frame_us = self.current_frame_us

        ds_records = [FrameRecord() for _ in range(n)]
        us_records = [FrameRecord() for _ in range(n)]

        # Clear dirty and set the re-entry guard BEFORE the loop so nested
        # ensure_records_cache calls (via signal callbacks) no-op cleanly.
        self._records_dirty = False
        self._rebuilding_records = True
        self.blockSignals(True)
        try:
            for f in range(n):
                # Navigation runs the pipeline via set_img_frame_number_to.
                # We force a rerun by clearing current_frame first, since the
                # setter short-circuits when frame is unchanged.
                self.current_frame = -1
                self.set_img_frame_number_to(f)
                ds_records[f] = self._record_from_live('ds')
                if dual:
                    us_records[f] = self._record_from_live('us')
                # Publish partial results so nested reads during the rebuild
                # see progressively-populated data rather than stale empties.
                self._ds_records = ds_records
                self._us_records = us_records
        finally:
            # Restore navigation to where the user was; force pipeline rerun
            # so live-model state is coherent with the current frame.
            self.current_frame = -1
            if cur_frame_ds is not None and cur_frame_us is not None \
                    and cur_frame_ds != cur_frame_us:
                self.set_img_frame_numbers(cur_frame_ds, cur_frame_us)
            else:
                self.set_img_frame_number_to(cur_frame)
            self.blockSignals(False)
            self._rebuilding_records = False

        self._ds_records = ds_records
        self._us_records = us_records
        self._records_dirty = False

        # Populate backwards-compat lists so pre-refactor readers keep
        # working. These are derived; do not read them for new code.
        ds_T, ds_Terr = self.frame_display_series('ds')
        us_T, us_Terr = self.frame_display_series('us')
        self.ds_temperatures = np.where(np.isfinite(ds_T), ds_T, 0.0).tolist()
        self.ds_temperatures_errors = np.where(np.isfinite(ds_Terr), ds_Terr, 0.0).tolist()
        self.us_temperatures = np.where(np.isfinite(us_T), us_T, 0.0).tolist()
        self.us_temperatures_errors = np.where(np.isfinite(us_Terr), us_Terr, 0.0).tolist()

    def frame_record(self, side, f):
        """Return the FrameRecord for (side, frame f).

        Triggers a lazy rebuild if the cache is dirty. Returns an empty
        FrameRecord (NaN T, empty spectra) when f is out of range — callers
        must handle 'no data at this frame' explicitly.
        """
        self.ensure_records_cache()
        records = self._ds_records if side == 'ds' else self._us_records
        if f is None or f < 0 or f >= len(records):
            return FrameRecord()
        return records[f]

    def displayed_frame(self, side):
        """Return the frame index currently displayed for side ('ds'|'us').

        None if the side has no readout at the current coincident k. Every
        display path should route through this rather than reading the
        ambiguous self.current_frame — in synced modes with q_ds != q_us,
        the two sides can be showing different physical frames.
        """
        return self.current_frame_ds if side == 'ds' else self.current_frame_us

    def frame_is_displayable(self, side, f):
        """Should the fitted T for (side, f) be shown to the user?

        Applies the unified display gate (T finite, in-range, error within
        error_limit, and — if apply_counts_filter — the 7.5%-of-max counts
        rule). Spectrum window and history plot MUST call this so they
        agree on which frames light up.
        """
        rec = self.frame_record(side, f)
        if not np.isfinite(rec.T) or not np.isfinite(rec.T_err):
            return False
        if rec.T <= self.min_allowed_T or rec.T >= self.max_allowed_T:
            return False
        if rec.T_err > self.error_limit:
            return False
        if self.apply_counts_filter and self.mode == 'dual':
            other = self.frame_record('us' if side == 'ds' else 'ds', f)
            max_c = max(rec.counts, other.counts)
            if max_c > 0 and rec.counts <= 0.075 * max_c:
                return False
        return True

    def frame_display_T(self, side, f):
        """(T, T_err) for the display path — or (NaN, NaN) if gated off.

        Read this from the spectrum widget T-text updater AND the history
        plot; identical inputs guarantee identical outputs, which
        guarantees the two views cannot disagree.
        """
        if not self.frame_is_displayable(side, f):
            return float('nan'), float('nan')
        rec = self.frame_record(side, f)
        return rec.T, rec.T_err

    def frame_display_series(self, side):
        """Full (T[N], T_err[N]) arrays for the history plot.

        NaN at frames the display gate excludes; the plot uses
        connect='finite' so the line breaks at NaN.
        """
        self.ensure_records_cache()
        n = int(self.data_img_file.num_frames) if self.data_img_file else 0
        T = np.full(n, np.nan)
        T_err = np.full(n, np.nan)
        for f in range(n):
            t, te = self.frame_display_T(side, f)
            T[f] = t
            T_err[f] = te
        return T, T_err

    def fit_all_frames(self):
        """Legacy entry point — rebuild the cache and return the four
        display-gated lists that pre-refactor callers expect.

        New code should use frame_display_series / frame_record instead.
        """
        if self.data_img_file is None or self.data_img_file.num_frames == 1:
            return [], [], [], []
        self._records_dirty = True
        self.ensure_records_cache()
        return (list(self.us_temperatures),
                list(self.us_temperatures_errors),
                list(self.ds_temperatures),
                list(self.ds_temperatures_errors))


class SingleTemperatureModel(QtCore.QObject):
    #data_changed_stm = QtCore.pyqtSignal()

    def __init__(self, ind, roi_data_manager):
        super(SingleTemperatureModel, self).__init__()
        self.ind = ind
        # Set by the owning TemperatureModelConfiguration right after
        # construction. Used by the 'kinetics_trend' extraction branch to
        # reach the shared bg-matrix cache and the per-side current-frame
        # index.
        self._parent_config = None

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

    def saturation_limit(self, array=None):
        """Per-array saturation threshold derived from the source dtype.

        Any integer dtype → iinfo(dtype).max - 1 (headroom of one count
            below true max — preserves the historic uint16=65534
            convention where a single noisy pixel at max-1 isn't flagged).
        Float dtype → np.inf (saturation detection disabled — summed
            or float data has no physically-meaningful full-scale limit
            at this layer)."""
        if array is None:
            array = getattr(self, 'data_img', None)
        if array is None:
            return np.inf
        dtype = np.asarray(array).dtype
        if np.issubdtype(dtype, np.integer):
            return int(np.iinfo(dtype).max) - 1
        return np.inf

    def columns_within_limit(self, array, limit=None):
        if limit is None:
            limit = self.saturation_limit(array)
        above_limit = np.any(array > limit, axis=0)
        return ~above_limit

    def count_columns_above_limit(self, array, limit=None):
        if limit is None:
            limit = self.saturation_limit(array)
        above_limit = np.any(array > limit, axis=0)
        below_limit = ~above_limit
        above_limit_count = np.sum(above_limit)
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
            _pre_clamp = (int(roi.x_min), int(roi.x_max), int(roi.y_min), int(roi.y_max))
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
            sat_limit = self.saturation_limit(roi_img)
            if np.isfinite(sat_limit) and np.any(roi_img > sat_limit):
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
            elif mode == 'hybrid' and self.dark_frame_img is not None:
                dark = np.asarray(self.dark_frame_img)
                if dark.shape == _data_img_as_array.shape:
                    roi_bg = self.roi_data_manager.get_roi(self.ind+2, self._data_img_dimension)
                    roi_bg.x_max = roi.x_max
                    roi_bg.x_min = roi.x_min
                    data_bg_mean = float(np.mean(get_roi_img(_data_img_as_array, roi_bg)))
                    dark_bg_mean = float(np.mean(get_roi_img(dark, roi_bg)))
                    scale = data_bg_mean / dark_bg_mean if dark_bg_mean != 0 else 0.0
                    data_y = data_y - get_roi_sum(dark, roi) * scale
            elif mode == 'kinetics_trend':
                # Canvas-y bg interpolation between the current strip and one
                # neighbor. Falls back to in-situ subtraction whenever the mode
                # isn't applicable (single-frame data, endpoint strip, or
                # bg-ROI x-range doesn't cover the signal-ROI x-range).
                side = 'ds' if self.ind == 0 else 'us'
                bg_interp = None
                parent = self._parent_config
                if parent is not None:
                    f = parent.current_frame_ds if side == 'ds' else parent.current_frame_us
                    if f is None:
                        f = parent.current_frame
                    bg_interp = parent._bg_interp_for_signal_roi(side, roi, int(f))
                if bg_interp is not None:
                    data_y = data_y - bg_interp
                else:
                    roi_bg = self.roi_data_manager.get_roi(self.ind+2, self._data_img_dimension)
                    roi_bg.x_max = roi.x_max
                    roi_bg.x_min = roi.x_min
                    data_y = data_y - get_roi_sum(_data_img_as_array, roi_bg)

            self.total_counts = np.sum(data_y)
            self.data_spectrum.data = data_x, data_y
            self.data_spectrum.mask = within_limit

    def _update_calibration_spectrum(self):
        if self.calibration_img is not None:
            roi = self.roi_data_manager.get_roi(self.ind, self._calibration_img_dimension)
            roi = validate_roi(roi)
            h, w = np.asarray(self.calibration_img).shape[-2:]
            _pre_clamp = (int(roi.x_min), int(roi.x_max), int(roi.y_min), int(roi.y_max))
            if roi.x_max >= w:
                roi.x_max = w - 1
            if roi.y_max >= h:
                roi.y_max = h - 1
            mode = getattr(self, 'background_mode', 'insitu')
            # Calibration is single-frame; 'kinetics_trend' has no meaning
            # here — treat it as 'insitu'.
            if mode == 'kinetics_trend':
                mode = 'insitu'
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
            elif mode == 'hybrid' and self.dark_frame_img is not None:
                dark = np.asarray(self.dark_frame_img)
                cal_arr = np.asarray(self._calibration_img)
                if dark.shape == cal_arr.shape:
                    roi_bg = self.roi_data_manager.get_roi(self.ind+2, self._calibration_img_dimension)
                    roi_bg.x_max = roi.x_max
                    roi_bg.x_min = roi.x_min
                    cal_bg_mean = float(np.mean(get_roi_img(cal_arr, roi_bg)))
                    dark_bg_mean = float(np.mean(get_roi_img(dark, roi_bg)))
                    scale = cal_bg_mean / dark_bg_mean if dark_bg_mean != 0 else 0.0
                    calibration_y = calibration_y - get_roi_sum(dark, roi) * scale
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
