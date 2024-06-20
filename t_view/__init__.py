# -*- coding: utf-8 -*-
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

import os
import sys
from sys import platform
from optparse import OptionParser

from PyQt5 import QtWidgets

from .version import get_version
__version__ = get_version()

resources_path = os.path.join(os.path.dirname(__file__), 'resources')
icons_path = os.path.join(resources_path, 'icons')
style_path = os.path.join(resources_path, 'style')

from .controller.MainController import MainController


def run_t_view():
    usage = 'Usage: run_t_view [options]'
    vers = 'run_t_view %s' % __version__

    parser = OptionParser(usage=usage, prog='pyshortcut', version=vers)

    parser.add_option('-m', '--make_icon', dest='makeicon', action="store_true",
                      default=False, help='make desktop shortcut')
    #(options, args) = parser.parse_args()
    
    app = QtWidgets.QApplication(sys.argv)
    if platform != "darwin":
        app.setStyle('plastique')
    controller = MainController()
    controller.show_window()
    app.exec_()
    
