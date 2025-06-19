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
    """
    Estimate the Full Width at Half Maximum (FWHM) for a peak in the FFT power spectrum.
    Uses linear interpolation between points to locate the half-maximum crossings.
    
    Parameters:
        freqs : np.ndarray
            FFT frequency array (shifted).
        power : np.ndarray
            FFT power spectrum (shifted).
        peak_idx : int
            Index of the peak in the power array.

    Returns:
        fwhm : float
            Full width at half maximum in frequency units.
        f_left : float
            Left crossing frequency.
        f_right : float
            Right crossing frequency.
    """
    peak_power = power[peak_idx]
    half_max = peak_power / 2
    n = len(power)

    # Search left of peak
    left_idx = peak_idx
    while left_idx > 0 and power[left_idx] > half_max:
        left_idx -= 1
    if left_idx == 0:
        f_left = freqs[0]
    else:
        # Linear interpolation
        f1, f2 = freqs[left_idx], freqs[left_idx + 1]
        p1, p2 = power[left_idx], power[left_idx + 1]
        f_left = f1 + (half_max - p1) * (f2 - f1) / (p2 - p1)

    # Search right of peak
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

    # Remove DC offset
    offset = 0 # np.mean(signal)
    signal_zeroed = signal - offset

    # Subtract linear trend using edge_k points at both ends
    x_edges = np.concatenate([x[:edge_k], x[-edge_k:]])
    y_edges = np.concatenate([signal_zeroed[:edge_k], signal_zeroed[-edge_k:]])
    poly_coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit = np.polyval(poly_coeffs, x)
    signal_detrended = signal_zeroed - poly_fit

    # FFT
    freqs = fftfreq(n, d=dx)
    fft_vals = fft(signal_detrended)
    fft_vals_shifted = fftshift(fft_vals)
    freqs_shifted = fftshift(freqs)
    power_before = np.abs(fft_vals_shifted)**2

    # Band-stop filter
    fft_vals_shifted_filtered = fft_vals_shifted.copy()
    keep_mask = np.ones_like(freqs_shifted, dtype=bool)
    if freq_min is not None and freq_max is not None:
        band_mask = (np.abs(freqs_shifted) >= freq_min) & (np.abs(freqs_shifted) <= freq_max)
        keep_mask[band_mask] = False
        fft_vals_shifted_filtered[~keep_mask] = 0

    filtered_fft = ifftshift(fft_vals_shifted_filtered)
    filtered_signal = np.real(ifft(filtered_fft))
    power_after = np.abs(fft_vals_shifted_filtered)**2

    # Restore polynomial and offset
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

    # Zoom around detected side peaks
    if left_edge_freq is not None and right_edge_freq is not None:
        center = (left_edge_freq + right_edge_freq) / 2
        half_span = (right_edge_freq - left_edge_freq) / 2
        x_margin = 2 * half_span
        plt.xlim(center - x_margin, center + x_margin)

        # Y-limits without central peak influence
        mask = (freqs >= center - x_margin) & (freqs <= center + x_margin) & (np.abs(freqs) > 0.01)
        if np.any(mask):
            max_power = np.max(power[mask])
            plt.ylim(0, 2 * max_power)

    plt.xlabel("Frequency")
    plt.ylabel("Power")
    plt.legend()
    plt.tight_layout()
    plt.show()

def plot_results(results, freq_min=None, freq_max=None, log_scale=False,
                 left_edge_freq=None, right_edge_freq=None,
                 cutoff_prominence_value=None):
    x = np.arange(len(results["original_signal"]))

    # Signal plot
    plt.figure(figsize=(12, 4))
    plt.title("Original vs Filtered Signal")
    plt.plot(x, results["original_signal"], label='Original', alpha=0.5)
    plt.plot(x, results["restored_signal"], label='Filtered + Restored', linewidth=2)
    plt.legend()
    plt.xlabel("Index")
    plt.ylabel("Signal")
    plt.tight_layout()
    plt.show()

        # FFT comparison after filtering
    plt.figure(figsize=(10, 4))
    plt.title("FFT Power Spectrum After Filtering")

    if log_scale:
        power_before = np.log10(results["power_before"] + 1e-12)
        power_after = np.log10(results["power_after"] + 1e-12)
    else:
        power_before = results["power_before"]
        power_after = results["power_after"]

    freqs = results["freqs"]
    plt.plot(freqs, power_before, label="Before", alpha=0.6)
    plt.plot(freqs, power_after, label="After", alpha=0.9)

    if freq_min and freq_max:
        center = (freq_min + freq_max) / 2
        half_span = (freq_max - freq_min) / 2
        x_margin = 2 * half_span
        plt.axvspan(-freq_max, -freq_min, color='red', alpha=0.2, label="Removed Band")
        plt.axvspan(freq_min, freq_max, color='red', alpha=0.2)
        plt.xlim(center - x_margin, center + x_margin)

        # Exclude central peak when computing y-limits
        mask = (freqs >= center - x_margin) & (freqs <= center + x_margin) & (np.abs(freqs) > 0.01)
        if np.any(mask):
            max_power = np.max(results["power_before"][mask])
            plt.ylim(0, 2 * max_power)

    plt.xlabel("Frequency")
    plt.ylabel("Log10 Power" if log_scale else "Power")
    plt.legend()
    plt.tight_layout()
    plt.show()

