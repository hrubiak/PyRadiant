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
# along with this program.  If not, see <http://www.gnu.org/licenses/>.import unittest

import unittest
import os

from PyQt6 import QtWidgets

from tests.utility import QtTest

from pyradiant.model.DiamondModel import DiamondModel

unittest_path = os.path.dirname(__file__)
unittest_files_path = os.path.join(unittest_path, '..', 'test_files')
test_file = os.path.join(unittest_files_path, 'temper_009.spe')


class DiamondModelTest(QtTest):
    def setUp(self):
        self.model = DiamondModel()

    def test_get_pressure(self):
        self.model.reference_position = 1334.
        self.model.sample_position = 1335.
        self.assertGreater(self.model.get_pressure(), 0)

    def test_change_reference_position(self):
        self.model.sample_position = 1350
        p1 = self.model.get_pressure()
        self.model.reference_position = 1338
        self.assertLess(self.model.get_pressure(), p1)
