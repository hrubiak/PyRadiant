Some background
===

The original T-View, up to version 0.5.5, was based on LabVIEW. The T-View versions 0.6 and higher were originaly forked from T-Rax by Clemens Prescher https://github.com/CPrescher/T-Rax and combined with code ported from the legacy LabVIEW version of T-View. 

T-View
===

A python GUI program for fast visual analysis of thermal spectra collected mostly during high pressure diamond anvil 
cell experiments.

 
Currently, the only input files allowed are Princeton Instruments \*.spe file saved either from WinSpec (File Version 2) 
or Lightfield (File Version 3).

Maintainer
===


Ross Hrubiak (hrubiak@anl.gov)  
High Pressue Collaborative Access Team, Argonne National Laboratory


Requirements
===

- Python 3
- PyQt5
- numpy
- scipy
- pyqtgraph
- dateutils
- lmfit
- h5py
    
Installation
===

Except for PyQt5, all of those packages can be easily installed using "pip" as python package manager. If you are on
Windows or Mac, please try to install PyQt5 by using a precompiled python distribution such as anaconda, enthought,
winpython or Python(x,y). On Linux PyQt usually can be easily installed using the packagemanager.

Using the minimum anaconda distribution, you have to only type the following two commands:

    conda install pyqt numpy scipy h5py PyQt5 pyqtgraph
    pip install dateutils lmfit pyshortcuts
    
The program itself can then be run by going into the "t-view" directory and type:
    
    python run_t-view.py






    

