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

import numpy as np
from scipy.interpolate import interp1d
from scipy.ndimage import gaussian_filter1d


class Spectrum(object):
    def __init__(self, x=None, y=None, name=''):
        if x is None:
            self._x = np.linspace(0.1, 15, 100)
        else:
            self._x = np.asarray(x)
        if y is None:
            self._y = np.log(self._x ** 2) - (self._x * 0.2) ** 2
        else:
            self._y = np.asarray(y)
        self.name = name
        self.offset = 0
        self._scaling = 1
        self.smoothing = 0
        self.bkg_spectrum = None
        self.mask = None

    def load(self, filename, skiprows=0):
        try:
            if filename.endswith('.chi'):
                skiprows = 4
            data = np.loadtxt(filename, skiprows=skiprows)
            self._x = data.T[0]
            self._y = data.T[1]
            self.name = os.path.basename(filename).split('.')[:-1][0]

        except ValueError:
            print('Wrong data format for spectrum file! - ' + filename)
            return -1

    def save(self, filename, header=''):
        data = np.dstack((self._x, self._y))
        np.savetxt(filename, data[0], header=header)

    def set_background(self, spectrum):
        self.bkg_spectrum = spectrum

    def reset_background(self):
        self.bkg_spectrum = None

    def set_smoothing(self, amount):
        self.smoothing = amount


    @property
    def counts(self):
        return np.sum(self.original_data[1])

    @property
    def data(self):
        if self.bkg_spectrum is not None:
            # create background function
            x_bkg, y_bkg = self.bkg_spectrum.data

            if not np.array_equal(x_bkg, self._x):
                # the background will be interpolated
                f_bkg = interp1d(x_bkg, y_bkg, kind='linear')

                # find overlapping x and y values:
                ind = np.where((self._x <= np.max(x_bkg)) & (self._x >= np.min(x_bkg)))
                x = self._x[ind]
                y = self._y[ind]

                if len(x) == 0:
                    # if there is no overlapping between background and spectrum, raise an error
                    raise BkgNotInRangeError(self.name)

                y = y * self._scaling + self.offset - f_bkg(x)
            else:
                # if spectrum and bkg have the same x basis we just delete y-y_bkg
                x, y = self._x, self._y * self._scaling + self.offset - y_bkg
        else:
            x, y = self.original_data

        if self.smoothing > 0:
            y = gaussian_filter1d(y, self.smoothing)

        
        return x, y

    @data.setter
    def data(self, data):
        (x, y) = data
        self._x = x
        self._y = y
        self.scaling = 1
        self.offset = 0

    @property
    def data_masked(self):
        data = self.data
        x = data[0]
        y = data[1]
        if self.mask is not None:
            if x.shape[0] == self.mask.shape[0]:
                x = x[self.mask]
                y = y[self.mask]
        return x, y

    '''@data_masked.setter
    def data_masked(self, data):
        (x, y) = data
        self._x = x
        self._y = y
        self.scaling = 1
        self.offset = 0'''

    @property
    def original_data(self):
        return self._x, self._y * self._scaling + self.offset

    @property
    def scaling(self):
        return self._scaling

    @scaling.setter
    def scaling(self, value):
        if value < 0:
            self._scaling = 0
        else:
            self._scaling = value

    def get_x_limits(self):
        if len(self):
            return np.min(self._x), np.max(self._x)
        return 0, 0

    # TODO: theses need to be removed, only for legacy purpose
    def get_y_limits(self):
        if len(self):
            return np.min(self._y), np.max(self._y)
        return 0, 0

    def get_x_range(self):
        if len(self):
            return np.max(self._x) - np.min(self._x)
        return 0

    def get_y_range(self):
        if len(self):
            return np.max(self._y) - np.min(self._y)
        return 0

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    # Operators:
    def __sub__(self, other):
        orig_x, orig_y = self.data
        other_x, other_y = other.data

        if orig_x.shape != other_x.shape:
            # the background will be interpolated
            other_fcn = interp1d(other_x, other_y, kind='cubic')

            # find overlapping x and y values:
            ind = np.where((orig_x <= np.max(other_x)) & (orig_x >= np.min(other_x)))
            x = orig_x[ind]
            y = orig_y[ind]

            if len(x) == 0:
                # if there is no overlapping between background and spectrum, raise an error
                raise BkgNotInRangeError(self.name)
            return Spectrum(x, y - other_fcn(x))
        else:
            return Spectrum(orig_x, orig_y - other_y)

    def __add__(self, other):
        orig_x, orig_y = self.data
        other_x, other_y = other.data

        if orig_x.shape != other_x.shape:
            # the background will be interpolated
            other_fcn = interp1d(other_x, other_y, kind='linear')

            # find overlapping x and y values:
            ind = np.where((orig_x <= np.max(other_x)) & (orig_x >= np.min(other_x)))
            x = orig_x[ind]
            y = orig_y[ind]

            if len(x) == 0:
                # if there is no overlapping between background and spectrum, raise an error
                raise BkgNotInRangeError(self.name)
            return Spectrum(x, y + other_fcn(x))
        else:
            return Spectrum(orig_x, orig_y + other_y)

    def __rmul__(self, other):
        orig_x, orig_y = self.data
        return Spectrum(orig_x, orig_y * other)

    def __len__(self):
        return len(self._x)


class BkgNotInRangeError(Exception):
    def __init__(self, spectrum_name):
        self.spectrum_name = spectrum_name

    def __str__(self):
        return "The background range does not overlap with the Spectrum range for " + self.spectrum_name
