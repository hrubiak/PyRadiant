# `T_history.json` format

PyRadiant writes one `T_history.json` per data folder, next to the SPE files. It holds the temperature fit history for that folder, so other programs can match temperatures against pressure, XRD, or other time-stamped data. It also serves as a reference for labeling similar per-frame results in other programs.

Current `schema_version`: **1**. Source of truth: `pyradiant/model/tlog/json_writer.py` (serialization) and `pyradiant/model/tlog/record.py` (field meanings).

## Lifecycle

- **Location:** `<data folder>/T_history.json`. The folder is the one holding the currently loaded data file. It changes when the user opens a file elsewhere or the EPICS folder PV changes.
- **Latest fit only:** each fitted `(file, frame)` pair has exactly one entry. Re-fitting a frame (new ROIs, new calibration, etc.) replaces its entry. Frames that were never fitted are absent, so a file may have entries for only some of its frames.
- **Atomic rewrite:** the whole file is rewritten on every update (temp file, `fsync`, rename). A reader never sees a half-written file. Poll the file's modification time and re-read the whole file when it changes.
- **Existing file is merged:** when PyRadiant opens a folder that already has a `T_history.json`, it loads it and keeps adding to it.
- **Clear:** the T-log "Clear" button rewrites the file with an empty `fits` list.

## Top level

```json
{
  "schema_version": 1,
  "timezone": "local, America/Chicago",
  "folder": "/data/2026-11/run12",
  "last_updated_local": "2026-11-11T14:03:27.512044",
  "fits": [ ... ]
}
```

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | int | Format version. Bumped on breaking changes. |
| `timezone` | string | **Informational label only.** All timestamps in the file are naive local wall-clock time on the machine that ran PyRadiant. This label names that zone, e.g. `"local, America/Chicago"`, `"local, CST"`, or just `"local"`. |
| `folder` | string | Absolute path of the data folder when the file was written. |
| `last_updated_local` | string | ISO-8601 naive local time of this write. |
| `fits` | array | One object per fitted `(file, frame)`. Sorted by `exposure_start_local` (or `fit_timestamp_local` when that is null), then `file`, then `frame`. |

## Per-fit entry

```json
{
  "file": "20261111_Pt_00048.spe",
  "frame": 0,
  "exposure_start_local": "2026-11-11T14:03:20.104000",
  "exposure_start_source": "origin_computed",
  "fit_timestamp_local": "2026-11-11T14:03:27.498311",
  "T_DS": 2154.7,
  "T_DS_err": 12.3,
  "T_US": 2098.1,
  "T_US_err": 15.8,
  "scaling_DS": 3.21e-07,
  "scaling_US": 2.87e-07,
  "counts_DS": 1834221.0,
  "counts_US": 1502917.0,
  "fringe_freq_DS": null,
  "fringe_freq_US": null,
  "fringe_nd_DS": null,
  "fringe_nd_US": null,
  "exposure": 0.1,
  "gain": 1.0,
  "detector": "PI-MAX4: 1024B/EM",
  "mode": "dual"
}
```

The values above are illustrative.

`DS` = downstream side and `US` = upstream side of the laser-heated sample. Each has its own spectrum region on the detector and its own fit.

| Key | Type | Units | Meaning |
|---|---|---|---|
| `file` | string | — | Data file **basename** (no directory; see top-level `folder`). |
| `frame` | int | — | **0-based** frame index within `file`. Kinetics caveat below. |
| `exposure_start_local` | string \| null | ISO-8601, naive local | Estimated start of this frame's exposure. **Use this field to match against other data.** Null when the file has no usable timestamp. |
| `exposure_start_source` | string | — | Where `exposure_start_local` came from. Values below. |
| `fit_timestamp_local` | string | ISO-8601, naive local | When PyRadiant computed this fit. Changes on every re-fit. It is processing time, not acquisition time. |
| `T_DS`, `T_US` | number \| null | K | Fitted temperature. |
| `T_DS_err`, `T_US_err` | number \| null | K | Fit uncertainty of the temperature. |
| `scaling_DS`, `scaling_US` | number \| null | arbitrary | Amplitude (scale) factor of the temperature fit. Depends on calibration and geometry. Useful only for relative comparisons within a run. |
| `counts_DS`, `counts_US` | number \| null | detector counts | Background-subtracted counts summed over the signal region. A rough signal-strength / saturation indicator. |
| `fringe_freq_DS`, `fringe_freq_US` | number \| null | cm | Fitted interference-fringe oscillation frequency. Null when no fringe fit ran. |
| `fringe_nd_DS`, `fringe_nd_US` | number \| null | μm | Optical half-path `n·d` derived from the fringe fit. Null when no fringe fit ran. |
| `exposure` | number | s | Exposure time from the file header; `0.0` if unknown. |
| `gain` | number | — | Detector gain from the file header; `1.0` if unknown. |
| `detector` | string | — | Camera model from the file header, e.g. `"PI-MAX4: 1024B/EM"`; `"unspecified"` if unknown (e.g. older SPE v2 files). |
| `mode` | string | — | `"dual"` (DS and US both fitted) or `"single"` (DS only). |

