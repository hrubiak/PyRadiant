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

from functools import partial

from PyQt6 import QtWidgets, QtCore

from .CustomWidgets import (
    LabelAlignRight,
    HorizontalSpacerItem,
    CheckableButton,
    NumberTextField,
    IntegerTextField,
    VerticalLine,
    SaveIconButton,
)


class ConfigurationWidget(QtWidgets.QWidget):
    configuration_selected = QtCore.pyqtSignal(int)  # configuration index

    def __init__(self, parent=None):
        super(ConfigurationWidget, self).__init__(parent)
        self.btn_size = QtCore.QSize(25, 25)

        self.create_widgets()
        self.create_layout()
        self.style_widgets()
        self.add_tooltips()

    def create_widgets(self):
        self.configuration_lbl = LabelAlignRight("Configuration:")

        self.configuration_btns = []
        self.configurations_btn_widget = QtWidgets.QWidget()
        self.configuration_btn_group = QtWidgets.QButtonGroup()

        self.add_configuration_btn = QtWidgets.QPushButton("+")
        self.remove_configuration_btn = QtWidgets.QPushButton("-")


    def create_layout(self):
        self.main_layout = QtWidgets.QHBoxLayout()
        self.main_layout.addWidget(self.configuration_lbl)
        self.main_layout.addWidget(self.add_configuration_btn)
        self.main_layout.addWidget(self.remove_configuration_btn)
        self.main_layout.addWidget(self.configurations_btn_widget)
        self.main_layout.addSpacerItem(HorizontalSpacerItem())

     
        self.setLayout(self.main_layout)

        self.configurations_btn_layout = QtWidgets.QHBoxLayout(
            self.configurations_btn_widget
        )

    def style_widgets(self):
        self.main_layout.setSpacing(6)
        self.main_layout.setContentsMargins(6, 0, 6, 0)
        self.configurations_btn_layout.setSpacing(3)
        self.configurations_btn_layout.setContentsMargins(0, 0, 0, 0)

        btns = [
            self.add_configuration_btn,
            self.remove_configuration_btn,

        ]

        for btn in btns:
            btn.setFixedSize(self.btn_size)



    def update_configuration_btns(self, configurations, cur_ind):
        btn:QtWidgets.QPushButton

        for btn in self.configuration_btns:
            self.configuration_btn_group.removeButton(btn)
            self.configurations_btn_layout.removeWidget(btn)
            btn.deleteLater()  # somehow needs tobe deleted, otherwise remains in the button group

        self.configuration_btns = []

        for ind, configuration in enumerate(configurations):
            new_button = CheckableButton(str(ind + 1))
            new_button.setFixedSize(25, 25)
            new_button.setToolTip("Switch to configuration {}".format(ind + 1))
            self.configuration_btn_group.addButton(new_button)
            self.configuration_btns.append(new_button)
            self.configurations_btn_layout.addWidget(new_button)
            if ind == cur_ind:
                new_button.setChecked(True)
            new_button.clicked.connect(partial(self.configuration_selected.emit, ind))

    def add_tooltips(self):
        self.add_configuration_btn.setToolTip("Add configuration")
        self.remove_configuration_btn.setToolTip("Remove configuration")
