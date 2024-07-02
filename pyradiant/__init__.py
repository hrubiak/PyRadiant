# -*- encoding: utf8 -*-
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
import sys
from sys import platform
#from optparse import OptionParser
#import PyQt6
from PyQt6 import QtWidgets, QtCore
import qdarktheme 
import platform

from .version import get_version
__version__ = get_version()
__platform__ = platform.system()

resources_path = os.path.join(os.path.dirname(__file__), 'resources')
icons_path = os.path.join(resources_path, 'icons')
style_path = os.path.join(resources_path, 'style')

from .controller.MainController import MainController

def make_dpi_aware():
    __platform__ = platform.system()
    if __platform__ == 'Windows':
        if int(platform.release()) >= 8:
            import ctypes
            from ctypes import wintypes
            # Constants for DPI awareness levels
            PROCESS_PER_MONITOR_DPI_AWARE = 2
            # Set the DPI awareness
            ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)

def run_pyradiant():

    '''make_dpi_aware()
    if hasattr(QtCore.Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

    if hasattr(QtCore.Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    '''
    #QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    qdarktheme.enable_hi_dpi()
    app = QtWidgets.QApplication(sys.argv)
    # Apply the complete dark theme to your Qt App.
    qdarktheme.setup_theme("dark", custom_colors={"primary": "#4DDECD"}) 
    '''if platform != "darwin":
        app.setStyle('plastique')'''
    controller = MainController(app)
    controller.show_window()
    sys.exit(app.exec())