# === MAIN TEST CODE ===
if __name__ == "__main__":
    filename = "/Users/hrubiak/Desktop/expt03_00226_ds.txt"  # Replace with your file
    wavelengths, ds_data = read_spectral_data(filename)

    # Compute dx from wavelength range
    lambda_min = wavelengths[0]
    lambda_max = wavelengths[-1]
    dx = (lambda_max - lambda_min) / (len(wavelengths) - 1)

    # === First pass: FFT only (no filtering) ===
    results = remove_oscillatory_component(
        ds_data,
        dx=dx,
        freq_min=None,
        freq_max=None,
        edge_k=2
    )

    # === Detect FFT peaks ===
    freqs = results["freqs"]
    power = results["power_before"]
    center_idx = np.argmin(np.abs(freqs))
    prominence_threshold = 0.01 * np.max(power)
    peaks, properties = find_peaks(power, prominence=prominence_threshold)
    left_peaks = [p for p in peaks if p < center_idx]
    right_peaks = [p for p in peaks if p > center_idx]
    left_edge_freq = freqs[max(left_peaks, key=lambda p: p)] if left_peaks else None
    right_edge_freq = freqs[min(right_peaks, key=lambda p: p)] if right_peaks else None

    if right_peaks:
        right_peak_idx = min(right_peaks, key=lambda i: abs(freqs[i]))
        fwhm_r, f_left_r, f_right_r = compute_fwhm(freqs, power, right_peak_idx)
        print(f"Right peak at {freqs[right_peak_idx]:.4f} Hz, FWHM: {fwhm_r:.5f}")

    if left_peaks:
        left_peak_idx = max(left_peaks, key=lambda i: abs(freqs[i]))
        fwhm_l, f_left_l, f_right_l = compute_fwhm(freqs, power, left_peak_idx)
        print(f"Left peak at {freqs[left_peak_idx]:.4f} Hz, FWHM: {fwhm_l:.5f}")

    print(f"Detected edge frequencies: {left_edge_freq:.4f}, {right_edge_freq:.4f}")
    print(f"Prominence cutoff: {prominence_threshold:.2e}")

    # === Plot detected FFT peaks (before filtering) ===
    plot_fft_peaks(
        freqs,
        power,
        peaks,
        threshold=prominence_threshold,
        left_edge_freq=left_edge_freq,
        right_edge_freq=right_edge_freq
    )

    # === Second pass: Apply filtering using manual band ===

    peaks_fre_ave = (abs(freqs[right_peak_idx]) + abs(freqs[left_peak_idx])) / 2
    fwhm_ave = (fwhm_r + fwhm_l) / 2
    cut_range = fwhm_ave * 4
    freq_min = peaks_fre_ave - fwhm_ave * 2.5
    freq_max = peaks_fre_ave + fwhm_ave * 10
    #freq_min = 0.06
    #freq_max = 0.17

    results = remove_oscillatory_component(
        ds_data,
        dx=dx,
        freq_min=freq_min,
        freq_max=freq_max,
        edge_k=3
    )

    # === Plot final filtered result ===
    plot_results(
        results,
        freq_min=freq_min,
        freq_max=freq_max,
        log_scale=False,
        left_edge_freq=left_edge_freq,
        right_edge_freq=right_edge_freq,
        cutoff_prominence_value=prominence_threshold
    )