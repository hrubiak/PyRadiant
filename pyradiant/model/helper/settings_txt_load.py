import re
import h5py

def parse_txt_settings(file_path):
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

def save_setting_h5py( filename):

        data = parse_txt_settings(filename)
        f = h5py.File(filename, 'w')

        f.create_group('downstream_calibration')
        ds_group = f['downstream_calibration']
        if self.ds_calibration_img_file is not None:
            ds_group['image'] = # self.ds_calibration_img_file.img
            ds_group['image'].attrs['filename'] = # self.ds_calibration_img_file.filename
            ds_group['image'].attrs['x_calibration'] = # self.ds_calibration_img_file.x_calibration
            ds_group['image'].attrs['subtract_bg'] = # self.use_insitu_data_background
        else:
            if self.ds_temperature_model.calibration_img is not None:
                ds_group['image'] = # self.ds_temperature_model.calibration_img
                ds_group['image'].attrs['filename'] = # self.ds_calibration_filename
                ds_group['image'].attrs['x_calibration'] = # self.ds_temperature_model._data_img_x_calibration
                ds_group['image'].attrs['subtract_bg'] = # self.use_insitu_data_background
        ds_group['roi'] = # self.ds_roi.as_list()
        ds_group['roi_bg'] = # self.ds_roi_bg.as_list()
        ds_group['modus'] = # self.ds_temperature_model.calibration_parameter.modus
        ds_group['temperature'] = # self.ds_temperature_model.calibration_parameter.temperature
        ds_group['standard_spectrum'] = # self.ds_temperature_model.calibration_parameter.get_standard_spectrum().data
        ds_group['standard_spectrum'].attrs['filename'] = # \
            # self.ds_temperature_model.calibration_parameter.get_standard_filename()
        ds_group['standard_spectrum'].attrs['subtract_bg'] = # self.use_insitu_calibration_background

        f.create_group('upstream_calibration')
        us_group = f['upstream_calibration']
        if self.us_calibration_img_file is not None:
            us_group['image'] = # self.us_calibration_img_file.img
            us_group['image'].attrs['filename'] = # self.us_calibration_img_file.filename
            us_group['image'].attrs['x_calibration'] = # self.us_calibration_img_file.x_calibration
            us_group['image'].attrs['subtract_bg'] = # self.use_insitu_data_background
        else:
            if self.us_temperature_model.calibration_img is not None:
                us_group['image'] = # self.us_temperature_model.calibration_img
                us_group['image'].attrs['filename'] = # self.us_calibration_filename
                us_group['image'].attrs['x_calibration'] = # self.us_temperature_model._data_img_x_calibration
                us_group['image'].attrs['subtract_bg'] = # self.use_insitu_data_background
        us_group['roi'] = # self.us_roi.as_list()
        us_group['roi_bg'] = # self.us_roi_bg.as_list()
        us_group['modus'] = # self.us_temperature_model.calibration_parameter.modus
        us_group['temperature'] = # self.us_temperature_model.calibration_parameter.temperature
        us_group['standard_spectrum'] = # self.us_temperature_model.calibration_parameter.get_standard_spectrum().data
        us_group['standard_spectrum'].attrs['filename'] # = \
            # self.us_temperature_model.calibration_parameter.get_standard_filename()
        us_group['standard_spectrum'].attrs['subtract_bg'] = # self.use_insitu_calibration_background

        f.close()