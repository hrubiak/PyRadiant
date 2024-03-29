# -*- coding: utf8 -*-

# DISCLAIMER
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" 
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE 
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE 
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE 
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL 
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR 
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER 
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, 
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE 
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


import os
import re
import time

import numpy as np
from PyQt5 import QtCore
from colorsys import hsv_to_rgb
import copy


def increment_filename(old_file):
    """
    Increments the file extension if it is numeric.  It preserves the number of
    characters in the extension.
    
    Examples:
            print increment_filename('test_001.ext')
            test_002.ext
            print increment_filename('test')
            test
    """
    dot = old_file.rfind('.')
    underscore = old_file.rfind('_')

    if (underscore == -1): return old_file
    if (underscore+1 == dot): return old_file

    ext = old_file[dot+1:]
    n = old_file[underscore+1:dot]
    file = old_file[0:underscore]
    nc = str(len(n))
    try:
        n = int(n)+1      # Convert to number, add one, catch error
        format = '%' + nc + '.' + nc + 'd'
        n = (format % n)
        new_file = file + '_' + n + '.'+ext
        return new_file
    except:
        return old_file



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
        file_list = [(os.path.getctime(path), path) for path in paths]
        self.filename_list = paths
        print('Time needed  for getting files: {0}s.'.format(time.time() - t1))
        return file_list

    def is_correct_file_type(self, filename):
        for ending in self.acceptable_file_endings:
            if filename.endswith(ending):
                return True
        return False

    def _order_file_list(self):
        t1 = time.time()
        self.ordered_file_list = self.file_list
        self.ordered_file_list.sort(key=lambda x: x[0])

        print('Time needed  for ordering files: {0}s.'.format(time.time() - t1))

    def update_file_list(self):
        self.file_list = self._get_files_list()
        self._order_file_list()

    def _iterate_file_number(self, path, step, pos=None):
        directory, file_str = os.path.split(path)
        pattern = re.compile(r'\d+')

        match_iterator = pattern.finditer(file_str)

        for ind, match in enumerate(reversed(list(match_iterator))):
            number_span = match.span()
            left_ind = number_span[0]
            right_ind = number_span[1]
            number = int(file_str[left_ind:right_ind]) + step
            new_file_str = "{left_str}{number:0{len}}{right_str}".format(
                left_str=file_str[:left_ind],
                number=number,
                len=right_ind - left_ind,
                right_str=file_str[right_ind:]
            )
            if pos is None:
                new_complete_path = os.path.join(directory, new_file_str)
                if os.path.exists(new_complete_path):
                    self.complete_path = new_complete_path
                    return new_complete_path
            elif ind == pos:
                new_complete_path = os.path.join(directory, new_file_str)
                if os.path.exists(new_complete_path):
                    self.complete_path = new_complete_path
                    return new_complete_path
        return None

    def _iterate_folder_number(self, path, step, mec_mode=False):
        directory_str, file_str = os.path.split(path)
        pattern = re.compile(r'\d+')

        match_iterator = pattern.finditer(directory_str)

        for ind, match in enumerate(reversed(list(match_iterator))):
            number_span = match.span()
            left_ind = number_span[0]
            right_ind = number_span[1]
            number = int(directory_str[left_ind:right_ind]) + step
            new_directory_str = "{left_str}{number:0{len}}{right_str}".format(
                left_str=directory_str[:left_ind],
                number=number,
                len=right_ind - left_ind,
                right_str=directory_str[right_ind:]
            )
            print(mec_mode)
            if mec_mode:
                match_file_iterator = pattern.finditer(file_str)
                for ind_file, match_file in enumerate(reversed(list(match_file_iterator))):
                    if ind_file != 2:
                        continue
                    number_span = match_file.span()
                    left_ind = number_span[0]
                    right_ind = number_span[1]
                    number = int(file_str[left_ind:right_ind]) + step
                    new_file_str = "{left_str}{number:0{len}}{right_str}".format(
                        left_str=file_str[:left_ind],
                        number=number,
                        len=right_ind - left_ind,
                        right_str=file_str[right_ind:]
                    )
                new_complete_path = os.path.join(new_directory_str, new_file_str)
                print(new_complete_path)
            else:
                new_complete_path = os.path.join(new_directory_str, file_str)
            if os.path.exists(new_complete_path):
                self.complete_path = new_complete_path
                return new_complete_path

    def get_next_filename(self, step=1, filename=None, mode='number', pos=None):
        if filename is not None:
            self.complete_path = filename

        if self.complete_path is None:
            return None

        if mode == 'time':
            time_stat = os.path.getctime(self.complete_path)
            cur_ind = self.ordered_file_list.index((time_stat, self.complete_path))
            # cur_ind = self.ordered_file_list.index(self.complete_path)
            try:
                self.complete_path = self.ordered_file_list[cur_ind + step][1]
                return self.complete_path
            except IndexError:
                return None
        elif mode == 'number':
            return self._iterate_file_number(self.complete_path, step, pos)

    def get_previous_filename(self, step=1, filename=None, mode='number', pos=None):
        """
        Tries to get the previous filename.

        :param step:
        :param pos:
        :param mode:
            can have two values either number or mode. Number will decrement the last digits of the file name \
            and time will get the next file by creation time.
        :param filename:
            Filename to get previous number from
        :return:
            either new filename as a string if it exists or None
        """
        if filename is not None:
            self.complete_path = filename

        if self.complete_path is None:
            return None

        if mode == 'time':
            time_stat = os.path.getctime(self.complete_path)
            cur_ind = self.ordered_file_list.index((time_stat, self.complete_path))
            # cur_ind = self.ordered_file_list.index(self.complete_path)
            if cur_ind > 0:
                try:
                    self.complete_path = self.ordered_file_list[cur_ind - step][1]
                    return self.complete_path
                except IndexError:
                    return None
        elif mode == 'number':
            return self._iterate_file_number(self.complete_path, -step, pos)

    def get_next_folder(self, filename=None, mec_mode=False):
        if filename is not None:
            self.complete_path = filename

        if self.complete_path is None:
            return None
        return self._iterate_folder_number(self.complete_path, 1, mec_mode)

    def get_previous_folder(self, filename=None, mec_mode=False):
        if filename is not None:
            self.complete_path = filename

        if self.complete_path is None:
            return None
        return self._iterate_folder_number(self.complete_path, -1, mec_mode)

    def update_filename(self, new_filename):
        self.complete_path = os.path.abspath(new_filename)
        new_directory, file_str = os.path.split(self.complete_path)
        try:
            self.acceptable_file_endings.append(file_str.split('.')[-1])
        except AttributeError:
            pass
        if self.directory != new_directory:
            if self.directory is not None and self.directory !='':
                self.directory_watcher.removePath(self.directory)
            self.directory_watcher.addPath(new_directory)
            self.directory = new_directory
            if self.create_timed_file_list:
                self.update_file_list()

        if (self.create_timed_file_list and self.ordered_file_list == []):
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


