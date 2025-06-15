"""
Example showing how to communicate with an Optronic Laboratories 16A DC Current Source.
To install MSL-Equipment run
pip install https://github.com/MSLNZ/msl-equipment/releases/download/v0.1.0/msl_equipment-0.1.0-py2.py3-none-any.whl
"""
from msl.equipment import ConnectionRecord
from msl.equipment import EquipmentRecord

record = EquipmentRecord(
    manufacturer='Optronic Laboratories',
    model='16A',
    connection=ConnectionRecord(
        address='COM3',
        # properties={'address': 1, 'delay': 0.1},  # optional settings
        timeout=2
    )
)

# connect to the current source
ol16a = record.connect()

# turn the output off
ol16a.turn_off()

# select a lamp
ol16a.select_lamp(9)

# get target information: lamp number, target value, target unit
print('target info: {}'.format(ol16a.target_info()))

# get the output state (on or off)
print('is the output on? {}'.format(ol16a.state()))

# set the target current
ol16a.set_current(0.2345)

# get the system status byte of the latest command that was executed
print('system status byte: {:b}'.format(ol16a.system_status_byte))

# read the output current, voltage and wattage
current = ol16a.get_current()
voltage = ol16a.get_voltage()
wattage = ol16a.get_wattage()
print('output current is {} A'.format(current))
print('output voltage is {} V'.format(voltage))
print('output wattage is {} W'.format(wattage))

# get the number of hours for lamp 9
hours = ol16a.get_setup(9, 40)
print('hours: {}'.format(hours))

# disconnect from the current source
ol16a.disconnect()
