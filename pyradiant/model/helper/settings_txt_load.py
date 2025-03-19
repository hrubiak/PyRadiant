import re

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

