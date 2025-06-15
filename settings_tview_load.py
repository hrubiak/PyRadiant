import re, os
import h5py
from pyradiant.model.data_models.SpeFile import SpeFile
from pyradiant.model.helper.HelperModule import get_partial_index
import copy
import numpy as np

def parse_tview_settings(file_path):
    data = {'i': {}, 'd': {}, 's': {}, 'b': {}, 'p': {}}
    current_type = None
    
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            
            # Identify the section type based on markers like [i], [d], etc.
            type_match = re.match(r'\[(i|d|s|b|p)\]', line)
            if type_match:
                current_type = type_match.group(1)
                continue
            
            # Skip empty lines
            if not line or current_type is None:
                continue
            
            # Extract key-value pairs
            match = re.match(r'(.+?)\s*=\s*(.+)', line)
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                
                # Convert values to appropriate data types
                if current_type == 'i':
                    value = int(value)
                elif current_type == 'd':
                    value = float(value)
                elif current_type == 'b':
                    value = value.upper() == "TRUE"
                elif current_type == 's' or current_type == 'p':
                    value = value.strip('"')  # Remove surrounding quotes if any
                
                data[current_type][key.replace(' ','_')] = value
    
    return data

def get_cal_temperature (data):
    suffix = ''

    detector_choice = data['b']['Detector_selection']

    if detector_choice:
        suffix = '_2'
    
    ds_temperature = data['d']['Temperature_Dn'+suffix]
    us_temperature = data['d']['Temperature_Up'+suffix]

    return ds_temperature, us_temperature

def get_rois(data,  x_calibration):
    # [int(self.x_min), int(self.x_max), int(self.y_min), int(self.y_max)]
    suffix = ''

    detector_choice = data['b']['Detector_selection']

    if detector_choice:
        suffix = '_2'

    full_range = data['b']['Full_range?'+suffix]
    if full_range:
        ds_start = 0
        ds_end = x_calibration.shape[0]-1
    else:

        ds_start_wl = data['d']['Start_Wavelength_(nm)'+suffix]
        ds_end_wl = data['d']['End_Wavelength_(nm)'+suffix]
        if ds_start_wl >= np.amin(x_calibration):

            ds_start = round(get_partial_index(x_calibration,ds_start_wl))
        else:
            ds_start = 0
        if ds_end_wl <= np.amax(x_calibration):

            ds_end = round(get_partial_index(x_calibration,ds_end_wl))
        else:
            ds_end = x_calibration.shape[0]-1
    ds_roi_list = [ds_start,ds_end, data['i']['Start_row_DN'+suffix], data['i']['bin_DN'+suffix]+data['i']['Start_row_DN'+suffix]]
    
    us_start = copy.copy(ds_start)
    us_end = copy.copy(ds_end)
    us_roi_list = [us_start,us_end, data['i']['Start_row_UP'+suffix], data['i']['bin_UP'+suffix]+data['i']['Start_row_UP'+suffix]]
    
    ds_bg_roi_list = [ds_start,ds_end, data['i']['Start_row_dc'+suffix], data['i']['bin_dc'+suffix]+data['i']['Start_row_dc'+suffix]]
    if 'Start_row_dc'+suffix+'_b' in data['i']:
        us_bg_roi_list = [us_start,us_end, data['i']['Start_row_dc'+suffix+'_b'], data['i']['bin_dc'+suffix+'_b']+data['i']['Start_row_dc'+suffix+'_b']]    
    else:
        us_bg_roi_list = copy.copy(ds_bg_roi_list)
    return ds_roi_list , us_roi_list, ds_bg_roi_list, us_bg_roi_list

def get_calib_paths(data, calib_data_path):
    folder = os.path.split(calib_data_path)[-1]
    print(folder)
    detector_choice = data['b']['Detector_selection']
    if detector_choice:
        ds_calib_file = os.path.split(data['p']['Downstream_side_calibration_file_2'])[-1]
        us_calib_file = os.path.split(data['p']['Upstream_side_calibration_file_2'])[-1]
        calib_folder = os.path.split(os.path.split(data['p']['Downstream_side_calibration_file_2'])[0])[-1]
    else:
        ds_calib_file = os.path.split(data['p']['Downstream_side_calibration_file'])[-1]
        us_calib_file = os.path.split(data['p']['Upstream_side_calibration_file'])[-1]
        calib_folder = os.path.split(os.path.split(data['p']['Downstream_side_calibration_file'])[0])[-1]
    calib_folder_found = folder ==calib_folder
    print(f'calib_folder_found {calib_folder_found}')

    ds_calib_filepath= os.path.join(calib_data_path, ds_calib_file)
    us_calib_filepath= os.path.join(calib_data_path, us_calib_file)

    return {'ds_calib_filepath':ds_calib_filepath, 'us_calib_filepath':us_calib_filepath}

