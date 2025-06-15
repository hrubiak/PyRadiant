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

import h5py
import math
import datetime
import time

from .helper import FileNameIterator
from .helper.HelperModule import get_partial_index, get_partial_value



from .TemperatureModel import LOG_HEADER, T_LOG_FILE

import csv
from collections import deque
from typing import List



class DataRecord:
    def __init__(self, **kwargs):
    
        self.File = kwargs.get('# File')
        self.Path = kwargs.get('Path')
        self.T_DS = float(kwargs.get('T_DS'))
        self.T_US = float(kwargs.get('T_US'))
        self.T_DS_error = float(kwargs.get('T_DS_error'))
        self.T_US_error = float(kwargs.get('T_US_error'))
        self.Detector = kwargs.get('Detector')
        self.Exposure_Time_sec = float(kwargs.get('Exposure Time [sec]'))
        self.Gain = float(kwargs.get('Gain'))
        self.scaling_DS = float(kwargs.get('scaling_DS'))
        self.scaling_US = float(kwargs.get('scaling_US'))
        self.counts_DS = float(kwargs.get('counts_DS'))
        self.counts_US = float(kwargs.get('counts_US'))

    def __repr__(self):
        return f"DataRecord({self.File}, {self.Path}, {self.T_DS}, {self.T_US}, {self.T_DS_error}, {self.T_US_error}, {self.Detector}, {self.Exposure_Time_sec}, {self.Gain}, {self.scaling_DS}, {self.scaling_US}, {self.counts_DS}, {self.counts_US})"


class DatalogModel(QtCore.QObject):
    selection_changed = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()

        self.filename = None
        self.data_records = []

    def clear_log(self):
        self.data_records= []

    def get_temperatures(self):
        
        data_records = self.data_records
        T_DS = np.zeros(len(data_records))
        T_US = np.zeros(len(data_records))
        for i, record in enumerate(data_records):
            tds = record.T_DS
            tus = record.T_US
            
            T_DS[i] = tds
            T_US[i] = tus
        return T_DS, T_US
       
    
    def add_record(self, record):
        data_record = DataRecord(**record)
        self.data_records.append(data_record)
    
    def load_last_n_records(self, file_path: str, n: int):
        # Using deque to keep only the last n lines in memory
        last_n_lines = deque(maxlen=n)
        
        with open(file_path, 'r') as file:
            # Skip the header line
            header = file.readline()
            
            # Read the file line by line from the end
            for line in reversed(list(file)):
                last_n_lines.appendleft(line)
                if len(last_n_lines) == n:
                    break
        
        # Parse the collected lines into DataRecord objects
        data_records = []
        reader = csv.DictReader(last_n_lines, delimiter='\t', fieldnames=header.strip().split('\t'))
        next(reader, None)  # Skip the header line in deque
        for row in reader:
            if row['Path'] != 'Path':
                headings = list(row.keys())

                all_strings = all(isinstance(item, str) for item in headings)
                if all_strings:
                    record = DataRecord(**row)
                    data_records.append(record)
                else:
                    continue
        
        return data_records