import numpy as np
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift
from scipy.signal import find_peaks, savgol_filter
from scipy.interpolate import interp1d

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

def compute_fwhm(freqs, power, peak_idx):
    peak_power = power[peak_idx]
    half_max = peak_power / 2
    n = len(power)

    left_idx = peak_idx
    while left_idx > 0 and power[left_idx] > half_max:
        left_idx -= 1
    f_left = freqs[left_idx] if left_idx == 0 else np.interp(half_max, [power[left_idx], power[left_idx+1]], [freqs[left_idx], freqs[left_idx+1]])

    right_idx = peak_idx
    while right_idx < n - 1 and power[right_idx] > half_max:
        right_idx += 1
    f_right = freqs[right_idx] if right_idx == n-1 else np.interp(half_max, [power[right_idx-1], power[right_idx]], [freqs[right_idx-1], freqs[right_idx]])

    return abs(f_right - f_left), f_left, f_right

def compute_fft_and_detrend(signal, dx, edge_k=10, pad_width=20):
    n = len(signal)
    x = np.arange(n) * dx
    signal_zeroed = signal.copy()
    x_edges = np.concatenate([x[:edge_k], x[-edge_k:]])
    y_edges = np.concatenate([signal_zeroed[:edge_k], signal_zeroed[-edge_k:]])
    coeffs = np.polyfit(x_edges, y_edges, deg=1)
    poly_fit = np.polyval(coeffs, x)
    detrended = signal_zeroed - poly_fit
    signal_padded = np.concatenate([detrended[pad_width-1::-1], detrended, detrended[:-pad_width-1:-1]])
    poly_fit_padded = np.concatenate([poly_fit[pad_width-1::-1], poly_fit, poly_fit[:-pad_width-1:-1]])
    x_padded = np.arange(-pad_width, n+pad_width) * dx
    freqs = fftfreq(len(signal_padded), dx)
    fft_vals = fftshift(fft(signal_padded))
    power = np.abs(fft_vals)**2
    return {
        "original_signal": signal,
        "detrended_signal": signal_padded,
        "poly_fit": poly_fit_padded,
        "dc_offset": 0,
        "freqs": fftshift(freqs),
        "fft_vals": fft_vals,
        "power_before": power,
        "pad_width": pad_width,
        "padded_length": len(signal_padded),
        "x_padded": x_padded,
        "x": x,
        "signal_padded": signal_padded
    }

def detect_fft_peaks(freqs, power, prominence_ratio=0.05, freq_min=0.0, freq_max=np.inf):
    threshold = prominence_ratio * np.max(power)
    peaks, _ = find_peaks(power, prominence=threshold)
    center_idx = np.argmin(np.abs(freqs))
    left_peaks = [i for i in peaks if i < center_idx and freq_min <= abs(freqs[i]) <= freq_max]
    right_peaks = [i for i in peaks if i > center_idx and freq_min <= abs(freqs[i]) <= freq_max]

    result = {
        "left_peak_idx": None, "right_peak_idx": None,
        "left_edge_freq": None, "right_edge_freq": None,
        "fwhm_l": None, "fwhm_r": None,
        "peaks": peaks, "prominence_threshold": threshold
    }

    if left_peaks:
        i = max(left_peaks, key=lambda j: power[j])
        result.update({"left_peak_idx": i, "left_edge_freq": freqs[i], "fwhm_l": compute_fwhm(freqs, power, i)[0]})
    if right_peaks:
        i = min(right_peaks, key=lambda j: power[j])
        result.update({"right_peak_idx": i, "right_edge_freq": freqs[i], "fwhm_r": compute_fwhm(freqs, power, i)[0]})

    return result

