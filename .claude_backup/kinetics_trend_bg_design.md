# Kinetics-trend background — design (final)

**Physical goal.** Estimate the background at each strip's DS/US row by
interpolating between bg samples from neighboring strips on the vstacked
RAW canvas. Rationale: bg varies gradually across strips (roughly with
strip index, driven by time-on-chip). Within a single strip the bg-ROI
sits at a different y than the DS/US rows, so the current in-situ
subtraction uses bg at the wrong y. Sampling bg from the current strip
AND from a neighboring strip (whose bg row lands on the opposite side of
DS/US on the canvas) brackets the target row, and linear interpolation
gives a bg estimate AT the DS/US row.

**Axis is canvas-y (not exposure time, not strip index directly).** Each
frame f occupies ndarray rows `[f*sensor_height, (f+1)*sensor_height)` on
`raw_ccd = np.vstack(frames)`. The bg-ROI (per-frame coords) at rows
`[y_bg_min, y_bg_max]` sits at canvas-row `f*H + y_bg_center` for frame
f. The DS signal-ROI at canvas-row `f*H + y_ds_center`. Interpolate bg
between the two bg samples that bracket the DS row on the canvas.

**Interpolation math** (per column). Let `H = sensor_height`, and
`Δ = y_bg_center − y_signal_center` (in per-frame ndarray rows, signed).

* `Δ > 0` (bg below signal within frame): bracketing samples are frame
  `k` (own bg, distance `Δ` below DS on canvas) and frame `k−1` (bg
  distance `H − Δ` above DS on canvas). Weights:
  * `w_own = (H − Δ) / H`
  * `w_prev = Δ / H`
  * `bg_interp = w_own · B[k] + w_prev · B[k−1]`
  * Requires `k ≥ 1`; else fall back to insitu.
* `Δ < 0` (bg above signal within frame): bracket is `k` and `k+1`.
  * `w_own = (H − |Δ|) / H`, `w_next = |Δ| / H`
  * `bg_interp = w_own · B[k] + w_next · B[k+1]`
  * Requires `k ≤ N−2`; else fall back to insitu.
* `Δ = 0`: trivial, use own bg.

**Per side.** DS and US are computed independently: same `B[side]` matrix
(usually identical for both sides in kinetics per the shared-bg-ROI
convention; independent if user places distinct bg-ROIs). Each side uses
its own `Δ_ds` / `Δ_us` and picks its own neighbor. At endpoint frames
one side may fall back to insitu while the other interpolates.

**No exposure-time / `q_side` coupling.** Whether DS and US came from the
same physical exposure is irrelevant. The bg gradient is a canvas-y
property; interpolate by canvas-y. `q_ds`/`q_us` stay purely a time-axis
concern for the history plot.

**Cache.**
* `_bg_matrix_cache = {'ds': None, 'us': None}` on
  `TemperatureModelConfiguration`.
* Entry is the `(N, W_bg_x)` per-column bg matrix (via `get_roi_sum`
  applied to each frame's bg-ROI). Built lazily by `get_bg_matrix(side)`.
* Invalidates on: file load, bg-ROI move (per side), dim/geometry change.
* Does NOT invalidate on: DS/US signal-ROI move (only changes
  evaluation `Δ`, not the samples), standard-T, filter, cal load, mode
  change, `error_limit`, T-range, background-mode change.

**Pipeline wiring** (`SingleTemperatureModel.calc_data_spectrum`, mode
branch beside `insitu`/`prerecorded`/`hybrid`/`off`):
* `mode == 'kinetics_trend'`: call
  `parent._bg_interp_for_signal_roi(side, roi)` where `roi` is the
  current signal ROI. Helper returns per-column bg (length matching the
  signal ROI's x extent) or `None` to indicate "fall back to insitu."
* Fallback conditions returned as `None`: `num_frames <= 1`, dim not
  known, neighbor frame missing (endpoint), or bg-ROI x-range doesn't
  cover the signal-ROI x-range.
* Calibration path: `kinetics_trend` → `insitu` (single-frame, no
  meaningful trend).

**Back-reference.** `SingleTemperatureModel` gets `self._parent_config`
set by the owning config right after construction. Needed so the
pipeline branch can call the shared bg matrix.

**UI wiring.**
* `pyradiant/widget/TemperatureWidget.py` `BackgroundSubtractionGB` combo
  gets a new "Kinetics trend" entry.
* `pyradiant/controller/TemperatureController.py` `_BG_MODES_BY_INDEX`
  adds `'kinetics_trend'`; when `num_frames <= 1` the combo entry is
  disabled and, if selected, falls back to insitu.

**Diagnostic.** Deferred v1. The existing RAW tab already shows the
vstacked canvas — the trend is visible there. If needed later, add
marker overlays (horizontal lines at each strip's DS-y and US-y across
the vstack, plus a shaded band for the bg-ROI x-range).

**.trs compat.** The shim at
`TemperatureModelConfiguration.load_setting` (~line 1039) that maps
loaded `'kinetics_trend'` → `'insitu'` becomes real support: remove the
mapping now that `'kinetics_trend'` is in `_VALID_BACKGROUND_MODES`.

**What we deliberately do NOT do (v1).**
* No global fit, no MAD rejection — pure two-point linear interpolation.
* No diagnostic tab (RAW tab already conveys the picture).
* No handling of interleaved kinetics with `q_ds ≠ q_us` (cross-mode
  cal case) — the physics of "canvas-y bracketing" is unchanged, but
  DS/US bands come from different exposures and the ROI y-coords may
  not straightforwardly correspond. Explicit fallback branch to insitu
  for now; v2 concern.
* No user-facing "which neighbor to use" toggle — direction is
  determined by sign of `Δ`.

**Files touched.**
* `pyradiant/model/TemperatureModelConfiguration.py` — add cache fields,
  invalidation, `get_bg_matrix`, `_bg_interp_for_signal_roi`,
  `'kinetics_trend'` in `_VALID_BACKGROUND_MODES`, wire invalidation
  into file-load + bg-ROI setters, back-ref in `SingleTemperatureModel`,
  new branch in `calc_data_spectrum`, remove/replace the
  `'kinetics_trend'`→`'insitu'` shim in `load_setting`.
* `pyradiant/widget/TemperatureWidget.py` — add combo entry.
* `pyradiant/controller/TemperatureController.py` — extend
  `_BG_MODES_BY_INDEX`, enable/disable combo entry based on num_frames.

**Not touched.** History plot, cross-mode cal, save/load beyond the shim
removal, ROI widgets, calibration widgets, EPICS.
