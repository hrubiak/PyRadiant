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
import csv
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional
import numpy as np

class DataRecord:
    def __init__(self, **kwargs):
        self.File = kwargs.get('# File')
        self.Path = kwargs.get('Path')
        self.T_DS = float(kwargs.get('T_DS', 0.0))
        self.T_US = float(kwargs.get('T_US', 0.0))
        self.T_DS_error = float(kwargs.get('T_DS_error', 0.0))
        self.T_US_error = float(kwargs.get('T_US_error', 0.0))
        self.Detector = kwargs.get('Detector', '')
        self.Exposure_Time_sec = float(kwargs.get('Exposure Time [sec]', 0.0))
        self.Gain = float(kwargs.get('Gain', 0.0))
        self.scaling_DS = float(kwargs.get('scaling_DS', 0.0))
        self.scaling_US = float(kwargs.get('scaling_US', 0.0))
        self.counts_DS = float(kwargs.get('counts_DS', 0.0))
        self.counts_US = float(kwargs.get('counts_US', 0.0))
        self.timestamp = kwargs.get('timestamp', datetime.now())

    def __repr__(self):
        return (f"DataRecord({self.File}, {self.Path}, {self.T_DS}, {self.T_US}, "
                f"{self.T_DS_error}, {self.T_US_error}, {self.Detector}, "
                f"{self.Exposure_Time_sec}, {self.Gain}, {self.scaling_DS}, "
                f"{self.scaling_US}, {self.counts_DS}, {self.counts_US}, {self.timestamp})")

class StaticRecordManager:
    def __init__(self):
        self.directory = ''
        self.records: Dict[str, DataRecord] = {}
        self.spe_file = ''
        

    def get_earliest_timestamp(self, file_path: str) -> datetime:
        try:
            creation_time = os.path.getctime(file_path)
        except Exception:
            creation_time = float('inf')

        try:
            modification_time = os.path.getmtime(file_path)
        except Exception:
            modification_time = float('inf')

        try:
            access_time = os.path.getatime(file_path)
        except Exception:
            access_time = float('inf')

        earliest_time = min(creation_time, modification_time, access_time)
        return datetime.fromtimestamp(earliest_time)

    def initialize_records(self,directory: str):
        self.directory = directory
        spe_files = [(f, self.get_earliest_timestamp(os.path.join(self.directory, f)))
                     for f in os.listdir(self.directory) if f.endswith('.spe')]

        spe_files.sort(key=lambda x: x[1])

        for spe_file, timestamp in spe_files:
            file_path = os.path.join(self.directory, spe_file)
            self.records[spe_file] = DataRecord(File=spe_file, Path=file_path, timestamp=timestamp)

    def update_records_from_log(self, log_file_path: str):
        last_n_lines = deque()

        with open(log_file_path, 'r') as file:
            header = file.readline()

            for line in file:
                last_n_lines.append(line)

        reader = csv.DictReader(last_n_lines, delimiter='\t', fieldnames=header.strip().split('\t'))
        next(reader, None)
        for row in reader:
            spe_file = row['# File']
            if spe_file in self.records:
                self.records[spe_file] = DataRecord(**row, timestamp=self.records[spe_file].timestamp)

    def update_record(self, **kwargs):
        spe_file = kwargs.get('# File')
        self.spe_file = spe_file
        if spe_file and spe_file in self.records:
            existing_record = self.records[spe_file]
            existing_timestamp = existing_record.timestamp

            # Update fields from kwargs
            for key, value in kwargs.items():
                if hasattr(existing_record, key):
                    setattr(existing_record, key, float(value) if isinstance(value, str) and value.replace('.', '', 1).isdigit() else value)

            # Ensure the timestamp remains unchanged
            existing_record.timestamp = existing_timestamp
        else:
            print(f"Record for {spe_file} not found.")

    def get_record_index(self, spe_file: str) -> Optional[int]:
        sorted_records = sorted(self.records.values(), key=lambda record: record.timestamp)
        for index, record in enumerate(sorted_records):
            if record.File == spe_file:
                return index
        return None

    def get_record(self, spe_file: str) -> Optional[DataRecord]:
        return self.records.get(spe_file, None)

    def get_last_n_records(self, n: Optional[int] = None) -> List[DataRecord]:
        sorted_records = sorted(self.records.values(), key=lambda record: record.timestamp)
        if n is not None:
            return sorted_records[-n:]
        return sorted_records
    
    def get_temperatures(self, n=None):
        static_records =  self.get_last_n_records(n)
        data_records = static_records
        T_DS = np.zeros(len(data_records))
        T_US = np.zeros(len(data_records))
        for i, record in enumerate(data_records):
            tds = record.T_DS
            tus = record.T_US
            
            T_DS[i] = tds
            T_US[i] = tus
        return T_DS, T_US

    def __repr__(self):
        return f"StaticRecordManager({self.directory}, {list(self.records.keys())})"
    
# Example usage:
'''manager = StaticRecordManager('/Volumes/Storage_T7/Experiments_data/Ex94b/temperature')
manager.update_records_from_log('/Volumes/Storage_T7/Experiments_data/Ex94b/temperature/T_log.txt')
manager.update_record('T 201.spe', T_DS=500.0, T_US=510.0)
print(manager.get_record('T 201.spe'))
'''