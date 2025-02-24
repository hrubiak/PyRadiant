# -*- coding: utf-8 -*-
"""
Created on Tue Jan  7 15:43:34 2025

@author: deansmith
"""

### This script opens a temperature data file, extracts and interpolates the
### data, and then attemps to generate a 2-color pyrometry plot using that data
### The math is done piecemeal, and can hopefully be cleaned up later to make
### a more efficient version of the code

import sys
import os.path
import time
import numpy as np
import matplotlib.pyplot as plt

### Define constants
h = 6.626e-34   #planck
c = 3.0e8       #speed of light
kB = 1.38e-23   #boltzmann

### Opening the data
filePath = '/Users/hrubiak/GitHub/PyRadiant/2colour_pyrometry/tempData/'   # Specify where the files live
fileName = '2colour_pyrometry/tempData/downstream.txt' #input('Which file:')    # Ask the user which filed to open
dataFile = '/Users/hrubiak/GitHub/PyRadiant/2colour_pyrometry/tempData/upstream.txt' 

rawWav = np.loadtxt(dataFile)[:, 0]                 # make ndarray of raw wavelength data
rawSpec = np.loadtxt(dataFile)[:, 1]                # make ndarray of raw intensity data

### plot the raw spectrum before any handling
# rawPlot = plt.plot(rawWav,rawSpec)
# plt.show()

### interpolate the data
intWav = np.linspace(min(rawWav), max(rawWav), 1024)    # make a new wavelength axis
intSpec = np.interp(intWav, rawWav, rawSpec)            # make a new intensity axis
intStep = [b-a for a,b in zip(intWav, intWav[1:])]      # calculate step size of interpolated data
# print(intStep)
intWav_m = intWav * 1e-9                                  # convert to metres

### plot the interpolated spectrum
intPlot = plt.plot(intWav, intSpec)
plt.show()

### use numpy arrays to calculate temperature from the data
delta = 150                                         # delta in units of rows
lam1 = np.array([i for i in intWav_m[:-delta]])     # range of lower wavelengths
lam2 = np.array([i for i in intWav_m[delta:]])      # range of higher wavelengths
int1 = np.array([i for i in intSpec[:-delta]])      # intensity data at lower wavelengths
int2 = np.array([i for i in intSpec[delta:]])       # intensity data at higher wavelengths
thet1 = - (kB/(h*c)) * np.log(int1*((lam1**5)/(2*h*(c**2))))    # theta1 term
thet2 = - (kB/(h*c)) * np.log(int2*((lam2**5)/(2*h*(c**2))))    # theta2 term
temp = ((1/lam2)-(1/lam1))/(thet2-thet1)            # temperature from Wien

### plotting the temperature as a function of wavelength
tempPlot = plt.plot(lam1,temp)
plt.xlabel('Wavelength (m)')
plt.ylabel('Wien Temperature (K)')
plt.show()
### making a temperature histogram
tempHist = plt.hist(temp, bins=50)
plt.xlabel('Temperature (K)')
plt.ylabel('Count')
plt.show()

### showing the size of the Wien window in units of nanometers
delta_m = delta*intStep[1]
print(delta_m)