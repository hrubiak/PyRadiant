import sys
import os
import copy
import json
from epics import caget, caput, PV
from PyQt6 import QtWidgets, QtCore


SETTINGS_FILE = "FilePathSettings.json"


class AD_filePathSetting():
    def __init__(self, experiment_name, folder_path, pv_name, filename_format):
        self.experiment_name = experiment_name
        self.folder_path = folder_path
        self.file_format = filename_format
        self.pv_name = pv_name
        self.file_path_pv_name = self.pv_name + ':FilePath'
        self.file_name_pv_name = self.pv_name + ':FileName'
        self.file_number_pv_name = self.pv_name + ':FileNumber'
        self.put_settings()

    def put_settings(self):
        caput(self.file_path_pv_name, self.folder_path)
        caput(self.file_name_pv_name, self.experiment_name)
        caput(self.file_number_pv_name, 1)


class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        # Load settings from file
        self.load_settings()

        # Create layout
        layout = QtWidgets.QVBoxLayout()

        # Create text input fields for experiment name and LH_PATH_WINDOWS
        layout.addWidget(QtWidgets.QLabel("Experiment name"))
        self.experiment_name_input = QtWidgets.QLineEdit(self)
        self.experiment_name_input.setPlaceholderText('Enter experiment name')
        layout.addWidget(self.experiment_name_input)

        layout.addWidget(QtWidgets.QLabel("LH folder path"))
        self.lh_path_input = QtWidgets.QLineEdit(self)
        self.lh_path_input.setText(self.settings['lh_path'])
        layout.addWidget(self.lh_path_input)

        # Create button to run the code
        self.ok_button = QtWidgets.QPushButton('OK')
        self.ok_button.clicked.connect(self.run_code)
        layout.addWidget(self.ok_button)

        # Set layout
        self.setLayout(layout)

    def load_settings(self):
        # Load settings from the 'settings.json' file in the same directory as the script
        try:
            with open(SETTINGS_FILE, "r") as file:
                self.settings = json.load(file)
        except FileNotFoundError:
            # If the file doesn't exist, initialize with default values
            self.settings = {
                "LF_PV": "16LF1",
                "Up_cam_PV": "16SP1",
                "Dn_cam_PV": "16SP2",
                "lh_path" : "\\\\pantera\\data\\16idb\\Data\\2024-2\\LH"
            }

    def run_code(self):
        # Get the values from input fields
        experiment_name = self.experiment_name_input.text()
        LH_PATH_WINDOWS = self.lh_path_input.text()
        LF_PV = self.settings["LF_PV"]
        Up_cam_PV = self.settings["Up_cam_PV"]
        Dn_cam_PV = self.settings["Dn_cam_PV"]

        # Define filename formats
        filename_format_spe = '%s_%5.5d'
        filename_format_tif = '%s%s_%5.5d.tif'
        filename_format_jpg = '%s%s_%5.5d.jpg'
        filename_format_hdf5 = '%s%s_%5.5d.h5'

        # Create paths
        exp_path = os.path.join(os.path.normpath(LH_PATH_WINDOWS), experiment_name)

        T_path = os.path.join(exp_path, 'T')
        T_path_spe = os.path.join(T_path, 'spe')
        T_path_tif = os.path.join(T_path, 'tif')

        T_filename = copy.copy(experiment_name)

        img_path = os.path.join(exp_path, 'Images')
        img_path_dn = os.path.join(img_path, 'Dn')
        img_path_up = os.path.join(img_path, 'Up')

        img_filename_dn = copy.copy(experiment_name) + '_Dn'
        img_filename_up = copy.copy(experiment_name) + '_Up'

        img_path_dn_tif = os.path.join(img_path_dn, 'tif')
        img_path_dn_jpg = os.path.join(img_path_dn, 'jpg')

        img_path_up_tif = os.path.join(img_path_up, 'tif')
        img_path_up_jpg = os.path.join(img_path_up, 'jpg')

        # Create all necessary folders
        all_folders = [LH_PATH_WINDOWS,
                       exp_path,
                       T_path,
                       T_path_spe,
                       T_path_tif,
                       img_path,
                       img_path_dn,
                       img_path_up,
                       img_path_dn_tif,
                       img_path_dn_jpg,
                       img_path_up_tif,
                       img_path_up_jpg]

        for folder in all_folders:
            if len(folder):
                if not os.path.isdir(folder):
                    os.mkdir(folder)
                    print(f'created folder: {folder}')

        # Set up file paths and file names
        LF_TIFF1_pv_name = LF_PV + ':TIFF1'
        LF_TIFF1_setting = AD_filePathSetting(experiment_name, T_path_tif, LF_TIFF1_pv_name, filename_format_tif)

        LF_cam1_pv_name = LF_PV + ':cam1'
        LF_cam1_setting = AD_filePathSetting(experiment_name, T_path_spe, LF_cam1_pv_name, filename_format_spe)

        Up_cam_TIFF1_pv_name = Up_cam_PV + ':TIFF1'
        Up_cam_TIFF1_setting = AD_filePathSetting(experiment_name, img_path_up_tif, Up_cam_TIFF1_pv_name, filename_format_tif)

        Dn_cam_TIFF1_pv_name = Dn_cam_PV + ':TIFF1'
        Dn_cam__TIFF1_setting = AD_filePathSetting(experiment_name, img_path_dn_tif, Dn_cam_TIFF1_pv_name, filename_format_tif)

        Up_cam_JPEG1_pv_name = Up_cam_PV + ':JPEG1'
        Up_cam_JPEG1_setting = AD_filePathSetting(experiment_name, img_path_up_jpg, Up_cam_JPEG1_pv_name, filename_format_jpg)

        Dn_cam_JPEG1_pv_name = Dn_cam_PV + ':JPEG1'
        Dn_cam_JPEG1_setting = AD_filePathSetting(experiment_name, img_path_dn_jpg, Dn_cam_JPEG1_pv_name, filename_format_jpg)

        print("Settings applied successfully!")


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.setWindowTitle('Experiment Setup')
    window.resize(400, 200)
    window.show()
    sys.exit(app.exec())