import numpy as np
import matplotlib.pyplot as plt
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift

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

def remove_oscillatory_component(signal, dx, freq_min=None, freq_max=None, edge_k=3):
    n = len(signal)
    x = np.arange(n)

    # --- Remove DC offset ---
    offset = np.mean(signal)
    signal_zeroed = signal - offset

    # --- Subtract linear polynomial using edge_k points ---
    x_edges = np.concatenate([x[:edge_k], x[-edge_k:]])
    y_edges = np.concatenate([signal_zeroed[:edge_k], signal_zeroed[-edge_k:]])
    poly_coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit = np.polyval(poly_coeffs, x)
    signal_detrended = signal_zeroed - poly_fit

    # --- FFT ---
    freqs = fftfreq(n, d=dx)
    fft_vals = fft(signal_detrended)
    fft_vals_shifted = fftshift(fft_vals)
    freqs_shifted = fftshift(freqs)
    power_before = np.abs(fft_vals_shifted)**2

    # --- Filter unwanted frequency band ---
    keep_mask = np.ones_like(freqs_shifted, dtype=bool)
    if freq_min is not None and freq_max is not None:
        band_mask = (np.abs(freqs_shifted) >= freq_min) & (np.abs(freqs_shifted) <= freq_max)
        keep_mask[band_mask] = False

    fft_vals_shifted_filtered = fft_vals_shifted.copy()
    fft_vals_shifted_filtered[~keep_mask] = 0
    filtered_fft = ifftshift(fft_vals_shifted_filtered)
    filtered_signal = np.real(ifft(filtered_fft))
    power_after = np.abs(fft_vals_shifted_filtered)**2

    # --- Restore polynomial and offset ---
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

def plot_results(results, freq_min=None, freq_max=None, log_scale=False,
                 left_edge_freq=None, right_edge_freq=None,
                 cutoff_prominence_value=None):
    x = np.arange(len(results["original_signal"]))

    # --- Signal comparison ---
    plt.figure(figsize=(12, 4))
    plt.title("Original vs Filtered Signal")
    plt.plot(x, results["original_signal"], label='Original', alpha=0.5)
    plt.plot(x, results["restored_signal"], label='Filtered + Restored', linewidth=2)
    plt.legend()
    plt.xlabel("Index")
    plt.ylabel("Signal")
    plt.tight_layout()
    plt.show()

    # --- FFT Comparison ---
    plt.figure(figsize=(10, 4))
    plt.title("FFT Power Spectrum Before and After Filtering")

    if log_scale:
        plt.plot(results["freqs"], np.log10(results["power_before"] + 1e-12), label="Before Filtering", alpha=0.6)
        plt.plot(results["freqs"], np.log10(results["power_after"] + 1e-12), label="After Filtering", alpha=0.9)
        plt.ylabel("Log10 Power")
    else:
        plt.plot(results["freqs"], results["power_before"], label="Before Filtering", alpha=0.6)
        plt.plot(results["freqs"], results["power_after"], label="After Filtering", alpha=0.9)
        plt.ylabel("Power")

    if freq_min is not None and freq_max is not None:
        plt.axvspan(-freq_max, -freq_min, color='red', alpha=0.2, label="Removed Band")
        plt.axvspan(freq_min, freq_max, color='red', alpha=0.2)
        # Overlay detected peak edges
        plt.axvline(left_edge_freq, color='blue', linestyle='--', label='Detected Edge')
        plt.axvline(right_edge_freq, color='blue', linestyle='--')
        plt.axhline(cutoff_prominence_value, color='gray', linestyle='--', label='Prominence Threshold')
        zoom_margin = 2 * (freq_max - freq_min)
        plt.xlim(-freq_max - zoom_margin, freq_max + zoom_margin)
        plt.ylim(0, 1e8)

    plt.xlabel("Frequency")
    plt.legend()
    plt.tight_layout()
    plt.show()

# === USAGE EXAMPLE ===
filename = "/Users/hrubiak/Desktop/expt03_00226_ds.txt"  # Replace with your file
wavelengths, ds_data = read_spectral_data(filename)

lambda_min = wavelengths[0]
lambda_max = wavelengths[-1]
wavelength_range = lambda_max - lambda_min
# --- Compute sampling parameters ---
n = len(ds_data)
wavelength_range = lambda_max - lambda_min
dx = wavelength_range / n         # sampling step in nm
fs = 1.0 / dx                     # samples per nm


dx = np.mean(np.diff(wavelengths))  # wavelength spacing
freq_min = 0.06
freq_max = 0.17

results = remove_oscillatory_component(
    ds_data,
    dx=dx,
    freq_min=freq_min,
    freq_max=freq_max,
    edge_k=5
)

# === AUTO-DETECT PEAK FREQUENCY EDGES ===
from scipy.signal import find_peaks

freqs = results["freqs"]
power = results["power_before"]
center_idx = np.argmin(np.abs(freqs))  # index of 0 frequency

# Detect peaks with prominence above threshold
prominence_threshold = 0.01 * np.max(power)  # 10% of max power
peaks, properties = find_peaks(power, prominence=prominence_threshold)

# Store for plotting
cutoff_prominence_value = prominence_threshold

# Split peaks left and right of zero
left_peaks = [p for p in peaks if p < center_idx]
right_peaks = [p for p in peaks if p > center_idx]

left_edge_freq = freqs[max(left_peaks, key=lambda p: p) if left_peaks else center_idx]
right_edge_freq = freqs[min(right_peaks, key=lambda p: p) if right_peaks else center_idx]

print(f"Detected edge frequencies: {left_edge_freq:.4f}, {right_edge_freq:.4f}")
print(f"Prominence cutoff: {cutoff_prominence_value:.2e}")

plot_results(
    results,
    freq_min=freq_min,
    freq_max=freq_max,
    log_scale=False,
    left_edge_freq=left_edge_freq,
    right_edge_freq=right_edge_freq,
    cutoff_prominence_value=cutoff_prominence_value
)