def rotate_matrix_m90(matrix):
    return np.rot90(matrix, -1)


def rotate_matrix_p90(matrix):
    return np.rot90(matrix)


def get_base_name(filename):
    str = os.path.basename(filename)
    if '.' in str:
        str = str.split('.')[:-1][0]
    return str


def calculate_color(ind):
    s = 1
    v = 0.8
    h = (0.19 * (ind + 2)) % 1
    return np.array(hsv_to_rgb(h, s, v)) * 255

def make_color(hue):
    s = 1
    v = 0.8
    h = hue
    return np.array(hsv_to_rgb(h, s, v)) * 255


def convert_d_to_two_theta(d, wavelength):
    return np.arcsin(wavelength / (2 * d)) / np.pi * 360


def get_partial_index(array, value):
    """
    Calculates the partial index for a value from an array using linear interpolation.
    e.g. with array = [0,1,2,3,4,5] and value = 2.5 it would return 2.5, since it in between the second and third
    element.
    :param array: list or numpy array
    :param value: value for which to get the index
    :return: partial index
    """
    upper_ind = np.where(array > value)
    lower_ind = np.where(array < value)

    try:
        spacing = array[upper_ind[0][0]] - array[lower_ind[-1][-1]]
        new_pos = lower_ind[-1][-1] + (value - array[lower_ind[-1][-1]]) / spacing
    except IndexError:
        return None

    return new_pos


def get_partial_value(array, ind):
    """
    Calculates the value for a non-integer array from an array using linear interpolation.
    e.g. with array = [0,2,4,6,8,10] and value = 2.5 it would return 5, since it is in between the second and third
    element.
    :param array: list or numpy array
    :param ind: float index for which to get value
    """
    step = array[int(np.floor(ind)) + 1] - array[int(np.floor(ind))]
    value = array[int(np.floor(ind))] + (ind - np.floor(ind)) * step
    return value


def reverse_interpolate_two_array(value1, array1, value2, array2, delta1=0.1, delta2=0.1):
    """
    Tries to reverse interpolate two vales from two arrays with the same dimensions, and finds a common index
    for value1 and value2 in their respective arrays. the deltas define the search radius for a close value match
    to the arrays.

    :return: index1, index2
    """
    tth_ind = np.argwhere(np.abs(array1 - value1) < delta1)
    azi_ind = np.argwhere(np.abs(array2 - value2) < delta2)

    tth_ind_ravel = np.ravel_multi_index((tth_ind[:, 0], tth_ind[:, 1]), dims=array1.shape)
    azi_ind_ravel = np.ravel_multi_index((azi_ind[:, 0], azi_ind[:, 1]), dims=array2.shape)

    common_ind_ravel = np.intersect1d(tth_ind_ravel, azi_ind_ravel)
    result_ind = np.unravel_index(common_ind_ravel, dims=array1.shape)

    while len(result_ind[0]) > 1:
        if np.max(np.diff(array1)) > 0:
            delta1 = np.max(np.diff(array1[result_ind]))

        if np.max(np.diff(array2)) > 0:
            delta2 = np.max(np.diff(array2[result_ind]))

        tth_ind = np.argwhere(np.abs(array1[result_ind] - value1) < delta1)
        azi_ind = np.argwhere(np.abs(array2[result_ind] - value2) < delta2)

        print(result_ind)

        common_ind = np.intersect1d(tth_ind, azi_ind)
        result_ind = (result_ind[0][common_ind], result_ind[1][common_ind])

    return result_ind[0], result_ind[1]

def getInterpolatedCounts(value, array):   
    # python version of LabVIEW "threshold 1D array" using numpy 1d array
    reversed_array = array[0] > array[1]
    if reversed_array:
        array = np.flip(array)
    p = next(ii for ii,v in enumerate(array) if (v>=value))
    frac=p-1.0+(float(value) - array[p-1])/(array[p]-array[p-1])
    n_chan = len(array)
    if reversed_array:
        frac = n_chan - frac - 1
    return frac

