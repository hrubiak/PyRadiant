import numpy as np
import matplotlib.pyplot as plt
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift
from scipy.signal import find_peaks
from scipy.interpolate import interp1d

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

import numpy as np
from scipy.fft import fft, fftfreq, fftshift

import numpy as np
from scipy.fft import fft, fftfreq, fftshift

import numpy as np
from scipy.fft import fft, fftfreq, fftshift

def compute_fft_and_detrend(signal, dx, edge_k=10, pad_width=20):
    """
    Compute FFT of a detrended and reflection-padded signal.

    Parameters:
        signal : np.ndarray
            Input 1D signal.
        dx : float
            Sample spacing.
        edge_k : int
            Number of edge points to use for detrending.
        pad_width : int
            Number of points to reflectively pad on each side.

    Returns:
        dict containing:
            - original_signal
            - detrended_signal
            - poly_fit
            - dc_offset
            - freqs
            - fft_vals
            - power_before
            - pad_width
            - padded_length
            - x_padded
            - x
            - signal_padded
    """
    n = len(signal)
    x = np.arange(n) * dx

    # Remove DC offset (optional)
    offset = 0  # or np.mean(signal)
    signal_zeroed = signal - offset

    # Detrend using linear fit at both ends (before padding)
    x_edges = np.concatenate([x[:edge_k], x[-edge_k:]])
    y_edges = np.concatenate([signal_zeroed[:edge_k], signal_zeroed[-edge_k:]])
    poly_coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit = np.polyval(poly_coeffs, x)
    signal_detrended = signal_zeroed - poly_fit

    # Pad the detrended signal
    signal_padded = np.concatenate([
        signal_detrended[pad_width - 1::-1],
        signal_detrended,
        signal_detrended[:-pad_width - 1:-1]
    ])
    n_padded = len(signal_padded)
    x_padded = np.arange(-pad_width, n + pad_width) * dx

    # Pad the polynomial fit to match padded signal
    poly_fit_padded = np.concatenate([
        poly_fit[pad_width - 1::-1],
        poly_fit,
        poly_fit[:-pad_width - 1:-1]
    ])

    # FFT on detrended, padded signal
    freqs = fftfreq(n_padded, d=dx)
    fft_vals = fft(signal_padded)
    fft_vals_shifted = fftshift(fft_vals)
    freqs_shifted = fftshift(freqs)
    power = np.abs(fft_vals_shifted) ** 2

    return {
        "original_signal": signal,
        "detrended_signal": signal_padded,
        "poly_fit": poly_fit_padded,
        "dc_offset": offset,
        "freqs": freqs_shifted,
        "fft_vals": fft_vals_shifted,
        "power_before": power,
        "pad_width": pad_width,
        "padded_length": n_padded,
        "x_padded": x_padded,
        "x": x,
        "signal_padded": signal_padded
    }


def plot_detrended_signal(fft_result, title_prefix=""):
    """
    Plot the original padded signal and the detrended version.

    Parameters:
        fft_result : dict
            Output from compute_fft_and_detrend.
        title_prefix : str
            Optional prefix to prepend to each subplot title.
    """
    x = fft_result["x"]
    y_orig = fft_result["original_signal"]
    y_padded = fft_result["signal_padded"]
    poly_fit = fft_result["poly_fit"]
    y_detrended = fft_result["detrended_signal"]
    x_padded = fft_result["x_padded"]
    fig, axs = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

    axs[0].plot(x_padded, y_padded, label="Original (padded)")
    axs[0].plot(x_padded, poly_fit, label="Linear Trend", linestyle="--")
    axs[0].set_title(f"{title_prefix}Original Signal with Linear Trend")
    axs[0].legend()
    axs[0].grid(True)

    
    axs[1].plot(x_padded, y_detrended, label="Detrended Signal", color="orange")
    axs[1].set_title(f"{title_prefix}Detrended Signal (Padded)")
    axs[1].legend()
    axs[1].grid(True)

    axs[1].set_xlabel("Index or Time (x)")
    plt.tight_layout()
    plt.show()

from scipy.fft import ifft, ifftshift
import numpy as np

