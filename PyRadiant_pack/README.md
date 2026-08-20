PyRadiant
===

PyRadiant is a Python-based graphical user interface (GUI) application designed for fast, visual analysis of thermal emission spectra collected during laser-heated diamond anvil cell (DAC) experiments. It provides real-time feedback by fitting black-body radiation models to the data, allowing users to estimate sample temperature during high-pressure experiments.

Originally based on code forked from T-Rax <https://github.com/CPrescher/T-Rax>_, PyRadiant combines this with functionality ported from the legacy LabVIEW program **T-View**, and includes numerous new features for usability and beamline integration.

The application supports Princeton Instruments .spe files collected using WinSpec (File Version 2) or LightField (File Version 3).

PyRadiant can optionally connect to EPICS to read detector data directly and publish fitted temperature values to process variables (PVs), enabling display on beamline control screens or archival as experimental metadata.

Maintainer
===


Ross Hrubiak (hrubiak@anl.gov)  
High Pressue Collaborative Access Team, Argonne National Laboratory


PyRadiant Installation Guide (via Conda)
========================================


Requirements
------------
- Anaconda or Miniforge installed (Python 3.12+ recommended)
- Internet connection

Step-by-Step Setup
------------------

1. Download the Code

   Download the PyRadiant source code from GitHub as a ZIP file and extract it to a local folder.

2. Open a Terminal and Navigate to the Folder

   For example:

       cd /path/to/PyRadiant

3. Create and Activate a Conda Environment

       conda create -n pyradiant_env python=3.12 -y
       conda activate pyradiant_env

4. Install Python Dependencies

       pip install -r requirements.txt
       pip install pyqtdarktheme==2.1.0 --ignore-requires-python

5. Launch PyRadiant

       python run_pyradiant.py

   Or use one of the wrapper scripts described below for convenience.

Notes
-----
- `pyqtdarktheme` requires `--ignore-requires-python` due to overly strict metadata.


Wrapper Launch Scripts
----------------------

To make launching PyRadiant easier, two wrapper scripts are provided:

**macOS/Linux**

1. Open a terminal.
2. Navigate to the PyRadiant folder:

       cd /path/to/PyRadiant

3. Make the script executable (first time only):

       chmod +x run_pyradiant.sh

4. Run the script:

       ./run_pyradiant.sh

This will activate the Conda environment and launch the application.

---

**Windows**

1. Double-click the file `run_pyradiant.bat` in the PyRadiant folder.

   This will activate the Conda environment and launch the application in a terminal window.

2. If it closes immediately after running, you can open a Command Prompt manually, navigate to the folder, and run:

       run_pyradiant.bat

---