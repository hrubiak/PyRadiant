import numpy as np
import os

folder_path = '/Volumes/hrubiak/Files/Data_analysis/2025-1/HPCAT/LHCommissioning/20250213_W-lamp-run28/T/spe/Up/exported/2.5amp'  # Change this to your folder path
output_file = '/Volumes/hrubiak/Files/Data_analysis/2025-1/HPCAT/LHCommissioning/20250213_W-lamp-run28/T/spe/Up/exported/averaged_output_2.5amp.txt'

# List all .txt files in the folder
files = [f for f in os.listdir(folder_path) if f.endswith('.txt')]

# Initialize lists to store data
x_values = None
y_values_list = []

# Read data from each file
for file in files:
    data = np.loadtxt(os.path.join(folder_path, file))
    if x_values is None:
        x_values = data[:, 0]  # Store x values from the first file
    y_values_list.append(data[:, 1])

# Convert list of y-values to a 2D numpy array
y_values_array = np.array(y_values_list)

# Compute the average across all files
average_y = np.mean(y_values_array, axis=0)

# Stack x and average y columns
output_data = np.column_stack((x_values, average_y))

# Save the output to a file
np.savetxt(output_file, output_data, fmt='%.6f', header='x average_y', comments='')

print(f"Averaged data saved to {output_file}")
