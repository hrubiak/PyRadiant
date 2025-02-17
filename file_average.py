import numpy as np
import os
from scipy.ndimage import gaussian_filter1d
import matplotlib.pyplot as plt

folder_path = '/Volumes/hrubiak/Files/Data_analysis/2025-1/HPCAT/LHCommissioning/20250213_W-lamp-run28/T/spe/Dn/exported/2.5amp'  # Change this to your folder path
output_file = '/Volumes/hrubiak/Files/Data_analysis/2025-1/HPCAT/LHCommissioning/20250213_W-lamp-run28/T/spe/Dn/exported/averaged_output_2.5amp.txt'

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

# Pad the averaged y values to avoid edge artifacts
pad_width = 15  # Adjust padding as needed
padded_y = np.pad(average_y, pad_width, mode='edge')

# Apply Gaussian filter
sigma = 7  # Adjust sigma as needed
filtered_y = gaussian_filter1d(padded_y, sigma=sigma)

# Trim the padded values
filtered_y = filtered_y[pad_width:-pad_width]

# Stack x and filtered y columns
output_data = np.column_stack((x_values, filtered_y))

# Save the output to a file
np.savetxt(output_file, output_data, fmt='%.6f', comments='')

# Plot the averaged y and filtered y
plt.figure(figsize=(10, 6))
plt.plot(x_values, average_y, label='Averaged y', alpha=0.7)
plt.plot(x_values, filtered_y, label='Filtered y', linewidth=2)
plt.legend()
plt.title('Averaged vs Filtered y')
plt.xlabel('x')
plt.ylabel('y')
plt.grid(True)
plt.show()

# Zoomed-in plot
zoom_start, zoom_end = 50, 100  # Adjust zoom range as needed
plt.figure(figsize=(10, 6))
plt.plot(x_values[zoom_start:zoom_end], average_y[zoom_start:zoom_end], label='Averaged y', alpha=0.7)
plt.plot(x_values[zoom_start:zoom_end], filtered_y[zoom_start:zoom_end], label='Filtered y', linewidth=2)
plt.legend()
plt.title('Zoomed-in View: Averaged vs Filtered y')
plt.xlabel('x')
plt.ylabel('y')
plt.grid(True)
plt.show()

print(f"Filtered and averaged data saved to {output_file}")