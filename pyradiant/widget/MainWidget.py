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
from PyQt6 import QtCore, QtWidgets, QtGui
from functools import partial

from .ConfigurationWidget import ConfigurationWidget
from .TemperatureWidget import TemperatureWidget


from .. import style_path
from .. import icons_path


class MainWidget(QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._centeral_widget = QtWidgets.QWidget()
        self._main_layout = QtWidgets.QVBoxLayout()
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        self.configuration_widget = ConfigurationWidget()


        self.temperature_widget = TemperatureWidget(self )
     
        
        self._main_layout.addWidget(self.temperature_widget)

        self._centeral_widget.setLayout(self._main_layout)
        self.setCentralWidget(self._centeral_widget)
        self.resize(1300,700)


     

'''if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    widget = MainWidget()
    widget.show()
    widget.raise_()
    app.exec()
'''