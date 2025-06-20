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
import re

class FileNameIterator(QtCore.QObject):
    # TODO create an File Index and then just get the next files according to this.
    # Otherwise searching a network is always to slow...

    def __init__(self, filename=None):
        super(FileNameIterator, self).__init__()
        self.acceptable_file_endings = []
        self.directory_watcher = QtCore.QFileSystemWatcher()
        self.directory_watcher.directoryChanged.connect(self.add_new_files_to_list)
        self.create_timed_file_list = False

        if filename is None:
            self.complete_path = None
            self.directory = None
            self.filename = None
            self.file_list = []
            self.ordered_file_list = []
            self.filename_list = []
        else:
            self.complete_path = os.path.abspath(filename)
            self.directory, self.filename = os.path.split(self.complete_path)
            self.acceptable_file_endings.append(self.filename.split('.')[-1])

    def _get_files_list(self):
        t1 = time.time()
        filename_list = os.listdir(self.directory)
        files = []
        for file in filename_list:
            if self.is_correct_file_type(file):
                files.append(file)
        paths = [os.path.join(self.directory, file) for file in files]
        file_list = [(os.path.getmtime(path), path) for path in paths]
        self.filename_list = paths
        print('Time needed  for getting files: {0}s.'.format(time.time() - t1))
        return file_list

    def is_correct_file_type(self, filename):
        is_correct_ending = False
        for ending in self.acceptable_file_endings:
            if filename.endswith(ending):
                is_correct_ending = True
                break
        return is_correct_ending

    def _order_file_list(self):
        t1 = time.time()
        self.ordered_file_list = self.file_list
        self.ordered_file_list.sort(key=lambda x: x[0])

        print('Time needed  for ordering files: {0}s.'.format(time.time() - t1))

    def update_file_list(self):
        self.file_list = self._get_files_list()
        self._order_file_list()


    

    def get_next_filename(self, mode='number'):
        # updated Feb 15, 2025 by chatgpt
        if self.complete_path is None:
            return None

        if mode == 'number':
            # Split the current file path into directory and filename.
            directory, file_str = os.path.split(self.complete_path)
            filename, file_type_str = get_file_and_extension(file_str)
            
            # Extract the ending number from the filename.
            file_number_str = FileNameIterator._get_ending_number(filename)
            try:
                file_number = int(file_number_str)
            except ValueError:
                return None

            # Get the base part of the filename (everything before the number)
            file_base_str = filename[:-len(file_number_str)]

            # Build a regex pattern to match files with the same base and extension.
            # For example, if file_base_str is "T " and extension is "spe",
            # the pattern will match "T 201.spe", "T 203.spe", etc.
            pattern = re.compile(r'^' + re.escape(file_base_str) + r'(\d+)\.' + re.escape(file_type_str) + r'$')
            
            matching_files = []
            # List all files in the directory and check for matches.
            for f in os.listdir(directory):
                m = pattern.match(f)
                if m:
                    num = int(m.group(1))
                    matching_files.append((num, f))
            
            # Sort the files based on the numeric part.
            matching_files.sort(key=lambda x: x[0])
            
            # Look for the first file with a number greater than the current file number.
            for num, f in matching_files:
                if num > file_number:
                    new_complete_path = os.path.join(directory, f)
                    self.complete_path = new_complete_path
                    return new_complete_path
            return None

        elif mode == 'time':
            # (Your original time-based logic remains unchanged.)
            time_stat = os.path.getmtime(self.complete_path)
            if not self.ordered_file_list:
                return None
            cur_ind = self.ordered_file_list.index((time_stat, self.complete_path))
            try:
                self.complete_path = self.ordered_file_list[cur_ind + 1][1]
                return self.complete_path
            except IndexError:
                return None

    def get_previous_filename(self, mode='number'):
        # updated Feb 15, 2025 by chatgpt
        if self.complete_path is None:
            return None

        if mode == 'number':
            directory, file_str = os.path.split(self.complete_path)
            filename, file_type_str = get_file_and_extension(file_str)
            file_number_str = FileNameIterator._get_ending_number(filename)
            try:
                file_number = int(file_number_str)
            except ValueError:
                return None

            file_base_str = filename[:-len(file_number_str)]
            pattern = re.compile(r'^' + re.escape(file_base_str) + r'(\d+)\.' + re.escape(file_type_str) + r'$')
            
            matching_files = []
            for f in os.listdir(directory):
                m = pattern.match(f)
                if m:
                    num = int(m.group(1))
                    matching_files.append((num, f))
            
            matching_files.sort(key=lambda x: x[0])
            
            # To get the previous file, iterate in reverse and pick the first number less than the current one.
            for num, f in reversed(matching_files):
                if num < file_number:
                    new_complete_path = os.path.join(directory, f)
                    self.complete_path = new_complete_path
                    return new_complete_path
            return None

        elif mode == 'time':
            time_stat = os.path.getmtime(self.complete_path)
            if not self.ordered_file_list:
                return None
            cur_ind = self.ordered_file_list.index((time_stat, self.complete_path))
            if cur_ind > 0:
                try:
                    self.complete_path = self.ordered_file_list[cur_ind - 1][1]
                    return self.complete_path
                except IndexError:
                    return None


    '''def get_next_filename(self, mode='number'):

        if self.complete_path is None:
            return None
        if mode == 'time':
            time_stat = os.path.getmtime(self.complete_path)
            if not len(self.ordered_file_list):
                return None
            cur_ind = self.ordered_file_list.index((time_stat, self.complete_path))
            # cur_ind = self.ordered_file_list.index(self.complete_path)
            try:
                self.complete_path = self.ordered_file_list[cur_ind + 1][1]
                return self.complete_path
            except IndexError:
                return None
        elif mode == 'number':
            directory, file_str = os.path.split(self.complete_path)
            filename, file_type_str =  get_file_and_extension(file_str)
            file_number_str = FileNameIterator._get_ending_number(filename)
            try:
                file_number = int(file_number_str)
            except ValueError:
                return None
            file_base_str = filename[:-len(file_number_str)]

            format_str = '0' + str(len(file_number_str)) + 'd'
            number_str = ("{0:" + format_str + '}').format(file_number + 1)
            new_file_name = file_base_str + number_str + '.' + file_type_str
            new_complete_path = os.path.join(directory, new_file_name)
            if os.path.exists(new_complete_path):
                self.complete_path = new_complete_path
                return new_complete_path
            return None

    def get_previous_filename(self, mode='number'):
        """
        Tries to get the previous filename.

        :param mode:
            can have two values either number or mode. Number will decrement the last digits of the file name \
            and time will get the next file by creation time.
        :return:
            either new filename as a string if it exists or None
        """
        if self.complete_path is None:
            return None
        if mode == 'time':
            time_stat = os.path.getmtime(self.complete_path)
            if not len(self.ordered_file_list):
                return None
            cur_ind = self.ordered_file_list.index((time_stat, self.complete_path))
            # cur_ind = self.ordered_file_list.index(self.complete_path)
            if cur_ind > 0:
                try:
                    self.complete_path = self.ordered_file_list[cur_ind - 1][1]
                    return self.complete_path
                except IndexError:
                    return None
        elif mode == 'number':
            directory, file_str = os.path.split(self.complete_path)
            filename, file_type_str =  get_file_and_extension(file_str)
            file_number_str = FileNameIterator._get_ending_number(filename)
            try:
                file_number = int(file_number_str)
            except ValueError:
                return None
            file_base_str = filename[:-len(file_number_str)]
            format_str = '0' + str(len(file_number_str)) + 'd'
            number_str = ("{0:" + format_str + '}').format(file_number - 1)
            new_file_name = file_base_str + number_str + '.' + file_type_str

            new_complete_path = os.path.join(directory, new_file_name)
            if os.path.exists(new_complete_path):
                self.complete_path = new_complete_path
                return new_complete_path

            format_str = '0' + str(len(file_number_str) - 1) + 'd'
            number_str = ("{0:" + format_str + '}').format(file_number - 1)
            new_file_name = file_base_str + number_str + '.' + file_type_str

            new_complete_path = os.path.join(directory, new_file_name)
            if os.path.exists(new_complete_path):
                self.complete_path = new_complete_path
                return new_complete_path
            return None'''

    def update_filename(self, new_filename):
        self.complete_path = os.path.abspath(new_filename)
        new_directory, file_str = os.path.split(self.complete_path)
        try:
            self.acceptable_file_endings.append(file_str.split('.')[-1])
        except AttributeError:
            pass
        if self.directory != new_directory:
            if self.directory is not None:
                self.directory_watcher.removePath(self.directory)
            self.directory_watcher.addPath(new_directory)
            self.directory = new_directory
            if self.create_timed_file_list:
                self.update_file_list()

        if self.create_timed_file_list and self.ordered_file_list == []:
            self.update_file_list()

    def add_new_files_to_list(self):
        """
        checks for new files in folder and adds them to the sorted_file_list
        :return:
        """
        cur_filename_list = os.listdir(self.directory)
        cur_filename_list = [os.path.join(self.directory, filename) for filename in cur_filename_list if
                             self.is_correct_file_type(filename)]
        new_filename_list = [filename for filename in cur_filename_list if filename not in list(self.filename_list)]
        self.filename_list = cur_filename_list
        for filename in new_filename_list:
            creation_time = os.path.getctime(filename)
            if len(self.ordered_file_list) > 0:
                if creation_time > self.ordered_file_list[-1][0]:
                    self.ordered_file_list.append((creation_time, filename))
                else:
                    for ind in range(len(self.ordered_file_list)):
                        if creation_time < self.ordered_file_list[ind][0]:
                            self.ordered_file_list.insert(ind, (creation_time, filename))
                            break
            else:
                self.ordered_file_list.append((creation_time, filename))

    @staticmethod
    def _get_ending_number(basename):
        res = ''
        for char in reversed(basename):
            if char.isdigit():
                res += char
            else:
                return res[::-1]

def get_file_and_extension(file_str):
    if '.' in file_str:
        tokens = file_str.split('.')
        file_type_str = tokens [-1]
        filename = file_str[:-1*(len(file_type_str)+1)]
    
    else:
        
        filename= file_str
        file_type_str = ''
    return filename,file_type_str