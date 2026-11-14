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


_SIDES = (('ds', 'DS'), ('us', 'US'))


class SpectrumLabelWindow(QtWidgets.QWidget):
    """Small floating window for manually labelling the current spectrum.

    Opened from the Temperature Log window and kept above it (Tool window),
    so the user can step through records there and label them here. Labels
    are per side — DS can be good while US is junk — so there is one column
    of checkable buttons per side; several labels can be on at once.

    Holds no state beyond the button check marks — the controller pushes the
    current (file, frame) and its labels in via set_target()/set_values()
    and listens to label_toggled(side, key, checked). step_requested(delta)
    asks the log to move to the previous/next record.
    """

    label_toggled = QtCore.pyqtSignal(str, str, bool)   # side, key, checked
    step_requested = QtCore.pyqtSignal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent, QtCore.Qt.WindowType.Tool)
        self.setWindowTitle('Label spectrum')
        # side → key → button, in definition order
        self._buttons: dict[str, dict[str, QtWidgets.QPushButton]] = {s: {} for s, _ in _SIDES}
        self._mode = 'dual'

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)

        # Which spectrum the buttons act on, so it's never ambiguous.
        self._target_lbl = QtWidgets.QLabel('(no spectrum loaded)')
        self._target_lbl.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._target_lbl)

        self._grid = QtWidgets.QGridLayout()
        self._grid.setSpacing(4)
        self._side_headers = {}
        for col, (side, title) in enumerate(_SIDES):
            hdr = QtWidgets.QLabel(title)
            hdr.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(hdr, 0, col)
            self._side_headers[side] = hdr
        layout.addLayout(self._grid)

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

        self._hint = QtWidgets.QLabel()
        self._hint.setStyleSheet('color: #999999;')
        layout.addWidget(self._hint)
        layout.addStretch()
        # No shortcuts of our own: Qt resolves a Tool window's shortcuts in
        # its parent's window context, so keys registered here would clash
        # with (and cancel) the parent's identical ones. The parent installs
        # them once via install_shortcuts() and they work in both windows.

    def set_definitions(self, definitions) -> None:
        """Rebuild the buttons from [{"key", "display_name", "color"}, ...].
        Keeps check marks for keys that survive the rebuild."""
        checked = self.values()
        for side_btns in self._buttons.values():
            for btn in side_btns.values():
                self._grid.removeWidget(btn)
                btn.deleteLater()
        self._buttons = {s: {} for s, _ in _SIDES}
        n_defs = len(definitions)
        for row, d in enumerate(definitions, start=1):
            key = d['key']
            name = d.get('display_name') or key
            for col, (side, title) in enumerate(_SIDES):
                n = row + col * n_defs          # DS 1..N, US N+1..2N
                btn = QtWidgets.QPushButton(f'{n}   {name}' if n <= 9 else name)
                btn.setCheckable(True)
                btn.setChecked(key in checked.get(side, ()))
                btn.setToolTip(f'Toggle "{name}" on the {title} side of the current spectrum')
                btn.setStyleSheet(
                    'QPushButton { text-align: left; padding: 4px 8px; }'
                    f' QPushButton:checked {{ background-color: {d.get("color", "#b8b7b2")};'
                    ' color: black; }')
                btn.toggled.connect(
                    lambda on, s=side, k=key: self.label_toggled.emit(s, k, on))
                self._grid.addWidget(btn, row, col)
                self._buttons[side][key] = btn
        self.set_mode(self._mode)

    def set_mode(self, mode: str) -> None:
        """'single' hides the US column (and its keys do nothing)."""
        self._mode = mode
        dual = mode != 'single'
        self._side_headers['us'].setVisible(dual)
        self._side_headers['ds'].setVisible(dual)
        for btn in self._buttons['us'].values():
            btn.setVisible(dual)
        n = len(self._buttons['ds'])
        if dual and n:
            self._hint.setText(f'Keys: 1–{n} DS · {n + 1}–{min(2 * n, 9)} US · ←/→ step')
        else:
            self._hint.setText('Keys: number toggles label · ←/→ step')

    def install_shortcuts(self, window: QtWidgets.QWidget) -> None:
        """Number keys 1..9 toggle the matching button while `window` is
        active: DS buttons first, then US. Text fields that have focus keep
        their digits (Qt's ShortcutOverride), so typing numbers elsewhere is
        unaffected."""
        for i in range(1, 10):
            sc = QtGui.QShortcut(QtGui.QKeySequence(str(i)), window)
            sc.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda i=i: self._toggle_nth(i - 1))

    def _toggle_nth(self, n: int) -> None:
        # Only while the window is open — the keys are a labelling mode,
        # not a global feature of the log window.
        btns = list(self._buttons['ds'].values()) + list(self._buttons['us'].values())
        if (self.isVisible() and 0 <= n < len(btns) and btns[n].isEnabled()
                and btns[n].isVisible()):
            btns[n].click()

    def set_labels_enabled(self, enabled: bool) -> None:
        """Grey out the label buttons (nothing loaded / read-only folder)
        while leaving ◀ ▶ usable."""
        for side_btns in self._buttons.values():
            for btn in side_btns.values():
                btn.setEnabled(enabled)

    def set_target(self, text: str) -> None:
        self._target_lbl.setText(text or '(no spectrum loaded)')

    def set_values(self, sides: dict) -> None:
        """Programmatic update of the check marks from {"ds": keys,
        "us": keys}. No emit."""
        for side, side_btns in self._buttons.items():
            on = set((sides or {}).get(side, ()))
            for key, btn in side_btns.items():
                btn.blockSignals(True)
                try:
                    btn.setChecked(key in on)
                finally:
                    btn.blockSignals(False)

    def values(self) -> dict:
        """{"ds": {keys}, "us": {keys}} currently checked."""
        return {side: {k for k, b in side_btns.items() if b.isChecked()}
                for side, side_btns in self._buttons.items()}

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
