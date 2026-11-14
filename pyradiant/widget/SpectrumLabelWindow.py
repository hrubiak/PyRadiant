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

from PyQt6 import QtWidgets, QtCore, QtGui


class SpectrumLabelWindow(QtWidgets.QWidget):
    """Small floating window for manually labelling the current spectrum.

    Opened from the Temperature Log window and kept above it (Tool window),
    so the user can step through records there and label them here. One
    checkable button per label definition; several can be on at once.

    Holds no state beyond the button check marks — the controller pushes the
    current (file, frame) and its labels in via set_target()/set_values()
    and listens to label_toggled(key, checked). step_requested(delta) asks
    the log to move to the previous/next record.
    """

    label_toggled = QtCore.pyqtSignal(str, bool)
    step_requested = QtCore.pyqtSignal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent, QtCore.Qt.WindowType.Tool)
        self.setWindowTitle('Label spectrum')
        self._buttons: dict[str, QtWidgets.QPushButton] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)

        # Which spectrum the buttons act on, so it's never ambiguous.
        self._target_lbl = QtWidgets.QLabel('(no spectrum loaded)')
        self._target_lbl.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._target_lbl)

        self._btn_layout = QtWidgets.QVBoxLayout()
        self._btn_layout.setSpacing(4)
        layout.addLayout(self._btn_layout)

        nav = QtWidgets.QHBoxLayout()
        self._btn_prev = QtWidgets.QPushButton('◀')
        self._btn_next = QtWidgets.QPushButton('▶')
        self._btn_prev.setToolTip('Previous record (←)')
        self._btn_next.setToolTip('Next record (→)')
        self._btn_prev.clicked.connect(lambda: self.step_requested.emit(-1))
        self._btn_next.clicked.connect(lambda: self.step_requested.emit(+1))
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._btn_next)
        layout.addLayout(nav)

        hint = QtWidgets.QLabel('Keys: number toggles label · ←/→ step')
        hint.setStyleSheet('color: #999999;')
        layout.addWidget(hint)
        layout.addStretch()
        # No shortcuts of our own: Qt resolves a Tool window's shortcuts in
        # its parent's window context, so keys registered here would clash
        # with (and cancel) the parent's identical ones. The parent installs
        # them once via install_shortcuts() and they work in both windows.

    def set_definitions(self, definitions) -> None:
        """Rebuild the buttons from [{"key", "display_name", "color"}, ...].
        Keeps check marks for keys that survive the rebuild."""
        checked = self.values()
        for btn in self._buttons.values():
            self._btn_layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons = {}
        for n, d in enumerate(definitions, start=1):
            key = d['key']
            name = d.get('display_name') or key
            btn = QtWidgets.QPushButton(f'{n}   {name}' if n <= 9 else name)
            btn.setCheckable(True)
            btn.setChecked(key in checked)
            btn.setToolTip(f'Toggle the "{name}" label on the current spectrum')
            btn.setStyleSheet(
                'QPushButton { text-align: left; padding: 4px 8px; }'
                f' QPushButton:checked {{ background-color: {d.get("color", "#b8b7b2")};'
                ' color: black; }')
            btn.toggled.connect(lambda on, k=key: self.label_toggled.emit(k, on))
            self._btn_layout.addWidget(btn)
            self._buttons[key] = btn

    def install_shortcuts(self, window: QtWidgets.QWidget) -> None:
        """Number keys 1..9 toggle the matching button while `window` is
        active. Text fields that have focus keep their digits (Qt's
        ShortcutOverride), so typing numbers elsewhere is unaffected."""
        for i in range(1, 10):
            sc = QtGui.QShortcut(QtGui.QKeySequence(str(i)), window)
            sc.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda i=i: self._toggle_nth(i - 1))

    def _toggle_nth(self, n: int) -> None:
        # Only while the window is open — the keys are a labelling mode,
        # not a global feature of the log window.
        btns = list(self._buttons.values())
        if self.isVisible() and 0 <= n < len(btns) and btns[n].isEnabled():
            btns[n].click()

    def set_labels_enabled(self, enabled: bool) -> None:
        """Grey out the label buttons (nothing loaded / read-only folder)
        while leaving ◀ ▶ usable."""
        for btn in self._buttons.values():
            btn.setEnabled(enabled)

    def set_target(self, text: str) -> None:
        self._target_lbl.setText(text or '(no spectrum loaded)')

    def set_values(self, values) -> None:
        """Programmatic update of the check marks. No emit."""
        values = set(values)
        for key, btn in self._buttons.items():
            btn.blockSignals(True)
            try:
                btn.setChecked(key in values)
            finally:
                btn.blockSignals(False)

    def values(self) -> set[str]:
        return {k for k, b in self._buttons.items() if b.isChecked()}

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
