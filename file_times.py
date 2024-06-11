import os
from datetime import datetime

def get_file_creation_time(file_path):
    # Get the m time of the file
    return datetime.fromtimestamp(os.path.getmtime(file_path))

def create_file_creation_table(folder_path, output_file_path):
    # List to hold the file names and creation times
    files = []
    
    # Iterate over all files in the folder
    for file_name in os.listdir(folder_path):
        # Check if the file is a jpg file
        if file_name.lower().endswith('.h5'):
            file_path = os.path.join(folder_path, file_name)
            creation_time = get_file_creation_time(file_path)
            files.append((file_name, creation_time))
    
    # Sort the files by creation time
    files.sort(key=lambda x: x[1])
    
    # Open the output file in write mode
    with open(output_file_path, 'w') as file:
        # Write the header line
        file.write("File Name\tCreation Time\n")
        
        # Write the sorted file names and creation times to the output file
        for file_name, creation_time in files:
            file.write(f"{file_name}\t{creation_time}\n")

# Example usage
folder_path = '/Users/hrubiak/Desktop/LH/20240523-Pt-wire-run6/melting'
output_file_path = '/Users/hrubiak/Desktop/LH/20240523-Pt-wire-run6/melting_times_T.txt'
create_file_creation_table(folder_path, output_file_path)

print(f"File creation times have been written to {output_file_path}")