def save_setting_h5py(calib_filename, tview_calib_filename, calib_data_path):
    data = parse_tview_settings(tview_calib_filename)
    calib_filepaths = get_calib_paths(data, calib_data_path)

    f = h5py.File(calib_filename, 'w')

    f.create_group('downstream_calibration')
    ds_group = f['downstream_calibration']
    ds_calibration_img_filepath = calib_filepaths['ds_calib_filepath']
    ds_calibration_img_file = SpeFile(ds_calibration_img_filepath)

    if ds_calibration_img_file is not None:
        ds_group['image'] = ds_calibration_img_file.img
        ds_group['image'].attrs['filename'] =  ds_calibration_img_file.filename
        ds_group['image'].attrs['x_calibration'] = ds_calibration_img_file.x_calibration
        ds_group['image'].attrs['subtract_bg'] = True # self.use_insitu_data_background
    '''else:
        if self.ds_temperature_model.calibration_img is not None:
            ds_group['image'] = # self.ds_temperature_model.calibration_img
            ds_group['image'].attrs['filename'] = data['p']['Downstream_side_calibration_file'] # self.ds_calibration_filename
            ds_group['image'].attrs['x_calibration'] = # self.ds_temperature_model._data_img_x_calibration
            ds_group['image'].attrs['subtract_bg'] =  True # self.use_insitu_data_background'''
    
    ds_roi_list , us_roi_list, ds_bg_roi_list, us_bg_roi_list = get_rois(data, ds_calibration_img_file.x_calibration)
    ds_group['roi'] = ds_roi_list

    ds_group['roi_bg'] = ds_bg_roi_list
    # modi: 0 - given temperature
    # 1 - standard spectrum
    ds_group['modus'] = 0 # self.ds_temperature_model.calibration_parameter.modus

    ds_temperature, us_temperature = get_cal_temperature(data)

    ds_group['temperature'] = ds_temperature
    ds_group['standard_spectrum'] = [[],[]]
    ds_group['standard_spectrum'].attrs['filename'] = ''
    ds_group['standard_spectrum'].attrs['subtract_bg'] = True

    f.create_group('upstream_calibration')
    us_group = f['upstream_calibration']
    us_calibration_img_filepath = calib_filepaths['us_calib_filepath']
    us_calibration_img_file = SpeFile(us_calibration_img_filepath)

    if us_calibration_img_file is not None:
        us_group['image'] = us_calibration_img_file.img
        us_group['image'].attrs['filename'] = us_calibration_img_file.filename
        us_group['image'].attrs['x_calibration'] = us_calibration_img_file.x_calibration
        us_group['image'].attrs['subtract_bg'] = True # self.use_insitu_data_background
    '''else:
        if self.us_temperature_model.calibration_img is not None:
            us_group['image'] = # self.us_temperature_model.calibration_img
            us_group['image'].attrs['filename'] = # self.us_calibration_filename
            us_group['image'].attrs['x_calibration'] = # self.us_temperature_model._data_img_x_calibration
            us_group['image'].attrs['subtract_bg'] = # self.use_insitu_data_background'''
    us_group['roi'] = us_roi_list # self.us_roi.as_list()
    us_group['roi_bg'] = us_bg_roi_list # self.us_roi_bg.as_list()
    us_group['modus'] = 0 # self.us_temperature_model.calibration_parameter.modus
    us_group['temperature'] = us_temperature # self.us_temperature_model.calibration_parameter.temperature
    us_group['standard_spectrum'] = [[],[]]
    us_group['standard_spectrum'].attrs['filename'] = ''
    us_group['standard_spectrum'].attrs['subtract_bg'] = True # self.use_insitu_calibration_background'''

    f.close()

tview_calib_filename = '/Users/hrubiak/Desktop/calibrations/Ex95_PIMAX.txt'
fname = os.path.split(tview_calib_filename)[-1].replace('txt','trs')


calib_data_path = '/Volumes/hrubiak/Files/Experiments_data/Ex095/temperature/00_Calibrations/PI-MAX'
calib_filename = os.path.join('/Users/hrubiak/Desktop/trs calibrations',fname)

save_setting_h5py(calib_filename, tview_calib_filename, calib_data_path)