def apply_fft_filter(fft_result, freq_min, freq_max):
    """
    Apply band-stop filtering on the FFT result by replacing FFT values in the masked region
    with the average of the edge values just outside the band. Then de-pad the signal.

    Parameters:
        fft_result : dict
            Output from compute_fft_and_detrend.
        freq_min : float
            Minimum frequency of band to remove.
        freq_max : float
            Maximum frequency of band to remove.

    Returns:
        dict with both padded and de-padded filtered results and power spectra.
    """
    fft_vals_shifted = fft_result["fft_vals"].copy()
    freqs_shifted = fft_result["freqs"]

    # Identify band to replace
    band_mask = (np.abs(freqs_shifted) >= freq_min) & (np.abs(freqs_shifted) <= freq_max)

    # Get indices of the band
    band_indices = np.where(band_mask)[0]
    if len(band_indices) == 0:
        print("Warning: No frequencies matched the filtering band.")
        return fft_result

    i_start, i_end = band_indices[0], band_indices[-1]

    # Find first and last indices outside the band
    i_before = i_start - 1 if i_start > 0 else i_start
    i_after = i_end + 1 if i_end + 1 < len(fft_vals_shifted) else i_end

    val_before = fft_vals_shifted[i_before]
    val_after = fft_vals_shifted[i_after]
    replacement_val = 0.5 * (val_before + val_after)

    # Fill band with average value
    fft_vals_shifted[band_mask] = replacement_val

    # Inverse FFT
    filtered_fft = ifftshift(fft_vals_shifted)
    filtered_signal = np.real(ifft(filtered_fft))

    # Restore trend and DC offset
    restored_signal = filtered_signal + fft_result["poly_fit"] + fft_result["dc_offset"]
    power_after = np.abs(fft_vals_shifted) ** 2

    # === Trim padding ===
    pad_width = fft_result.get("pad_width", 0)
    n_original = len(fft_result["original_signal"])

    start = pad_width
    end = pad_width + n_original

    restored_signal_trimmed = restored_signal[start:end]
    filtered_signal_trimmed = filtered_signal[start:end]
    x_trimmed = fft_result["x_padded"][start:end]

    

    return {
        "original_signal": fft_result["original_signal"],
        "restored_signal": restored_signal,
        "filtered_signal": filtered_signal,
        "power_after": power_after,
        "power_before": fft_result["power_before"],
        "freqs": fft_result["freqs"],
        "x_padded": fft_result["x_padded"],
        "restored_signal_trimmed": restored_signal_trimmed,
        "filtered_signal_trimmed": filtered_signal_trimmed,
        "x_trimmed": x_trimmed,
     

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
        x_margin = 10 * half_span
        #plt.xlim(center - x_margin, center + x_margin)

        # Y-limits without central peak influence
        mask = (freqs >= center - x_margin) & (freqs <= center + x_margin) & (np.abs(freqs) > 0.01)
        if np.any(mask):
            max_power = np.max(power[mask])
            #plt.ylim(0, 500 * max_power)

    plt.xlabel("Frequency")
    plt.ylabel("Power")
    plt.legend()
    plt.tight_layout()
    plt.show()

def plot_results(results, freq_min=None, freq_max=None, log_scale=False,
                 left_edge_freq=None, right_edge_freq=None,
                 cutoff_prominence_value=None):
    #x = np.arange(len(results["original_signal"]))
    x = results['x_trimmed']
    x_padded = results['x_padded']
    
    # Signal plot
    plt.figure(figsize=(12, 4))
    plt.title("Original vs Filtered Signal")
    plt.plot(x, results["original_signal"], label='Original', alpha=0.5)
    plt.plot(x_padded, results["restored_signal"], label='Filtered + Restored', linewidth=2)
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
        plt.xlim(-1*(center + x_margin), center + x_margin)

        # Exclude central peak when computing y-limits
        mask = (freqs >= center - x_margin) & (freqs <= center + x_margin) & (np.abs(freqs) > 0.01)
        if np.any(mask):
            max_power = np.max(results["power_before"][mask])
            plt.ylim(0, 200 * max_power)

    plt.xlabel("Frequency")
    plt.ylabel("Log10 Power" if log_scale else "Power")
    plt.legend()
    plt.tight_layout()
    plt.show()


def convert_nm_to_cm_inv(x_nm, y_counts, num_points=None):
    """
    Convert spectral data from wavelength (nm) to wavenumber (cm⁻¹).
    """
    x_nm = np.asarray(x_nm)
    y_counts = np.asarray(y_counts)

    x_cm_inv = 1e7 / x_nm
    sort_idx = np.argsort(x_cm_inv)
    x_sorted = x_cm_inv[sort_idx]
    y_sorted = y_counts[sort_idx]

    if num_points is None:
        num_points = len(x_sorted)
    wavenumber_grid = np.linspace(x_sorted.min(), x_sorted.max(), num_points)

    interp_func = interp1d(x_sorted, y_sorted, kind='linear', bounds_error=False, fill_value=0)
    y_interp = interp_func(wavenumber_grid)

    return wavenumber_grid, y_interp


def convert_cm_inv_to_nm(x_cm_inv, y_counts, num_points=None):
    """
    Convert spectral data from wavenumber (cm⁻¹) to wavelength (nm).
    """
    x_cm_inv = np.asarray(x_cm_inv)
    y_counts = np.asarray(y_counts)

    x_nm = 1e7 / x_cm_inv
    sort_idx = np.argsort(x_nm)
    x_sorted = x_nm[sort_idx]
    y_sorted = y_counts[sort_idx]

    if num_points is None:
        num_points = len(x_sorted)
    wavelength_grid = np.linspace(x_sorted.min(), x_sorted.max(), num_points)

    interp_func = interp1d(x_sorted, y_sorted, kind='linear', bounds_error=False, fill_value=0)
    y_interp = interp_func(wavelength_grid)

    return wavelength_grid, y_interp

from scipy.signal import find_peaks

def detect_fft_peaks(freqs, power, prominence_ratio=0.05, freq_min=0.0, freq_max=np.inf):
    """
    Detect left and right FFT peaks within a specified frequency range.

    Parameters:
        freqs : np.ndarray
            Frequency array (already shifted).
        power : np.ndarray
            Power spectrum (same shape as freqs, already shifted).
        prominence_ratio : float
            Minimum prominence ratio for peak detection (relative to max power).
        freq_min : float
            Minimum absolute frequency to consider when selecting peaks.
        freq_max : float
            Maximum absolute frequency to consider when selecting peaks.

    Returns:
        dict with:
            - left_peak_idx, right_peak_idx : int or None
            - fwhm_l, fwhm_r : float or None
            - left_edge_freq, right_edge_freq : float or None
            - prominence_threshold : float
            - peaks : list of all peaks found
    """
    center_idx = np.argmin(np.abs(freqs))
    prominence_threshold = prominence_ratio * np.max(power)

    peaks, properties = find_peaks(power, prominence=prominence_threshold)

    def in_range(i):
        return freq_min <= abs(freqs[i]) <= freq_max

    left_peaks = [i for i in peaks if i < center_idx and in_range(i)]
    right_peaks = [i for i in peaks if i > center_idx and in_range(i)]

    result = {
        "left_peak_idx": None,
        "right_peak_idx": None,
        "fwhm_l": None,
        "fwhm_r": None,
        "left_edge_freq": None,
        "right_edge_freq": None,
        "prominence_threshold": prominence_threshold,
        "peaks": peaks
    }

    if left_peaks:
        idx = max(left_peaks, key=lambda i: abs(freqs[i]))
        result["left_peak_idx"] = idx
        result["left_edge_freq"] = freqs[idx]
        result["fwhm_l"], _, _ = compute_fwhm(freqs, power, idx)

    if right_peaks:
        idx = min(right_peaks, key=lambda i: abs(freqs[i]))
        result["right_peak_idx"] = idx
        result["right_edge_freq"] = freqs[idx]
        result["fwhm_r"], _, _ = compute_fwhm(freqs, power, idx)

    return result

# === MAIN TEST CODE ===
if __name__ == "__main__":
    filename = "/Users/hrubiak/Desktop/20250624-melt-devel_00115_ds.txt"  # Replace with your file
    wavelengths, ds_data = read_spectral_data(filename)

    # Convert to cm⁻¹
    x_cm_inv, y_interp1 = convert_nm_to_cm_inv(wavelengths,ds_data)

    
    
    # Compute dx from wavelength range
    lambda_min = np.amin(x_cm_inv)
    lambda_max = np.amax(x_cm_inv)
    dx = (lambda_max - lambda_min) / (len(x_cm_inv) - 1)

    # === First pass: FFT only (no filtering) ===
    fft_result = compute_fft_and_detrend(
        y_interp1,
        dx=dx,
        edge_k=20, pad_width=10
    )

    plot_detrended_signal(fft_result)

    freqs = fft_result['freqs']
    power = fft_result['power_before']
    peak_info = detect_fft_peaks(
            freqs,
            power,
            prominence_ratio=0.05,
            freq_min=0.003,
            freq_max=0.015
            )     

    peaks = peak_info['peaks']     
    prominence_threshold = peak_info['prominence_threshold']   
    left_edge_freq = peak_info['left_edge_freq']   
    right_edge_freq = peak_info['right_edge_freq']   

    right_peak_idx = peak_info['right_peak_idx'] 
    left_peak_idx = peak_info['left_peak_idx'] 
   
    fwhm_r = peak_info['fwhm_r'] 
    fwhm_l = peak_info['fwhm_l'] 


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
    cut_range = fwhm_ave * 2
    freq_min = peaks_fre_ave - fwhm_ave * 2.5
    freq_max = peaks_fre_ave + fwhm_ave * 3
   

    filtered_result = apply_fft_filter(fft_result,
        freq_min=freq_min,
        freq_max=freq_max,
     
    )


    # === Plot final filtered result ===
    plot_results(
        filtered_result,
        freq_min=freq_min,
        freq_max=freq_max,
        log_scale=False,
        left_edge_freq=left_edge_freq,
        right_edge_freq=right_edge_freq,
        cutoff_prominence_value=prominence_threshold
    )