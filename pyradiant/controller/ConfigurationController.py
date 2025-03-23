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

from ..widget.ConfigurationWidget import ConfigurationWidget
from ..model.TemperatureModel import TemperatureModel
from .DataLogController import DataLogController

class ConfigurationController(object):
    """
    Deals with all the signal handling and model upgrades related to be using multiple configurations.
    """

    def __init__(self, configuration_widget:ConfigurationWidget, temperature_model: TemperatureModel, controllers=()):
        """
        :type configuration_widget: ConfigurationWidget
        :type dioptas_model: DioptasModel
        """
        self.widget = configuration_widget
        self.model = temperature_model
        self.controllers = controllers

        self.update_configuration_widget()

        self.create_signals()

    def create_signals(self):
        self.widget.add_configuration_btn.clicked.connect(self.model.add_configuration)
        self.widget.remove_configuration_btn.clicked.connect(self.model.remove_configuration)

        self.widget.configuration_selected.connect(self.model.select_configuration)

        self.model.configuration_added.connect(self.update_configuration_widget)
        self.model.configuration_removed.connect(self.update_configuration_widget)
        self.model.configuration_selected.connect(self.configuration_selected)

   

    def update_configuration_widget(self):
        self.widget.update_configuration_btns(
            configurations=self.model.configurations,
            cur_ind=self.model.configuration_ind
        )

    def configuration_selected(self):
        datalog_controller: DataLogController
        datalog_controller = self.controllers[1]
        datalog_controller.disconnect_models()
        datalog_controller.connect_models()
