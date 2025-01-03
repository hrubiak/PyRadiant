import os
import shutil

def organize_tif_files(directory, n):
    """
    Organizes TIFF files in the specified directory into subfolders based on their numbering pattern,
    only if the number string after the last underscore is exactly `n` characters long.

    Args:
        directory (str): Path to the directory containing the TIFF files.
        n (int): The required length of the number string after the last underscore.
    """
    if not os.path.exists(directory):
        print(f"The directory {directory} does not exist.")
        return

    # Iterate through all files in the directory
    for file in os.listdir(directory):
        if file.endswith(".tif") and "_" in file:
            # Extract the part after the last underscore and before .tif
            file_suffix = file.rsplit("_", 1)[-1]
            number_string = file_suffix.split(".")[0]

            # Check if the number string length matches `n`
            if len(number_string) == n and number_string.isdigit():
                folder_name = number_string

                # Create the subfolder if it doesn't exist
                folder_path = os.path.join(directory, folder_name)
                os.makedirs(folder_path, exist_ok=True)

                # Move the file to the appropriate folder
                src = os.path.join(directory, file)
                dest = os.path.join(folder_path, file)
                shutil.move(src, dest)
                print(f"Moved {file} to {folder_path}")

# Example usage
directory = "/Volumes/hrubiak/Files/Data_analysis/2024-3/20241105-LH-devel/Ir-MZ"  # Replace with the path to your folder
required_length = 3  # Set the required length of the number string
organize_tif_files(directory, required_length)
