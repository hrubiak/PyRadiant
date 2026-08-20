import numpy as np

from scipy import stats

def planck_radiation(wavelengths, temperature):
    """
    Calculate the blackbody radiation intensity as a function of wavelength and temperature.

    Parameters:
    - wavelengths: Array of wavelengths (in meters) at which to calculate the radiation.
    - temperature: Temperature of the blackbody in Kelvin.

    Returns:
    - intensity: Array of radiation intensities corresponding to the given wavelengths.
    """

    # Planck's constant (Joule seconds)
    h = 6.62607015e-34

    # Speed of light (meters per second)
    c = 299792458.0

    # Boltzmann constant (Joules per Kelvin)
    k = 1.380649e-23

    # Calculate the normalization constant for the Planck equation
    norm = 2.0 * h * c ** 2

    # Calculate the radiation intensity for each wavelength
    intensity = norm / (wavelengths ** 5 * (np.exp((h * c) / (wavelengths * k * temperature)) - 1))

    return intensity

def wien_approximation(wavelength, temperature_K):
    
    # Boltzmann constant
    k = 1.380649e-23  # J/K
    
    # Planck's constant
    h = 6.62607015e-34  # J·s
    
    # Speed of light
    c = 299792458  # m/s
    
    # Calculate spectral radiance using the Wien approximation
    spectral_radiance = (2  * h * c**2) / (wavelength**5) * np.exp(-h * c / (wavelength * k * temperature_K))
    
    return spectral_radiance

def wien_pre_transform(wavelength_m, radiance):
    x = 0.0143878 / wavelength_m
    y = 36.6666 + (np.log(wavelength_m)*5+ np.log(radiance))
    return x, y

def inverse_wien_pre_transform(wavelength_m, y):
    
    radiance = np.exp(y - 36.6666 - np.log(wavelength_m)*5)
    return radiance

def fit_linear(x, y, compute_eror = True):
    X = np.vstack([x, np.ones(len(x))]).T
    Y = np.array(y)
    m, b = np.linalg.lstsq(X, Y, rcond=None)[0]

    if not compute_eror:
        return m, b, 0
    else:
        # Calculate the predicted Y values
        Y_pred = m * x + b

        # Calculate the residuals
        residuals = Y - Y_pred
        # Calculate the sum of squared residuals
        ssr = np.sum(residuals**2)

        # Calculate the standard error of the regression slope (m)
        n = len(X)  # Number of data points
        std_error_m = np.sqrt(ssr / (n - 2)) / np.sqrt(np.sum((X - np.mean(X))**2))

        # Assuming a 95% confidence interval (two-tailed)
        confidence_level = 0.95
        t_critical = np.abs(stats.t.ppf((1 - (1 - confidence_level) / 2), n - 2))

        # Calculate the standard deviation of m
        std_deviation_m = std_error_m * t_critical

        return m, b, std_deviation_m

def m_b_wien(wavelength_m, m, b):
    wavelength_start = np.amin(wavelength_m)
    wavelength_end = np.amax(wavelength_m)
    wavelength = np.linspace(wavelength_start, wavelength_end, 50)
    x = 0.0143878 / wavelength
    y = m * x + b
    radiance = inverse_wien_pre_transform(wavelength,y)
    return wavelength, radiance

def m_to_T(m, m_std_dev):
    T = 1./(-1. * m )

    T_std_dev = abs(1 / (m**2)) * m_std_dev
    return T, T_std_dev