# -*- coding: utf-8 -*-
from setuptools import setup, find_packages
import versioneer

setup(name='t-view',
      version=versioneer.get_version(),
      cmdclass=versioneer.get_cmdclass(),
      license='GPLv3',
      author='Clemens Prescher, Eran Greenberg',
      author_email="clemens.prescher@gmail.com",
      url='https://github.com/CPrescher/T-View/',
      install_requires=['python-dateutil', 'pyqt5' , 'qtpy', 'pyqtgraph', 'h5py', 'lmfit', 'pyshortcuts'],
      package_dir={'t_view': 't_view'},
      packages=find_packages(),
      package_data={'t_view': ['resources/style/*.qss', 'resources/icons/*']},
      entry_points = {'console_scripts' : ['t-view = t_view:run_t_view']},
      description='GUI program for optical spectroscopy',
      classifiers=['Intended Audience :: Science/Research',
                   'Operating System :: OS Independent',
                   'Programming Language :: Python',
                   'Topic :: Scientific/Engineering',
                   ],
)
