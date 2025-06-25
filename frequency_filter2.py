import numpy as np
import matplotlib.pyplot as plt
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift
from scipy.signal import find_peaks

def read_spectral_data(filename):
    wavelengths = []
    ds_data = []
    with open(filename, 'r') as f:
        for line in f:
            if line.strip().startswith('#') or not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) >= 2:
                wavelengths.append(float(parts[0]))
                ds_data.append(float(parts[1]))
    return np.array(wavelengths), np.array(ds_data)

def compute_fwhm(freqs, power, peak_idx):
    peak_power = power[peak_idx]
    half_max = peak_power / 2
    n = len(power)

    left_idx = peak_idx
    while left_idx > 0 and power[left_idx] > half_max:
        left_idx -= 1
    if left_idx == 0:
        f_left = freqs[0]
    else:
        f1, f2 = freqs[left_idx], freqs[left_idx + 1]
        p1, p2 = power[left_idx], power[left_idx + 1]
        f_left = f1 + (half_max - p1) * (f2 - f1) / (p2 - p1)

    right_idx = peak_idx
    while right_idx < n - 1 and power[right_idx] > half_max:
        right_idx += 1
    if right_idx == n - 1:
        f_right = freqs[-1]
    else:
        f1, f2 = freqs[right_idx - 1], freqs[right_idx]
        p1, p2 = power[right_idx - 1], power[right_idx]
        f_right = f1 + (half_max - p1) * (f2 - f1) / (p2 - p1)

    fwhm = abs(f_right - f_left)
    return fwhm, f_left, f_right

def remove_oscillatory_component(signal, dx, freq_min=None, freq_max=None, edge_k=3):
    n = len(signal)
    x = np.arange(n)

    offset = 0
    signal_zeroed = signal - offset

    x_edges = np.concatenate([x[:edge_k], x[-edge_k:]])
    y_edges = np.concatenate([signal_zeroed[:edge_k], signal_zeroed[-edge_k:]])
    poly_coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit = np.polyval(poly_coeffs, x)
    signal_detrended = signal_zeroed - poly_fit

    freqs = fftfreq(n, d=dx)
    fft_vals = fft(signal_detrended)
    fft_vals_shifted = fftshift(fft_vals)
    freqs_shifted = fftshift(freqs)
    power_before = np.abs(fft_vals_shifted)**2

    fft_vals_shifted_filtered = fft_vals_shifted.copy()
    keep_mask = np.ones_like(freqs_shifted, dtype=bool)
    if freq_min is not None and freq_max is not None:
        band_mask = (np.abs(freqs_shifted) >= freq_min) & (np.abs(freqs_shifted) <= freq_max)
        keep_mask[band_mask] = False
        fft_vals_shifted_filtered[~keep_mask] = 0

    filtered_fft = ifftshift(fft_vals_shifted_filtered)
    filtered_signal = np.real(ifft(filtered_fft))
    power_after = np.abs(fft_vals_shifted_filtered)**2

    restored_signal = filtered_signal + poly_fit + offset

    return {
        "original_signal": signal,
        "restored_signal": restored_signal,
        "filtered_signal": filtered_signal,
        "detrended_signal": signal_detrended,
        "dc_offset": offset,
        "poly_fit": poly_fit,
        "freqs": freqs_shifted,
        "power_before": power_before,
        "power_after": power_after
    }

