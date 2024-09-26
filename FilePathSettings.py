
LH_PATH_WINDOWS = '\\\\pantera\\data\\16idb\\Data\\2024-2\\LH'
experiment_name = '20240926-Ir-wire-run11'

LF_PV = '16LF1'
Up_cam_PV = '16SP1'
Dn_cam_PV = '16SP2'

filename_format_spe = '%s_%5.5d'
filename_format_tif = '%s%s_%5.5d.tif'
filename_format_jpg= '%s%s_%5.5d.jpg'
filename_format_hdf5= '%s%s_%5.5d.h5'

#########################################################################

import os
import copy
from epics import caget, caput, PV

class AD_filePathSetting():
    def __init__(self, experiment_name, folder_path, pv_name, filename_format):
        self.experiment_name = experiment_name
        self.folder_path = folder_path
        self.file_format = filename_format
        self.pv_name = pv_name
        self.file_path_pv_name = self.pv_name+':FilePath'
        self.file_name_pv_name = self.pv_name+':FileName'
        self.file_number_pv_name = self.pv_name+':FileNumber'
        self.put_settings()

    def put_settings(self):
        caput(self.file_path_pv_name, self.folder_path)
        caput(self.file_name_pv_name, self.experiment_name)
        caput(self.file_number_pv_name, 1)

exp_path= os.path.join(os.path.normpath(LH_PATH_WINDOWS),experiment_name)

T_path = os.path.join(exp_path,'T')
T_path_spe = os.path.join(T_path,'spe')
T_path_tif = os.path.join(T_path,'tif')

T_filename = copy.copy(experiment_name)

img_path = os.path.join(exp_path,'Images')
img_path_dn = os.path.join(img_path,'Dn')
img_path_up = os.path.join(img_path,'Up')

img_filename_dn = copy.copy(experiment_name)+ '_Dn'
img_filename_up = copy.copy(experiment_name)+ '_Up'

img_path_dn_tif = os.path.join(img_path_dn,'tif')
img_path_dn_jpg = os.path.join(img_path_dn,'jpg')

img_path_up_tif = os.path.join(img_path_up,'tif')
img_path_up_jpg = os.path.join(img_path_up,'jpg')

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

LF_TIFF1_pv_name = LF_PV+':TIFF1'
LF_TIFF1_setting = AD_filePathSetting(experiment_name, T_path_tif, LF_TIFF1_pv_name, filename_format_tif)

LF_cam1_pv_name = LF_PV+':cam1'
LF_cam1_setting = AD_filePathSetting(experiment_name, T_path_spe, LF_cam1_pv_name, filename_format_spe)

Up_cam_TIFF1_pv_name = Up_cam_PV+':TIFF1'
Up_cam_TIFF1_setting = AD_filePathSetting(experiment_name, img_path_up_tif, Up_cam_TIFF1_pv_name, filename_format_tif)

Dn_cam_TIFF1_pv_name = Dn_cam_PV+':TIFF1'
Dn_cam__TIFF1_setting = AD_filePathSetting(experiment_name, img_path_dn_tif, Dn_cam_TIFF1_pv_name, filename_format_tif)

Up_cam_JPEG1_pv_name = Up_cam_PV+':JPEG1'
Up_cam_JPEG1_setting = AD_filePathSetting(experiment_name, img_path_up_jpg, Up_cam_JPEG1_pv_name, filename_format_jpg)

Dn_cam_JPEG1_pv_name = Dn_cam_PV+':JPEG1'
Dn_cam_JPEG1_setting = AD_filePathSetting(experiment_name, img_path_dn_jpg, Dn_cam_JPEG1_pv_name, filename_format_jpg)