### `exposure_start_source` values

| Value | Meaning |
|---|---|
| `origin_computed` | SPE v3: file creation time from the XML `Origin` element, plus `frame × exposure`. The common case. |
| `v2_header` | Same formula, with the origin time taken from the SPE v2 binary header. |
| `unknown` | No timestamp available. `exposure_start_local` is null. Also used for non-SPE sources (HDF5, TIFF, Area Detector stream). |

The value `lightfield_metadata` (true per-frame hardware timestamps) is reserved but not produced yet.

`origin_computed` and `v2_header` assume frames are back to back with no readout gap. The resulting drift is negligible for matching against data logged at about 10 Hz or slower, but it adds up over long multi-frame files.

## Rules for null values

- JSON has no NaN, so every undefined number is written as `null`. Treat `null` as "no value", never as zero.
- **Failed fit:** `T_*` and `T_*_err` are null.
- **Error gate:** PyRadiant has a user-set error limit. If a side's `T_*_err` exceeds it, both `T_*` and `T_*_err` for that side are written as null. So a null temperature can mean a failed fit or a fit rejected as too uncertain.
- **`mode: "single"`:** all US fields are null, **except `counts_US`, which is written as `0.0`**. Check `mode` before reading US values.
- `counts_*` is `0.0`, not null, when no spectrum exists.

## Kinetics caveat for `frame`

In kinetics mode, PyRadiant can pair DS and US from **different** readout frames so that both sides show the same physical exposure. When that happens, `frame` is the **DS** readout frame. `T_US` comes from a different frame, and that frame number is not recorded. In normal (non-kinetics) mode, and in kinetics when both sides use the same frame, `frame` applies to both sides.

## Reading it

- The unique key is `(file, frame)`. Build absolute paths as `folder` + `file`.
- Ignore keys you don't recognize. Check `schema_version`, and refuse or warn on a version you don't support.
- Parse timestamps with any ISO-8601 parser. They carry no UTC offset, so interpret them in the zone named by `timezone`.
- Example (Python):

```python
import json
from datetime import datetime

with open(path, encoding="utf-8") as f:
    doc = json.load(f)
assert doc["schema_version"] == 1

for fit in doc["fits"]:
    t0 = fit["exposure_start_local"]
    t0 = datetime.fromisoformat(t0) if t0 else None
    T_ds = fit["T_DS"]                                  # None means no value
    T_us = fit["T_US"] if fit["mode"] == "dual" else None
```

## Relation to `T_log.txt`

PyRadiant also writes the legacy tab-separated `T_log.txt` in the same folder. It is append-only (re-fits add rows), has fewer fields, writes undefined values as `0`, and numbers frames **from 1**. New programs should read `T_history.json`.

## Conventions worth reusing

If you are labeling similar per-frame results in another program, these choices have worked well:

- **Side suffixes:** `_DS` / `_US`, with uncertainties as `<name>_err` (e.g. `T_DS`, `T_DS_err`).
- **Key each record by `(file, frame)`**, with 0-based frames and the file basename. Put the folder once at the top level.
- **Keep acquisition time and processing time separate:** `exposure_start_local` vs. `fit_timestamp_local`.
- **Record where each timestamp came from** (`exposure_start_source`), so readers know how much to trust it.
- **Use naive local ISO-8601 timestamps plus one top-level `timezone` label**, rather than mixing offsets per record.
- **Write `null` for undefined values**, never `0` or NaN.
- **Include a top-level `schema_version`**, and rewrite the file atomically.
