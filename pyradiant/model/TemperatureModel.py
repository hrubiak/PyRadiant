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
from .TemperatureModelConfiguration import TemperatureModelConfiguration
from .helper.signal import Signal

T_LOG_FILE = 'T_log'
LOG_HEADER = '# File\tFrame\tPath\tT_DS\tT_US\tT_DS_error\tT_US_error\tDetector\tExposure Time [sec]\tGain\tscaling_DS\tscaling_US\tcounts_DS\tcounts_US\n'

class TemperatureModel(QtCore.QObject):
    
    def __init__(self):
        super().__init__()
        self.configurations = []
        self.configuration_ind = 0
        self.configurations.append(TemperatureModelConfiguration())

        self.configuration_added = Signal()
        self.configuration_selected = Signal(int)  # new index
        self.configuration_removed = Signal(int)  # removed index

        self.data_changed_signal = Signal()
        self.ds_calculations_changed = Signal()
        self.us_calculations_changed = Signal()

        self.connect_models()

    def add_configuration(self):
        """
        Adds a new configuration to the list of configurations. The new configuration will have the same working
        directories as the currently selected.
        """
        new_conf = TemperatureModelConfiguration()
        _setting_working_dir = self.configurations[self.configuration_ind]._setting_working_dir
        new_conf._setting_working_dir = _setting_working_dir
        self.configurations.append(new_conf)


        self.select_configuration(len(self.configurations) - 1)
        self.configuration_added.emit()

    def select_configuration(self, ind):
        """
        Selects a configuration specified by the ind(ex) as current model. This will reemit all needed signals, so that
        the GUI can update accordingly
        """
        if 0 <= ind < len(self.configurations):
            self.disconnect_models()
            self.configuration_ind = ind
            self.connect_models()
            # Recompute spectra + fit on the newly-selected config against its
            # current state, so a switch never leaves stale results visible.
            # Without this, switching from a kinetics config back to a non-
            # kinetics config required a PyRadiant restart before fits ran.
            cfg = self.current_configuration
            if cfg.data_img_file is not None:
                from .temperature_pipeline import pipeline, Stage
                pipeline.run(cfg, Stage.DATA_SPEC)
            self.configuration_selected.emit(ind)

            self.data_changed_signal.emit()
            self.ds_calculations_changed.emit()
            self.us_calculations_changed.emit()

    '''@property
    def working_directories(self):
        return self.current_configuration.working_directories

    @working_directories.setter
    def working_directories(self, new):
        self.current_configuration.working_directories = new'''

    @property
    def current_configuration(self) -> TemperatureModelConfiguration:
        return self.configurations[self.configuration_ind]
    
    def disconnect_models(self):
        """
        Disconnects signals of the currently selected configuration.
        """
        self.current_configuration.data_changed_signal.disconnect(self.data_changed_signal)
        self.current_configuration.ds_calculations_changed.disconnect(self.ds_calculations_changed)
        self.current_configuration.us_calculations_changed.disconnect(self.us_calculations_changed)

    def connect_models(self):
        """
        Connects signals of the currently selected configuration
        """

        self.current_configuration.data_changed_signal.connect(self.data_changed_signal)
        self.current_configuration.ds_calculations_changed.connect(self.ds_calculations_changed)
        self.current_configuration.us_calculations_changed.connect(self.us_calculations_changed)

    def remove_configuration(self):
        """
        Removes the currently selected configuration.
        """
        if len(self.configurations) == 1:
            return
        ind = self.configuration_ind
        self.disconnect_models()
        del self.configurations[ind]
        if ind == len(self.configurations) or ind == -1:
            self.configuration_ind = len(self.configurations) - 1
        self.connect_models()
        self.configuration_removed.emit(self.configuration_ind)

    def resolve_configuration_for_file(self, detector, is_kinetics):
        """Decide which configuration a newly-opened data file should load into.

        Only hardware facts from the file header are used — camera model and
        normal vs. kinetics readout — never calibration / ROI / fit settings,
        because those are exactly what users vary between configurations.
        A config's hardware is taken from the data file it currently holds.

        Rules (first that applies wins):
          * unknown detector (v2 SPE)           -> stay, silently
          * current config has no data yet      -> stay (just added / mid-restore:
                                                   it is the user's explicit target)
          * then, first with an exact match (camera + readout), then camera-only:
              - current config matches          -> stay
              - exactly one other matches       -> switch to it
              - two or more others match        -> stay + note (never guess)
          * nothing matches                     -> stay + note

        Returns (ind, note): ind is the config index to load into (may be the
        current one); note is None or a short user-facing message.
        """
        cur = self.configuration_ind
        if not detector or detector == 'unspecified':
            return cur, None
        if self.configurations[cur].data_img_file is None:
            return cur, None

        want_det = detector.lower()
        want_kin = bool(is_kinetics)

        def hardware(config):
            f = config.data_img_file
            if f is None:
                return None
            det = str(getattr(f, 'detector', 'unspecified') or 'unspecified').lower()
            if det == 'unspecified':
                return None
            kin = str(getattr(f, 'readout_mode', '') or '').lower() == 'kinetics'
            return det, kin

        def exact(config):
            return hardware(config) == (want_det, want_kin)

        def camera_only(config):
            hw = hardware(config)
            return hw is not None and hw[0] == want_det

        mode_lbl = 'kinetics' if want_kin else 'normal'
        for fits in (exact, camera_only):
            if fits(self.configurations[cur]):
                return cur, None
            others = [i for i, c in enumerate(self.configurations)
                      if i != cur and fits(c)]
            if len(others) == 1:
                return others[0], None
            if len(others) > 1:
                nums = ', '.join(str(i + 1) for i in others)
                return cur, (f"Configurations {nums} all match {detector} ({mode_lbl}); "
                             f"stayed on {cur + 1}. Pick one above.")
        return cur, (f"No configuration holds {detector} data yet; "
                     f"loaded into {cur + 1}.")
