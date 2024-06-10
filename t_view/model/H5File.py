# -*- coding: utf8 -*-
# T-View - GUI program for analysis of thermal spectra during
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

"""Implements the SPE_File class for loading princeton instrument binary SPE files into Python
works for version 2 and version 3 files.

Usage:
mydata = SPE_File('data.spe')

most important properties:

num_frames - number of frames collected
exposure_time

img - 2d data if num_frames==1
      list of 2d data if num_frames>1  

x_calibration - wavelength information of x-axis



the data will be automatically loaded and all important parameters and the data 
can be requested from the object.

RH: Updated March 25, 2024
"""

import h5py


class H5File(object):
    def __init__(self, filename, x_calibration, debug=False):
        """Opens the PI SPE file and loads its content

        :param filename: filename of the PI SPE to open
        :param debug: if set to true, will automatically save <filename>.xml files for version 3 spe files in the spe
        directory
        """
        """"""
        self.filename = filename
        self.detector = 'NA'
        self.exposure_time = 0
        self.num_frames = 1
        self.grating = '800nm 150'
        self._xdim = 0
        self._ydim = 0
        self.debug = debug
        self._fid = h5py.File(filename, 'r')
        self.gain = 1

        self._load_h5()
        self.num_frames = 1

        self.x_calibration = x_calibration
        

    def _load_h5(self):
        f =  self._fid
        detector_group = f['detector']
        if 'data1' in detector_group:
            data_img = detector_group['data1'][...]
            
            self.img = data_img

            [self._ydim, self._xdim] = data_img.shape

            
        if 'CameraModel' in f:
            self.detector = f['CameraModel'][0].decode('utf-8')
        if 'AcquireTime' in f:
            exposure_time = f['AcquireTime'][0]
            self.exposure_time = exposure_time
    
    def get_dimension(self):
        """Returns (xdim, ydim)"""
        return (self._xdim, self._ydim)