def plot_fft_peaks(freqs, power, peaks, threshold, left_edge_freq=None, right_edge_freq=None):
    plt.figure(figsize=(10, 4))
    plt.title("FFT Power Spectrum (Before Filtering)")
    plt.plot(freqs, power, label="Power Spectrum")
    plt.plot(freqs[peaks], power[peaks], "x", label="Detected Peaks")
    plt.axhline(threshold, color="gray", linestyle="--", label="Prominence Threshold")

    if left_edge_freq is not None:
        plt.axvline(left_edge_freq, color='blue', linestyle='--', label="Left Edge")
    if right_edge_freq is not None:
        plt.axvline(right_edge_freq, color='blue', linestyle='--', label="Right Edge")

    if left_edge_freq is not None and right_edge_freq is not None:
        center = (left_edge_freq + right_edge_freq) / 2
        half_span = (right_edge_freq - left_edge_freq) / 2
        x_margin = 4 * half_span
        plt.xlim(center - x_margin, center + x_margin)
        mask = (freqs >= center - x_margin) & (freqs <= center + x_margin) & (np.abs(freqs) > 0.01)
        if np.any(mask):
            max_power = np.max(power[mask])
            plt.ylim(0, 2 * max_power)

    plt.xlabel("Frequency")
    plt.ylabel("Power")
    plt.legend()
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    filename = "/Users/hrubiak/Desktop/20250624-melt-devel_00115_ds.txt"
    wavelengths, ds_data = read_spectral_data(filename)

    lambda_min = wavelengths[0]
    lambda_max = wavelengths[-1]
    dx = (lambda_max - lambda_min) / (len(wavelengths) - 1)

    pad_width = 100
    ds_data_padded = np.concatenate([
        ds_data[pad_width - 1::-1],
        ds_data,
        ds_data[:-pad_width - 1:-1]
    ])

    n = len(ds_data)
    n_padded = len(ds_data_padded)
    idx_padded = np.arange(n_padded)

    idx_original = np.arange(pad_width, pad_width + n)
    x_edges = np.concatenate([idx_original[:3], idx_original[-3:]])
    y_edges = np.concatenate([
        ds_data_padded[x_edges[0]:x_edges[0]+3],
        ds_data_padded[x_edges[-3]:x_edges[-3]+3]
    ])
    poly_coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit = np.polyval(poly_coeffs, idx_padded)
    ds_data_detrended = ds_data_padded - poly_fit

    freqs = fftfreq(n_padded, d=dx)
    fft_vals = fft(ds_data_detrended)
    fft_vals_shifted = fftshift(fft_vals)
    freqs_shifted = fftshift(freqs)
    power = np.abs(fft_vals_shifted)**2

    center_idx = np.argmin(np.abs(freqs_shifted))
    prominence_threshold = 0.02 * np.max(power)
    peaks, _ = find_peaks(power, prominence=prominence_threshold)

    min_peak_freq = 0.08
    max_peak_freq = 0.5

    def in_freq_range(i):
        f = abs(freqs_shifted[i])
        return f >= min_peak_freq and (max_peak_freq is None or f <= max_peak_freq)

    left_peaks = [p for p in peaks if p < center_idx and in_freq_range(p)]
    right_peaks = [p for p in peaks if p > center_idx and in_freq_range(p)]

    left_peak_idx = max(left_peaks, key=lambda i: power[i]) if left_peaks else None
    right_peak_idx = max(right_peaks, key=lambda i: power[i]) if right_peaks else None

    left_edge_freq = freqs_shifted[left_peak_idx] if left_peak_idx is not None else None
    right_edge_freq = freqs_shifted[right_peak_idx] if right_peak_idx is not None else None

    if left_peak_idx is not None and right_peak_idx is not None:
        fwhm_l, _, _ = compute_fwhm(freqs_shifted, power, left_peak_idx)
        fwhm_r, _, _ = compute_fwhm(freqs_shifted, power, right_peak_idx)

        peaks_fre_ave = (abs(freqs_shifted[right_peak_idx]) + abs(freqs_shifted[left_peak_idx])) / 2
        fwhm_ave = (fwhm_r + fwhm_l) / 2

        freq_min = peaks_fre_ave - fwhm_ave * 7.5
        freq_max = peaks_fre_ave + fwhm_ave * 10

        results_full = remove_oscillatory_component(
            ds_data_padded,
            dx=dx,
            freq_min=freq_min,
            freq_max=freq_max,
            edge_k=3
        )

        start = pad_width
        end = pad_width + n
        results = {
            key: (
                results_full[key][start:end]
                if isinstance(results_full[key], np.ndarray) and results_full[key].shape == results_full["original_signal"].shape
                else results_full[key]
            )
            for key in results_full
        }

        plot_fft_peaks(
            freqs_shifted,
            power,
            peaks,
            threshold=prominence_threshold,
            left_edge_freq=left_edge_freq,
            right_edge_freq=right_edge_freq
        )

        plt.figure(figsize=(12, 4))
        plt.title("Original vs Filtered Signal")
        plt.plot(np.arange(len(results["original_signal"])), results["original_signal"], label='Original', alpha=0.5)
        plt.plot(np.arange(len(results["restored_signal"])), results["restored_signal"], label='Filtered + Restored', linewidth=2)
        plt.legend()
        plt.xlabel("Index")
        plt.ylabel("Signal")
        plt.tight_layout()
        plt.show()