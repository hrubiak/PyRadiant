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

from PyQt6 import QtWidgets
import qdarktheme 
import platform

from .version import get_version
__version__ = get_version()
__platform__ = platform.system()

resources_path = os.path.join(os.path.dirname(__file__), 'resources')
icons_path = os.path.join(resources_path, 'icons')
style_path = os.path.join(resources_path, 'style')

EPICS_INSTALLED = False
try:
    from epics import PV, caget, camonitor, camonitor_clear, ca, pv
    import socket
    import gc
    EPICS_INSTALLED = True
except:
    pass

from .controller.MainController import MainController

def cleanup_ca():
    print("Shutting down any CA threads and closing sockets...")
    # Finalize PyEpics Channel Access
    ca.finalize_libca()
    # Force garbage collection
    gc.collect()
    # Check if any sockets remain open
    for obj in gc.get_objects():
        if isinstance(obj, socket.socket):
            print(f"Unclosed socket detected: {obj}")
            obj.close()  # Force close lingering sockets

def run_pyradiant():
    qdarktheme.enable_hi_dpi()
    app = QtWidgets.QApplication(sys.argv)

    # Apply the complete dark theme to your Qt App.
    qdarktheme.setup_theme("dark", custom_colors={"primary": "#4DDECD"}) 
  
    controller = MainController(app)
    controller.show_window()
    if EPICS_INSTALLED:
        app.aboutToQuit.connect(cleanup_ca)  # Ensure cleanup happens on exit
    sys.exit(app.exec())
