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

import sys
import os

from PyQt6 import QtWidgets, QtCore

from .. import __version__
from ..model.TemperatureModel import TemperatureModel

from ..widget.MainWidget import MainWidget
from .TemperatureController import TemperatureController
from .. widget.TemperatureSpectrumWidget import dataHistoryWidget
from .DataLogController import DataLogController

from .. import style_path

class MainController(object):
    def __init__(self,app):
        self.app = app  # app object
        self.style_path = style_path
        self.main_widget = MainWidget()
        self.data_history_widget = dataHistoryWidget()
        

        self.main_widget.setWindowTitle('T-View ' + __version__)

        self.create_signals()
        self.create_data_models()
        self.create_sub_controller()
        self.settings = QtCore.QSettings("T-View", "T-View")
        self.load_settings()
        #self.load_stylesheet()

    def show_window(self):
        self.main_widget.show()
        if sys.platform == "darwin":
            self.main_widget.setWindowState(
                self.main_widget.windowState() & ~QtCore.Qt.WindowState.WindowMinimized | QtCore.Qt.WindowState.WindowActive)
            self.main_widget.activateWindow()
            self.main_widget.raise_()

    def create_data_models(self):
        self.temperature_model = TemperatureModel()
        #self.ruby_model = RubyModel()
        #self.diamond_model = DiamondModel()
        #self.raman_model = RamanModel()

    def create_sub_controller(self):

        self.datalog_controller = DataLogController(self.temperature_model, self.data_history_widget)
        self.temperature_controller = TemperatureController(self.main_widget.temperature_widget, self.temperature_model, self.data_history_widget)
        
        #self.ruby_controller = RubyController(self.ruby_model, self.main_widget.ruby_widget)
        #self.diamond_controller = DiamondController(self.diamond_model, self.main_widget.diamond_widget)
        #self.raman_controller = RamanController(self.raman_model, self.main_widget.raman_widget)

    def load_settings(self):
        try:
            self.temperature_controller.load_settings(self.settings)
        except (AttributeError, TypeError):
            pass
        '''try:
            self.ruby_controller.load_settings(self.settings)
        except (AttributeError, TypeError):
            pass
        try:
            self.diamond_controller.load_settings(self.settings)
        except (AttributeError, TypeError):
            pass
        try:
            self.raman_controller.load_settings(self.settings)
        except (AttributeError, TypeError):
            pass'''

    def save_settings(self):
        self.temperature_controller.save_settings(self.settings)
        '''self.ruby_controller.save_settings(self.settings)
        self.diamond_controller.save_settings(self.settings)
        self.raman_controller.save_settings(self.settings)'''

    def create_signals(self):
        self.main_widget.closeEvent = self.closeEvent

        #self.main_widget.navigation_widget.temperature_btn.clicked.connect(
        #    self.navigation_temperature_btn_clicked)
        '''self.main_widget.navigation_widget.ruby_btn.clicked.connect(
            self.navigation_ruby_btn_clicked)
        self.main_widget.navigation_widget.diamond_btn.clicked.connect(
            self.navigation_diamond_btn_clicked
        )
        self.main_widget.navigation_widget.raman_btn.clicked.connect(
            self.navigation_raman_btn_clicked)'''

    '''def navigation_ruby_btn_clicked(self):
        self.hide_module_widgets()
        self.main_widget.ruby_widget.show()'''

    '''def navigation_diamond_btn_clicked(self):
        self.hide_module_widgets()
        self.main_widget.diamond_widget.show()

    def navigation_raman_btn_clicked(self):
        self.hide_module_widgets()
        self.main_widget.raman_widget.show()'''

    def navigation_temperature_btn_clicked(self):
        self.hide_module_widgets()
        self.main_widget.temperature_widget.show()

    def hide_module_widgets(self):
        self.main_widget.temperature_widget.hide()
        
        '''self.main_widget.ruby_widget.hide()
        self.main_widget.diamond_widget.hide()
        self.main_widget.raman_widget.hide()'''

    def closeEvent(self, event):
        self.save_settings()
        try:
            self.temperature_controller.roi_controller.view.close()
        except:
            pass
        self.main_widget.close()
        self.temperature_controller.close_log()
        event.accept()

    def load_stylesheet(self):
        WStyle = 'plastique'
        file = open(os.path.join(self.style_path, "stylesheet.qss"))
        stylesheet = file.read()
        self.app.setStyleSheet(stylesheet)
        file.close()
        self.app.setStyle(WStyle)