def apply_fft_filter(fft_result, freq_min, freq_max):
    fft_vals = fft_result["fft_vals"].copy()
    freqs = fft_result["freqs"]
    band_mask = (np.abs(freqs) >= freq_min) & (np.abs(freqs) <= freq_max)
    idx_band = np.where(band_mask)[0]
    if len(idx_band) == 0:
        pad = fft_result["pad_width"]
        n_orig = len(fft_result["original_signal"])
        restored = fft_result["signal_padded"] + fft_result["poly_fit"]
        return {
            "original_signal": fft_result["original_signal"],
            "restored_signal_trimmed": restored[pad:pad+n_orig],
            "filtered_signal_trimmed": fft_result["signal_padded"][pad:pad+n_orig],
            "power_before": fft_result["power_before"],
            "power_after": fft_result["power_before"],
            "freqs": freqs,
        }
    i0, i1 = idx_band[0], idx_band[-1]
    val_before = fft_vals[i0-1] if i0 > 0 else 0
    val_after = fft_vals[i1+1] if i1+1 < len(fft_vals) else 0
    fft_vals[band_mask] = 0.5 * (val_before + val_after)
    filtered = np.real(ifft(ifftshift(fft_vals)))
    restored = filtered + fft_result["poly_fit"]
    pw_after = np.abs(fft_vals)**2
    pad = fft_result["pad_width"]
    n_orig = len(fft_result["original_signal"])
    return {
        "original_signal": fft_result["original_signal"],
        "restored_signal_trimmed": restored[pad:pad+n_orig],
        "filtered_signal_trimmed": filtered[pad:pad+n_orig],
        "power_before": fft_result["power_before"],
        "power_after": pw_after,
        "freqs": freqs
    }

def filter_oscillatory_component(x_nm, y_counts, sg_window_cm=1500, freq_min=0.0005, freq_max=np.inf):
    """
    Remove interference fringe oscillations from a thermal emission spectrum.

    Uses a wide Savitzky-Golay filter to estimate the slowly varying blackbody
    background, computes the FFT of the residual to locate the dominant fringe
    frequency via argmax (no hardcoded search window), then applies a band-stop
    notch to the original signal.

    Returns
    -------
    filtered_nm : np.ndarray
        Filtered spectral intensities on the original wavelength grid.
    fringe_info : dict with keys:
        'f_osc'         – fringe frequency in cm (= 1 / wavenumber_period)
        'period_cm_inv' – fringe period in cm⁻¹
        'nd_um'         – optical half-path n·d in μm  (= f_osc/2 × 1e4)
    """
    x_cm_inv, y_cm = convert_nm_to_cm_inv(x_nm, y_counts)
    dx = (x_cm_inv[-1] - x_cm_inv[0]) / (len(x_cm_inv) - 1)

    # Wide SG smoother removes the slow blackbody curve; residual ≈ pure fringes
    window_pts = int(round(sg_window_cm / dx))
    if window_pts % 2 == 0:
        window_pts += 1
    window_pts = max(window_pts, 5)
    background = savgol_filter(y_cm, window_pts, 3)
    residual = y_cm - background

    # FFT of residual — argmax within [freq_min, freq_max]
    freqs_res = fftshift(fftfreq(len(residual), dx))
    power_res = np.abs(fftshift(fft(residual))) ** 2
    pos_mask = (freqs_res >= freq_min) & (freqs_res <= freq_max)
    f_pos = freqs_res[pos_mask]
    p_pos = power_res[pos_mask]
    if len(f_pos) == 0:
        _, y_out = convert_cm_inv_to_nm(x_cm_inv, y_cm)
        return y_out, None
    dominant_idx = np.argmax(p_pos)
    f_osc = f_pos[dominant_idx]

    # FWHM of the detected peak → notch width
    fwhm = compute_fwhm(f_pos, p_pos, dominant_idx)[0]
    filt_min = max(f_osc - 2.5 * fwhm, freq_min)
    filt_max = f_osc + 3.0 * fwhm

    # Apply band-stop to the original (detrended + padded) signal
    fft_result = compute_fft_and_detrend(y_cm, dx=dx, edge_k=20, pad_width=10)
    filtered = apply_fft_filter(fft_result, filt_min, filt_max)
    _, filtered_nm = convert_cm_inv_to_nm(x_cm_inv, filtered["restored_signal_trimmed"])

    fringe_info = {
        'f_osc': f_osc,
        'period_cm_inv': 1.0 / f_osc,
        'nd_um': (f_osc / 2.0) * 1e4,
    }
    return filtered_nm, fringe_info