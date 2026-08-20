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

"""Reader for single-frame TIFF spectra (e.g. Photron high-speed camera on Acton spectrometer).

TIFFs carry no wavelength calibration. The caller (TemperatureModelConfiguration)
is responsible for providing an x_calibration array (computed from the
per-configuration wavelength-calibration polynomial the user explicitly loads).
If x_calibration is None, this reader falls back to a pixel-index x-axis.
"""

import numpy as np
import tifffile

from .DataModel import DataModel


class TifFile(DataModel):
    def __init__(self, filename, polynomial_coeffs=None,
                 dispersion_axis='horizontal', debug=False):
        """Load a single-frame TIFF.

        polynomial_coeffs: optional list of wavelength-polynomial coefficients
            (ascending order, evaluated at zero-indexed pixel positions).
            If None or empty, the x-axis defaults to pixel indices.
        """
        DataModel.__init__(self, debug)
        self.filename = filename

        img = tifffile.imread(filename)
        if img.ndim != 2:
            raise ValueError(
                f"TifFile expects a single-frame 2D TIFF; got shape {img.shape}"
            )
        img = img.astype(np.float64)
        if dispersion_axis == 'vertical':
            img = img.T

        self.img = img
        self.raw_ccd = np.copy(img)
        self._ydim, self._xdim = img.shape
        self.num_frames = 1
        self.detector = 'Photron'
        self.grating = ''
        self.exposure_time = 0
        self.gain = 1

        if polynomial_coeffs:
            coeffs = np.asarray(polynomial_coeffs, dtype=float)
            self.x_calibration = np.polynomial.polynomial.polyval(
                np.arange(self._xdim, dtype=float), coeffs
            )
        else:
            self.x_calibration = np.arange(self._xdim, dtype=float)
