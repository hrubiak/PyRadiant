import xml.etree.ElementTree as ET
import csv

# Adjust the filenames/paths below:
xml_filename = "recalculated-115-8May2017.xml"
csv_filename = "recalculated-115-8May2017.xml.csv"

# Parse the XML
tree = ET.parse(xml_filename)
root = tree.getroot()

# LabVIEW files often have this default namespace:
ns = {"lv": "http://www.ni.com/LVData"}

##############################################################################
# Step 1. Locate the <Array> named "T results"
##############################################################################
tresults_array = None
for arr in root.findall("lv:Array", ns):
    name_elem = arr.find("lv:Name", ns)
    if name_elem is not None and name_elem.text == "T results":
        tresults_array = arr
        break

if tresults_array is None:
    raise RuntimeError("Could not find an <Array> named 'T results' in the XML.")

##############################################################################
# Step 2. Within that array, each child <Cluster> is one 'T result'
##############################################################################
tresult_clusters = tresults_array.findall("lv:Cluster", ns)

# We'll accumulate rows of data here
data_rows = []

for tresult_cluster in tresult_clusters:

    # Find <String> named "File"
    file_string = tresult_cluster.find("lv:String[lv:Name='File']", ns)
    if file_string is not None:
        file_val_elem = file_string.find("lv:Val", ns)
        file_name = file_val_elem.text if file_val_elem is not None else ""
    else:
        file_name = ""

    # Find the frames array
    frames_array = tresult_cluster.find("lv:Array[lv:Name='frames']", ns)
    if frames_array is None:
        # If there's no frames array, skip this cluster
        continue

    # Each child <Cluster> in "frames" is one frame
    frame_clusters = frames_array.findall("lv:Cluster", ns)

    for frame_cluster in frame_clusters:
        # Initialize
        frame_num = None
        temp_dn   = None
        temp_up   = None

        # Loop over each I32 field in the frame cluster
        for i32_elem in frame_cluster.findall("lv:I32", ns):
            field_name_elem = i32_elem.find("lv:Name", ns)
            field_val_elem  = i32_elem.find("lv:Val", ns)

            if field_name_elem is not None and field_val_elem is not None:
                field_name = field_name_elem.text
                field_val  = field_val_elem.text

                if field_name == "Frame":
                    frame_num = field_val
                elif field_name == "Temperature Dn":
                    temp_dn = field_val
                elif field_name == "Temperature Up":
                    temp_up = field_val

        data_rows.append([file_name, frame_num, temp_dn, temp_up])

##############################################################################
# Step 3. Write out the CSV
##############################################################################
with open(csv_filename, "w", newline="") as f:
    writer = csv.writer(f)
    # header
    writer.writerow(["File", "Frame", "Temperature Dn", "Temperature Up"])
    # data
    writer.writerows(data_rows)

print(f"Done! Wrote {len(data_rows)} rows to {csv_filename}.")