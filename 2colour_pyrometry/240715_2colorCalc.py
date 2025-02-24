__author__ = "Dean Smith"

"""
Takes reduced and fitted data from T-View and processes "two-colour" pyrometry on it
Input is a txt file from T-View
Output is temperature as a function of spectral wavelength per Wien approximation, and a histogram of Wien temperatures across the entire spectrum

changelog
240715 — created

"""

# import sys
import os.path
import time
import numpy as np
from scipy.optimize import curve_fit
import matplotlib.pyplot as plt

#filePath = 'C:/Software/pythonData/240715_Tview/'

#fileName = input('Which file:')
#dataFile = filePath+fileName+'.txt'
dataFile = '/Users/hrubiak/GitHub/PyRadiant/2colour_pyrometry/tempData/upstream.txt'            

# wav = np.loadtxt(dataFile)[:, 0]
spec = np.loadtxt(dataFile, usecols=(0,1))

# plot the spectrum before any handling
# spectrumPlot = plt.plot(spec)
# plt.show()

# print(wav)
# print(spec)
# print(spec.shape)

# define constants
h = 6.626e-34
c = 3.0e8
kB = 1.38e-23
# define specing parameter in units of ROWS
delta = 150

i = 500
j = i+1

specSlice1 = spec[i:i+1]
specSlice2 = spec[i+delta:i+1+delta]

# wave1 = specSlice1[0, 0] * 1e-9
# wave2 = specSlice2[0, 0] * 1e-9

# int1 = specSlice1[0,1]
# int2 = specSlice2[0,1]

# wave1 = spec[i:i+1][0,0]*1e-9
# wave2 = spec[i+delta:i+1+delta][0,0]*1e-9

# int1 = spec[i:i+1][0,1]
# int2 = spec[i+delta:i+1+delta][0,1]

# print(wave1, int1)
# print(wave2, int2)

# thet1 = - (kB/(h*c)) * np.log(int1*((wave1**5)/(2*h*(c**2))))
# thet2 = - (kB/(h*c)) * np.log(int2*((wave2**5)/(2*h*(c**2))))

# temp = ((1/wave2)-(1/wave1))/(thet2-thet1)

# print(temp)

def twoWien(spec):
    h = 6.626e-34   #planck
    c = 3.0e8       #speed of light
    kB = 1.38e-23   #boltzmann
    delta = 150     #spacing parameter in units of ROWS
    lam1 = [spec[i:i+1][0,0]*1e-9 for i in spec]
    lam2 = [spec[j+delta:j+1+delta][0,0]*1e-9 for j in spec]
    int1 = [spec[i:i+1][0,1] for i in spec]
    int2 = [spec[j+delta:j+1+delta][0,1] for j in spec]
    thet1 = [- (kB/(h*c)) * np.log(int1*((l1**5)/(2*h*(c**2)))) for l1 in lam1]
    thet2 = [- (kB/(h*c)) * np.log(int2*((l2**5)/(2*h*(c**2)))) for l2 in lam2]
    temp = ((1/lam2)-(1/lam1))/(thet2-thet1)
    return temp

twoColTemp = [twoWien(i) for i in spec]

