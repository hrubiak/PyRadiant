from tifffile import TiffFile
from PIL import Image
import numpy as np
import shutil


# Step 1: Define paths
original_path = '/Users/hrubiak/Desktop/badpix_mask.tif'     # Path to your original TIFF from TVX
new_tif_path = '/Users/hrubiak/Desktop/badpix_mask_new_python_4.tif'           # Output file to be created
png_path = '/Users/hrubiak/Desktop/20250610-glass-slide_120sec_00021.png'      # Your replacement data (must match shape/dtype)


# --- Copy the original file (preserve header)
shutil.copyfile(original_path, new_tif_path)

# --- Step 1: Read original TIFF metadata and pixel data
with TiffFile(original_path) as tif:
    page = tif.pages[0]
    offset = page.dataoffsets[0]
    dtype = page.dtype
    shape = page.shape
    original_data = page.asarray()

# --- Step 2: Load the PNG as 8-bit grayscale and resize
img = Image.open(png_path).convert('L')  # Convert to 8-bit grayscale
img = img.resize((shape[1], shape[0]))   # Resize to match (width, height)
png_data = np.array(img)
png_data = np.flipud(png_data)                  # Flip vertically

# --- Step 3: Threshold to binary (0 or 1)
new_data = (png_data > 0).astype(np.uint8)     # or np.bool_ if you prefer
original_binary = (original_data > 0).astype(np.uint8)

# --- Step 4: Perform logical OR
combined_data = ((original_binary | new_data) > 0).astype(dtype)

# --- Step 5: Sanity checks
assert combined_data.shape == shape
assert combined_data.dtype == dtype

# --- Step 6: Overwrite pixel data in the new file
with open(new_tif_path, 'r+b') as f:
    f.seek(offset)
    f.write(combined_data.tobytes())

print("New TIFF written with logical OR of original and PNG.")