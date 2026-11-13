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
from PyQt6.QtGui import QIcon
from PyQt6 import QtWidgets, QtCore
import os

from .CustomWidgets import (
    LabelAlignRight,
    HorizontalSpacerItem,
    CheckableButton,
    NumberTextField,
    IntegerTextField,
    VerticalLine,
    SaveIconButton,
)
from .. import icons_path

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

        # Add a button to add a new model
        self.add_configuration_btn = QtWidgets.QPushButton("")
        add_model_button_icon = QIcon()
        add_model_button_icon.addFile(os.path.join(icons_path,'shadow_add_24dp_E1E5E9_FILL0_wght400_GRAD0_opsz24.svg'))
        self.add_configuration_btn.setIcon(add_model_button_icon)
       

        # Add a button to remove a model
        self.remove_configuration_btn = QtWidgets.QPushButton("")
        remove_model_button_icon = QIcon()
        remove_model_button_icon.addFile(os.path.join(icons_path,'delete_24dp_E1E5E9_FILL0_wght400_GRAD0_opsz24.svg'))
        self.remove_configuration_btn.setIcon(remove_model_button_icon)

        # Auto-switch: when a data file is opened, the controller may move to
        # the configuration set up for that camera / readout mode. The note
        # label explains why it did NOT switch when the choice was unclear.
        self.auto_switch_cb = QtWidgets.QCheckBox("Auto-switch configuration")
        self.auto_switch_cb.setChecked(True)
        self.auto_switch_note_lbl = QtWidgets.QLabel('')
        self.auto_switch_note_lbl.setWordWrap(True)
        self.auto_switch_note_lbl.setVisible(False)
        # A manual pick or turning the feature off makes any note stale.
        self.configuration_selected.connect(lambda _ind: self.set_auto_switch_note(''))
        self.auto_switch_cb.toggled.connect(lambda _on: self.set_auto_switch_note(''))

    def set_auto_switch_note(self, text):
        self.auto_switch_note_lbl.setText(text or '')
        self.auto_switch_note_lbl.setVisible(bool(text))

    def create_layout(self):
        self.main_layout = QtWidgets.QHBoxLayout()
        self.main_layout.addWidget(self.configuration_lbl)
        self.main_layout.addWidget(self.add_configuration_btn)
        self.main_layout.addWidget(self.remove_configuration_btn)
        self.main_layout.addWidget(self.configurations_btn_widget)
        self.main_layout.addSpacerItem(HorizontalSpacerItem())

        # Second line under the config buttons: auto-switch toggle + note.
        self._auto_switch_layout = QtWidgets.QVBoxLayout()
        self._auto_switch_layout.addWidget(self.auto_switch_cb)
        self._auto_switch_layout.addWidget(self.auto_switch_note_lbl)

        self._outer_layout = QtWidgets.QVBoxLayout()
        self._outer_layout.addLayout(self.main_layout)
        self._outer_layout.addLayout(self._auto_switch_layout)
        self.setLayout(self._outer_layout)

        self.configurations_btn_layout = QtWidgets.QHBoxLayout(
            self.configurations_btn_widget
        )

    def style_widgets(self):
        self.main_layout.setSpacing(6)
        self.main_layout.setContentsMargins(6, 0, 6, 0)
        self.configurations_btn_layout.setSpacing(3)
        self.configurations_btn_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(2)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._auto_switch_layout.setSpacing(2)
        self._auto_switch_layout.setContentsMargins(6, 0, 6, 0)
        self.auto_switch_note_lbl.setStyleSheet("color: #F0A500;")

        btns = [
            self.add_configuration_btn,
            self.remove_configuration_btn,

        ]

        for btn in btns:
            btn.setFixedSize(self.btn_size)



    def update_configuration_btns(self, configurations, cur_ind):
        """Sync the config-selector buttons to the model.

        Updates existing buttons in place (label / tooltip / checked state) so
        routine refreshes (dirty-flag toggle, config switch) don't flicker.
        Only rebuilds when the configuration count changes.
        """
        n = len(configurations)
        if len(self.configuration_btns) != n:
            # Count changed → rebuild from scratch.
            for btn in self.configuration_btns:
                self.configuration_btn_group.removeButton(btn)
                self.configurations_btn_layout.removeWidget(btn)
                btn.deleteLater()
            self.configuration_btns = []
            for ind in range(n):
                new_button = CheckableButton('')
                new_button.setFixedSize(32, 25)
                self.configuration_btn_group.addButton(new_button)
                self.configuration_btns.append(new_button)
                self.configurations_btn_layout.addWidget(new_button)
                new_button.clicked.connect(partial(self.configuration_selected.emit, ind))

        # In-place update: label, tooltip, checked state.
        for ind, configuration in enumerate(configurations):
            btn = self.configuration_btns[ind]
            dirty = bool(getattr(configuration, 'dirty', False))
            label = f"{ind + 1}*" if dirty else str(ind + 1)
            if btn.text() != label:
                btn.setText(label)
            tooltip = "Switch to configuration {}".format(ind + 1)
            if dirty:
                tooltip += " (unsaved changes)"
            if btn.toolTip() != tooltip:
                btn.setToolTip(tooltip)
            should_check = (ind == cur_ind)
            if btn.isChecked() != should_check:
                btn.setChecked(should_check)

    def add_tooltips(self):
        self.add_configuration_btn.setToolTip("Add configuration")
        self.remove_configuration_btn.setToolTip("Remove configuration")
        self.auto_switch_cb.setToolTip(
            "When a data file is opened, switch to the configuration already set up\n"
            "for that camera and readout mode (normal / kinetics).\n"
            "Never switches away from an empty configuration, and never picks\n"
            "between two configurations that both match."
        )
