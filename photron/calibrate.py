"""
Wavelength calibration of an Acton 300 g/mm spectrometer centered at 700 nm,
using a He/Ar calibration lamp imaged on a Photron camera.

Reduces the 2D TIFF to a 1D spectrum (counts vs pixel), detects peaks, matches
them to known He/Ar lines by a brute-force RANSAC-style search, and fits a
polynomial pixel -> wavelength calibration.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from scipy.signal import find_peaks
from scipy.ndimage import percentile_filter, gaussian_filter1d

# ---------------------------------------------------------------------------
# Reference Ne/Ar emission lines (NIST, strong lines in the ~580-820 nm band)
# ---------------------------------------------------------------------------
# (wavelength_nm, species, relative_intensity_hint)
NE_AR_LINES = [
    # Ne I
    (585.2488, "Ne I", 500),
    (588.1895, "Ne I", 100),
    (594.4834, "Ne I", 100),
    (597.5534, "Ne I", 100),
    (603.0000, "Ne I", 100),
    (607.4338, "Ne I", 100),
    (609.6163, "Ne I", 100),
    (614.3063, "Ne I", 500),
    (616.3594, "Ne I", 120),
    (621.7281, "Ne I", 150),
    (626.6495, "Ne I", 150),
    (633.4428, "Ne I", 250),
    (638.2991, "Ne I", 500),
    (640.2246, "Ne I", 2000),
    (650.6528, "Ne I", 500),
    (653.2882, "Ne I", 100),
    (659.8953, "Ne I", 150),
    (667.8276, "Ne I", 100),
    (671.7043, "Ne I", 250),
    (692.9467, "Ne I", 1000),
    (703.2413, "Ne I", 1000),
    (717.3938, "Ne I", 100),
    (724.5167, "Ne I", 500),
    (743.8898, "Ne I", 100),
    (748.8871, "Ne I", 100),
    (753.5774, "Ne I", 100),
    # Ar I
    (696.5431, "Ar I", 10000),
    (706.7218, "Ar I", 10000),
    (714.7042, "Ar I", 1000),
    (720.6980, "Ar I", 800),
    (727.2936, "Ar I", 2000),
    (737.2118, "Ar I", 500),
    (738.3980, "Ar I", 6000),
    (750.3869, "Ar I", 20000),
    (751.4652, "Ar I", 15000),
    (763.5106, "Ar I", 25000),
    (772.3761, "Ar I", 15000),
    (772.4207, "Ar I", 5000),
    (794.8176, "Ar I", 20000),
    (800.6157, "Ar I", 15000),
    (801.4786, "Ar I", 20000),
    (810.3693, "Ar I", 15000),
    (811.5311, "Ar I", 35000),
]


def load_and_reduce(
    tif_path: Path, band_half_height: int = 100
) -> tuple[np.ndarray, np.ndarray]:
    """Load the TIFF, take a horizontal band in the middle, sum vertically."""
    img = np.array(Image.open(tif_path)).astype(np.float64)
    ny, nx = img.shape
    y0 = ny // 2 - band_half_height
    y1 = ny // 2 + band_half_height
    band = img[y0:y1, :]
    spectrum = band.sum(axis=0)
    pixels = np.arange(nx)
    return pixels, spectrum, img, (y0, y1)


def subtract_baseline(y: np.ndarray, window: int = 101, pct: float = 20.0) -> np.ndarray:
    """Rolling percentile baseline (very robust to peaks)."""
    baseline = percentile_filter(y, percentile=pct, size=window)
    return y - baseline


def detect_peaks(
    pixels: np.ndarray,
    spectrum: np.ndarray,
    smooth_sigma: float = 1.5,
    noise_sigma_mult: float = 8.0,
    min_distance: int = 6,
) -> np.ndarray:
    """
    Find peak pixel positions on a smoothed spectrum.

    - `smooth_sigma`: Gaussian smoothing width (pixels) applied before peak finding.
    - `noise_sigma_mult`: prominence threshold in units of estimated noise sigma
      (from MAD of the smoothed spectrum), so it adapts to the data.
    - Peak positions are refined by a parabolic sub-pixel fit on the smoothed data.
    """
    smoothed = gaussian_filter1d(spectrum, smooth_sigma)
    # Noise estimate: high-frequency residual (raw - smoothed) is dominated by
    # noise, not by the emission peaks. MAD -> sigma-equivalent.
    hf = spectrum - smoothed
    mad = np.median(np.abs(hf - np.median(hf)))
    noise = 1.4826 * mad if mad > 0 else np.std(hf)
    prom = noise_sigma_mult * noise
    idx, _ = find_peaks(smoothed, prominence=prom, distance=min_distance)

    refined = []
    for i in idx:
        if 1 <= i < len(smoothed) - 1:
            ym1, y0, yp1 = smoothed[i - 1], smoothed[i], smoothed[i + 1]
            denom = ym1 - 2 * y0 + yp1
            delta = 0.5 * (ym1 - yp1) / denom if denom != 0 else 0.0
            refined.append(i + delta)
        else:
            refined.append(float(i))
    return np.array(refined), smoothed[idx], smoothed


def _matches_for(disp, pixel_center, peak_pixels, peak_heights, n_pixels,
                 ref_wl, ref_v, tol_pixels, center_wl):
    """Nearest-neighbor assignment; two ref lines fighting for one peak -> closest wins."""
    pred_px = pixel_center + (ref_wl - center_wl) / disp
    proposals = []
    for rw, ppx, rv in zip(ref_wl, pred_px, ref_v):
        if not (0 <= ppx < n_pixels):
            continue
        d_px = np.abs(peak_pixels - ppx)
        j = int(np.argmin(d_px))
        if d_px[j] <= tol_pixels:
            proposals.append((j, rw, float(d_px[j]), float(rv)))
    best_for_peak: dict[int, tuple[float, float, float]] = {}
    for j, rw, dpx, rv in proposals:
        if j not in best_for_peak or dpx < best_for_peak[j][1]:
            best_for_peak[j] = (rw, dpx, rv)
    return [(rw, peak_pixels[j], peak_heights[j], rv)
            for j, (rw, _, rv) in best_for_peak.items()]


def match_peaks_to_lines(
    peak_pixels: np.ndarray,
    peak_heights: np.ndarray,
    n_pixels: int,
    center_wl: float = 700.0,
    ref_lines: list[tuple[float, str, float]] = NE_AR_LINES,
    disp_abs_min: float = 0.10,
    disp_abs_max: float = 0.35,
    disp_steps: int = 250,
    tol_pixels: float = 3.0,
    min_matches: int = 8,
):
    """
    Search (dispersion, pixel_center) for the linear model
        wl = center_wl + disp * (pixel - pixel_center)
    that maximizes the number of detected peaks matched to reference lines
    within `tol_pixels`, using sqrt(peak_intensity * ref_intensity) as a
    tiebreaker.

    Seeds pixel_center from every (strong peak, strong ref line) pair, which
    covers the search space without a dense 2D grid.
    """
    ref_wl = np.array([w for w, _, _ in ref_lines])
    ref_v = np.array([v for _, _, v in ref_lines])

    dispersions = np.concatenate([
        np.linspace(disp_abs_min, disp_abs_max, disp_steps),
        -np.linspace(disp_abs_min, disp_abs_max, disp_steps),
    ])

    n_strong_peaks = min(20, len(peak_pixels))
    strong_peaks_px = peak_pixels[np.argsort(peak_heights)[::-1][:n_strong_peaks]]
    strong_ref_wl = np.array([w for w, _, _ in sorted(ref_lines, key=lambda t: -t[2])[:15]])

    best_key = (-1, -np.inf)
    best = None  # (disp, pixel_center, matches)

    for disp in dispersions:
        for peak_px in strong_peaks_px:
            for ref_w in strong_ref_wl:
                pixel_center = peak_px - (ref_w - center_wl) / disp
                if not (0 <= pixel_center <= n_pixels):
                    continue

                matches = _matches_for(
                    disp, pixel_center,
                    peak_pixels, peak_heights, n_pixels,
                    ref_wl, ref_v, tol_pixels, center_wl,
                )
                n = len(matches)
                if n < min_matches:
                    continue
                score = sum(float(np.sqrt(max(ph, 1.0) * rv)) for _, _, ph, rv in matches)
                key = (n, score)
                if key > best_key:
                    best_key = key
                    best = (disp, pixel_center, matches)

    if best is None:
        return None
    disp, pixel_center, matches = best
    matches = [(rw, ppx, ph) for rw, ppx, ph, _ in matches]
    matches.sort(key=lambda t: t[1])
    return disp, pixel_center, matches


def fit_polynomial(matches, degree: int = 2):
    px = np.array([m[1] for m in matches])
    wl = np.array([m[0] for m in matches])
    coeffs = np.polyfit(px, wl, degree)
    fit_wl = np.polyval(coeffs, px)
    residuals = wl - fit_wl
    return coeffs, residuals, px, wl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "tif",
        nargs="?",
        default="20260810_135352_C001H001S0001000001.tif",
        help="Input TIFF file",
    )
    ap.add_argument("--band", type=int, default=100, help="Half-height of vertical band (pixels)")
    ap.add_argument("--center-wl", type=float, default=700.0, help="Grating center wavelength (nm)")
    ap.add_argument("--degree", type=int, default=2, help="Polynomial degree for calibration fit")
    ap.add_argument("--smooth", type=float, default=1.5,
                    help="Gaussian smoothing sigma (pixels) before peak finding")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="Peak prominence threshold in units of noise sigma (MAD-based)")
    ap.add_argument("--out-prefix", default="calibration", help="Prefix for output files")
    args = ap.parse_args()

    tif_path = Path(args.tif)
    print(f"Loading {tif_path}")
    pixels, spectrum, image, (y0, y1) = load_and_reduce(tif_path, args.band)
    print(f"  image shape {image.shape}, band rows {y0}:{y1}")

    spectrum_bs = subtract_baseline(spectrum)
    peak_px, peak_h, spectrum_smooth = detect_peaks(
        pixels, spectrum_bs, smooth_sigma=args.smooth, noise_sigma_mult=args.threshold
    )
    print(f"  detected {len(peak_px)} peaks "
          f"(smooth sigma={args.smooth}, threshold={args.threshold} sigma)")

    result = match_peaks_to_lines(
        peak_px, peak_h, n_pixels=len(pixels), center_wl=args.center_wl
    )
    if result is None:
        print("Failed to match any peaks to reference lines")
        return
    disp0, pixel_center0, matches = result
    print(f"  linear guess: dispersion={disp0:+.4f} nm/pix, pixel_center={pixel_center0:.1f}")
    print(f"  matched {len(matches)} lines")

    if len(matches) < args.degree + 1:
        print(f"Only {len(matches)} matches, degree {args.degree} fit not possible; using linear.")
        args.degree = max(1, len(matches) - 1)

    coeffs, residuals, m_px, m_wl = fit_polynomial(matches, degree=args.degree)
    rms = np.sqrt(np.mean(residuals ** 2))
    print(f"  polynomial fit (deg {args.degree}) coeffs (highest first): {coeffs}")
    print(f"  RMS residual: {rms*1000:.2f} pm  ({rms:.4f} nm)")

    wl_axis = np.polyval(coeffs, pixels)
    print(f"  wavelength range: {wl_axis.min():.2f} - {wl_axis.max():.2f} nm")

    # ---- output: text calibration ----
    txt_path = tif_path.parent / f"{args.out_prefix}.txt"
    with open(txt_path, "w") as f:
        f.write("# Wavelength calibration\n")
        f.write(f"# Source: {tif_path.name}\n")
        f.write(f"# Grating center wavelength: {args.center_wl} nm\n")
        f.write(f"# Polynomial (highest order first): {list(coeffs)}\n")
        f.write(f"# RMS residual: {rms:.5f} nm\n")
        f.write("#\n# pixel  wavelength_nm  species  residual_nm\n")
        species_lookup = {w: s for w, s, _ in NE_AR_LINES}
        for (rw, ppx, _), res in zip(matches, residuals):
            f.write(f"{ppx:8.3f}  {rw:10.4f}  {species_lookup[rw]:5s}  {res:+.4f}\n")
        f.write("#\n# pixel  wavelength_nm  counts\n")
        for p, w, c in zip(pixels, wl_axis, spectrum):
            f.write(f"{p:5d}  {w:9.4f}  {c:.1f}\n")
    print(f"  wrote {txt_path}")

    # ---- output: sidecar JSON for PyRadiant TifFile reader ----
    # np.polyfit returns descending order; TifFile expects ascending, 0-indexed.
    ascending = list(map(float, coeffs[::-1]))
    sidecar = {
        "polynomial_coeffs": ascending,
        "convention": "ascending_zero_indexed",
        "center_wl": float(args.center_wl),
        "grating": "",
        "source_tif": tif_path.name,
        "rms_nm": float(rms),
        "n_matches": len(matches),
        "band_center": int((y0 + y1) // 2),
        "band_half_height": int((y1 - y0) // 2),
        "dispersion_axis": "horizontal",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    json_path = tif_path.parent / f"{args.out_prefix}.json"
    with open(json_path, "w") as f:
        json.dump(sidecar, f, indent=2)
    print(f"  wrote {json_path}")

    # ---- output: plots ----
    fig, axes = plt.subplots(3, 1, figsize=(12, 10))

    ax = axes[0]
    ax.imshow(image, cmap="gray", aspect="auto")
    ax.axhline(y0, color="cyan", lw=0.8)
    ax.axhline(y1, color="cyan", lw=0.8)
    ax.set_title(f"Raw image: {tif_path.name}  (band {y0}:{y1})")
    ax.set_xlabel("pixel column")
    ax.set_ylabel("pixel row")

    ax = axes[1]
    ax.plot(pixels, spectrum, "0.7", lw=0.6, label="raw sum")
    ax.plot(pixels, spectrum_smooth + (spectrum.min() - spectrum_smooth.min()),
            "b-", lw=0.7, alpha=0.8, label=f"smoothed (σ={args.smooth})")
    ax.plot(peak_px, np.interp(peak_px, pixels, spectrum), "r.", ms=6, label="peaks")
    for rw, ppx, _ in matches:
        ax.axvline(ppx, color="green", lw=0.4, alpha=0.5)
        ax.text(ppx, spectrum.max(), f"{rw:.2f}", rotation=90,
                fontsize=6, ha="right", va="top", color="green")
    ax.set_xlabel("pixel column")
    ax.set_ylabel("counts (band-integrated)")
    ax.set_title(f"1D spectrum, {len(matches)} matched lines")
    ax.legend(loc="upper right", fontsize=8)

    ax = axes[2]
    ax.plot(wl_axis, spectrum, "k-", lw=0.8)
    for rw, ppx, _ in matches:
        ax.axvline(rw, color="green", lw=0.4, alpha=0.5)
        ax.text(rw, spectrum.max(), f"{rw:.2f} {species_lookup[rw]}", rotation=90,
                fontsize=6, ha="right", va="top", color="green")
    ax.set_xlabel("wavelength (nm)")
    ax.set_ylabel("counts")
    ax.set_title(f"Calibrated spectrum  (deg {args.degree}, RMS {rms*1000:.1f} pm)")

    plt.tight_layout()
    plot_path = tif_path.parent / f"{args.out_prefix}.png"
    fig.savefig(plot_path, dpi=140)
    print(f"  wrote {plot_path}")

    # residuals plot
    fig2, ax = plt.subplots(figsize=(8, 4))
    ax.plot(m_wl, residuals * 1000, "o")
    ax.axhline(0, color="k", lw=0.5)
    ax.set_xlabel("wavelength (nm)")
    ax.set_ylabel("residual (pm)")
    ax.set_title(f"Fit residuals  (RMS {rms*1000:.1f} pm)")
    plt.tight_layout()
    resid_path = tif_path.parent / f"{args.out_prefix}_residuals.png"
    fig2.savefig(resid_path, dpi=140)
    print(f"  wrote {resid_path}")


if __name__ == "__main__":
    main()
