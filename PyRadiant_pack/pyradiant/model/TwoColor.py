# -*- coding: utf-8 -*-
"""
Created on Tue Jan  7 15:43:34 2025

@author: deansmith
"""

### This script opens a temperature data file, extracts and interpolates the
### data, and then attemps to generate a 2-color pyrometry plot using that data
### The math is done piecemeal, and can hopefully be cleaned up later to make
### a more efficient version of the code

import time
import numpy as np
#import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter1d

### Define constants
h = 6.626e-34   #planck
c = 3.0e8       #speed of light
kB = 1.38e-23   #boltzmann

def calculate_2_color(rawWav, rawSpec):

    ### interpolate the data
    intWav = np.linspace(min(rawWav), max(rawWav), 1024)    # make a new wavelength axis
    intSpec = np.interp(intWav, rawWav, rawSpec)            # make a new intensity axis
    intStep = [b-a for a,b in zip(intWav, intWav[1:])]      # calculate step size of interpolated data
    # print(intStep)
    intWav_m = intWav * 1e-9                                  # convert to metres


    ### use numpy arrays to calculate temperature from the data
    delta = 150                                         # delta in units of rows
    lam1 = np.array([i for i in intWav_m[:-delta]])     # range of lower wavelengths
    lam2 = np.array([i for i in intWav_m[delta:]])      # range of higher wavelengths
    int1 = np.array([i for i in intSpec[:-delta]])      # intensity data at lower wavelengths
    int2 = np.array([i for i in intSpec[delta:]])       # intensity data at higher wavelengths
    thet1 = - (kB/(h*c)) * np.log(int1*((lam1**5)/(2*h*(c**2))))    # theta1 term
    thet2 = - (kB/(h*c)) * np.log(int2*((lam2**5)/(2*h*(c**2))))    # theta2 term
    temp = ((1/lam2)-(1/lam1))/(thet2-thet1)            # temperature from Wien

    return lam1* 1e9, temp