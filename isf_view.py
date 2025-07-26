"""
Usage:
1. In the application, go to File → Open Folder to select the directory containing your `.isf` and/or `.csv` files.
2. Browse the folder tree on the left and click on any file to display its waveform on the right.
3. For `.isf` files, the two-column data is plotted automatically.
4. For `.csv` files, all channels are plotted with a legend indicating each channel name.
"""

import sys
import os
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QTreeView, QFileDialog, QMessageBox, QSplitter
)
from PyQt6.QtGui import QAction, QFileSystemModel
from PyQt6.QtCore import Qt
import pyqtgraph as pg

class ISFViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Oscilloscope ISF/CSV Viewer")
        self.resize(1000, 600)

        # Central widget and layout
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Menu bar with folder selection
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open Folder", self)
        open_action.triggered.connect(self.select_root_folder)
        file_menu.addAction(open_action)

        # Splitter for tree and plot
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # File system tree: .isf and .csv
        self.model = QFileSystemModel()
        self.model.setNameFilters(["*.isf", "*.csv"])
        self.model.setNameFilterDisables(False)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self.on_tree_clicked)
        splitter.addWidget(self.tree)
        self.tree.setMinimumWidth(250)

        # Plot area
        self.plotWidget = pg.PlotWidget()
        splitter.addWidget(self.plotWidget)

    def select_root_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Data Root Folder")
        if folder:
            self.model.setRootPath(folder)
            self.tree.setRootIndex(self.model.index(folder))

    def on_tree_clicked(self, index):
        path = self.model.filePath(index)
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            pi = self.plotWidget.getPlotItem()
            pi.clear()
            # Handle ISF files: two-column ASCII
            if ext == '.isf':
                try:
                    data = np.loadtxt(path, delimiter=',')
                    if data.ndim == 2 and data.shape[1] >= 2:
                        x = data[:, 0]
                        y = data[:, 1]
                        pi.plot(x, y, pen='b')
                    else:
                        QMessageBox.warning(
                            self,
                            "Format Error",
                            f"File '{os.path.basename(path)}' does not have two columns."
                        )
                except Exception as e:
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"Failed to load '{os.path.basename(path)}': {e}"
                    )
            # Handle CSV files: header + units + data
            elif ext == '.csv':
                try:
                    # Read header and skip unit line
                    with open(path, 'r') as f:
                        header = f.readline().strip().split(',')
                        _ = f.readline()
                    data = np.loadtxt(path, delimiter=',', skiprows=2)
                    if data.ndim == 2 and data.shape[1] >= 2:
                        x = data[:, 0]
                        # Add legend to distinguish channels
                        pi.addLegend()
                        for i, name in enumerate(header[1:]):
                            y = data[:, i + 1]
                            pi.plot(x, y, pen=pg.intColor(i), name=name)
                    else:
                        QMessageBox.warning(
                            self,
                            "Format Error",
                            f"CSV '{os.path.basename(path)}' has insufficient columns."
                        )
                except Exception as e:
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"Failed to load CSV '{os.path.basename(path)}': {e}"
                    )

if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = ISFViewer()
    viewer.show()
    sys.exit(app.exec())