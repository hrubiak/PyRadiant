# -*- coding: utf-8 -*-
"""
Created on Fri Jan  3 15:19:59 2025

@author: deansmith
"""

### This script opens a temperature data file, extracts the wavelength axis,
### and plots the step size as a function of wavelength

import sys
import os.path
import time
import numpy as np
import matplotlib.pyplot as plt

### specify where the files live
#filePath = 'C:/Software/pythonData/240715_Tview/'
### ask user to specify which file to open
#fileName = input('Which file:')
#dataFile = filePath+fileName+'.txt'
dataFile = '/Users/hrubiak/GitHub/PyRadiant/2colour_pyrometry/tempData/upstream.txt' 

### pull the x and y data out of the txt file as ndarray
wav = np.loadtxt(dataFile)[:, 0]
spec = np.loadtxt(dataFile)[:, 1]

### plot the spectrum before any handling
# spectrumPlot = plt.plot(wav,spec)
# plt.show()

### determine the step size
step = [b-a for a,b in zip(wav, wav[1:])]

### plot the step size against wavelength
# stepPlot = plt.plot(wav[1:],step)
# plt.show()

### interpolate the spectral data, upsample to 2048 points
newShape = np.linspace(min(wav),max(wav), 2048)
interp = np.interp(newShape, wav, spec)
# print(newShape)
# print(interp)

newStep = [b-a for a,b in zip(newShape, newShape[1:])]
# print(newStep)

### plot raw and interpolated step sizes against wavelength
stepPlot = plt.plot(wav[1:], step)
newSPlot = plt.plot(newShape[1:], newStep)
plt.show()

### plot raw and interpolated spectrum against wavelength
spectrumPlot = plt.plot(wav,spec,'o')
interpPlot = plt.plot(newShape,interp,'-')
plt.show()