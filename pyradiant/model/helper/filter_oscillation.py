import numpy as np
import matplotlib.pyplot as plt
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift
from scipy.signal import find_peaks



def compute_fwhm(freqs, power, peak_idx):
    peak_power = power[peak_idx]
    half_max = peak_power / 2
    left_idx = peak_idx
    while left_idx > 0 and power[left_idx] > half_max:
        left_idx -= 1
    right_idx = peak_idx
    while right_idx < len(power) - 1 and power[right_idx] > half_max:
        right_idx += 1
    f_left = np.interp(half_max, [power[left_idx], power[left_idx + 1]], [freqs[left_idx], freqs[left_idx + 1]])
    f_right = np.interp(half_max, [power[right_idx - 1], power[right_idx]], [freqs[right_idx - 1], freqs[right_idx]])
    return abs(f_right - f_left)

def filter_oscillatory_component(
    x,
    y,
    edge_k=3,
    pad_width=20,
    prominence_ratio=0.005,
    cut_scale_left=2.5,
    cut_scale_right=3.0,
    fwhm_expand_factor=1.0,
    min_peak_freq=0.08,
    max_peak_freq=0.5
):
    """
    Removes oscillatory components from a signal using FFT and auto-detected side peaks.
    Applies filtering on a reflection-padded signal and trims the result.

    Parameters:
        ...
        min_peak_freq : float
            Minimum absolute frequency to consider when detecting FFT peaks.
        max_peak_freq : float or None
            Maximum absolute frequency to consider when detecting FFT peaks. If None, no upper limit.

    Returns:
        y_filtered : np.ndarray
            Filtered signal, same shape as input.
    """
    n = len(y)
    dx = (x[-1] - x[0]) / (n - 1)

    y_padded = np.concatenate([
        y[pad_width - 1::-1],
        y,
        y[:-pad_width - 1:-1]
    ])
    n_padded = len(y_padded)
    idx_padded = np.arange(n_padded)

    idx_original = np.arange(pad_width, pad_width + n)
    x_edges = np.concatenate([idx_original[:edge_k], idx_original[-edge_k:]])
    y_edges = np.concatenate([
        y_padded[x_edges[0]:x_edges[0]+edge_k],
        y_padded[x_edges[-edge_k]:x_edges[-edge_k]+edge_k]
    ])
    poly_coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit_full = np.polyval(poly_coeffs, idx_padded)
    y_detrended = y_padded - poly_fit_full

    freqs = fftfreq(n_padded, d=dx)
    fft_vals = fft(y_detrended)
    fft_vals_shifted = fftshift(fft_vals)
    freqs_shifted = fftshift(freqs)
    power = np.abs(fft_vals_shifted)**2
    center_idx = np.argmin(np.abs(freqs_shifted))

    # Peak detection with frequency limits
    peaks, _ = find_peaks(power, prominence=prominence_ratio * np.max(power))
    def in_freq_range(i):
        f = abs(freqs_shifted[i])
        return f >= min_peak_freq and (max_peak_freq is None or f <= max_peak_freq)

    left_peaks = [i for i in peaks if i < center_idx and in_freq_range(i)]
    right_peaks = [i for i in peaks if i > center_idx and in_freq_range(i)]

    if not left_peaks or not right_peaks:
        freq_min = 0.07
        freq_max = 0.17
    else:

        l_idx = max(left_peaks, key=lambda i: abs(freqs_shifted[i]))
        r_idx = min(right_peaks, key=lambda i: abs(freqs_shifted[i]))
        fwhm_l = compute_fwhm(freqs_shifted, power, l_idx)
        fwhm_r = compute_fwhm(freqs_shifted, power, r_idx)
        fwhm = 0.5 * (fwhm_l + fwhm_r) * fwhm_expand_factor

        peak_freq = 0.5 * (abs(freqs_shifted[l_idx]) + abs(freqs_shifted[r_idx]))
        freq_min = peak_freq - cut_scale_left * fwhm
        freq_max = peak_freq + cut_scale_right * fwhm

    #print(f'freq_min {freq_min}, freq_max {freq_max}')

    band_mask = (np.abs(freqs_shifted) >= freq_min) & (np.abs(freqs_shifted) <= freq_max)
    fft_vals_filtered = fft_vals_shifted.copy()
    fft_vals_filtered[band_mask] = 0
    filtered_fft = ifftshift(fft_vals_filtered)
    y_filtered_padded = np.real(ifft(filtered_fft)) + poly_fit_full

    return y_filtered_padded[pad_width:pad_width + n]