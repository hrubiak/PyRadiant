# -*- coding: utf8 -*-
# PyRadiant - T-log v2: canonical record schema.

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Mapping, Any


# One header, one place. Widget/store/writer/reader all agree on this order.
FIELD_NAMES = (
    "file",
    "frame",
    "path",
    "T_DS",
    "T_US",
    "T_DS_error",
    "T_US_error",
    "detector",
    "exposure_time_sec",
    "gain",
    "scaling_DS",
    "scaling_US",
    "counts_DS",
    "counts_US",
)

# On-disk TSV header. Matches the legacy T_log.txt schema byte-for-byte so
# existing files stay readable and downstream analysis scripts don't break.
LEGACY_TSV_HEADER = (
    "# File\tFrame\tPath\tT_DS\tT_US\tT_DS_error\tT_US_error\tDetector\t"
    "Exposure Time [sec]\tGain\tscaling_DS\tscaling_US\tcounts_DS\tcounts_US\n"
)

# Map legacy TSV column labels → dataclass field names, for reading old logs.
_LEGACY_TO_FIELD = {
    "# File": "file",
    "Frame": "frame",
    "Path": "path",
    "T_DS": "T_DS",
    "T_US": "T_US",
    "T_DS_error": "T_DS_error",
    "T_US_error": "T_US_error",
    "Detector": "detector",
    "Exposure Time [sec]": "exposure_time_sec",
    "Gain": "gain",
    "scaling_DS": "scaling_DS",
    "scaling_US": "scaling_US",
    "counts_DS": "counts_DS",
    "counts_US": "counts_US",
}


def _nan_safe_int_str(x: float) -> str:
    """Legacy log format: integer temperature, '0' for NaN. Preserved so
    downstream scripts see byte-identical output."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "0"
    return str(int(x))


def _nan_safe_exp_str(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "0"
    return format(float(x), ".3e")


@dataclass(frozen=True)
class TLogRecord:
    """A single T-log row. Immutable; construct once, pass everywhere.

    The float fields carry NaN when the underlying quantity is undefined
    (fit failed, mode='single' hides US, error above the display limit).
    Serialization (to_tsv_row) collapses NaN to '0' to keep the on-disk
    schema identical to the legacy T_log.txt.
    """

    file: str = ""
    frame: int = 0
    path: str = ""
    T_DS: float = float("nan")
    T_US: float = float("nan")
    T_DS_error: float = float("nan")
    T_US_error: float = float("nan")
    detector: str = "unspecified"
    exposure_time_sec: float = 0.0
    gain: float = 1.0
    scaling_DS: float = float("nan")
    scaling_US: float = float("nan")
    counts_DS: float = 0.0
    counts_US: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    # -------- Serialization --------------------------------------------------
    def to_tsv_row(self) -> str:
        """Legacy-format TSV row (no trailing newline)."""
        return "\t".join((
            self.file,
            str(int(self.frame) + 1),  # legacy log is 1-indexed
            self.path,
            _nan_safe_int_str(self.T_DS),
            _nan_safe_int_str(self.T_US),
            _nan_safe_int_str(self.T_DS_error),
            _nan_safe_int_str(self.T_US_error),
            self.detector,
            str(self.exposure_time_sec),
            str(self.gain),
            _nan_safe_exp_str(self.scaling_DS),
            _nan_safe_exp_str(self.scaling_US),
            _nan_safe_exp_str(self.counts_DS),
            _nan_safe_exp_str(self.counts_US),
        ))

    def as_dict(self) -> dict:
        return asdict(self)

    # -------- Construction ---------------------------------------------------
    @classmethod
    def from_legacy_tsv_row(cls, row: Mapping[str, str]) -> Optional["TLogRecord"]:
        """Parse a row read from a legacy TSV log. Returns None on any error
        so the reader can skip bad rows without failing the whole load."""
        try:
            def _f(key: str, default: float = 0.0) -> float:
                v = row.get(key, "")
                if v == "" or v is None:
                    return default
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default

            file_val = row.get("# File", "") or ""
            frame_str = row.get("Frame", "1") or "1"
            try:
                frame_1_indexed = int(float(frame_str))
            except (TypeError, ValueError):
                frame_1_indexed = 1

            return cls(
                file=file_val,
                frame=max(0, frame_1_indexed - 1),  # store 0-indexed internally
                path=row.get("Path", "") or "",
                T_DS=_f("T_DS"),
                T_US=_f("T_US"),
                T_DS_error=_f("T_DS_error"),
                T_US_error=_f("T_US_error"),
                detector=row.get("Detector", "unspecified") or "unspecified",
                exposure_time_sec=_f("Exposure Time [sec]"),
                gain=_f("Gain", 1.0),
                scaling_DS=_f("scaling_DS"),
                scaling_US=_f("scaling_US"),
                counts_DS=_f("counts_DS"),
                counts_US=_f("counts_US"),
            )
        except Exception:
            return None

    @classmethod
    def from_configuration(cls, cfg: Any, frame: int) -> "TLogRecord":
        """Build a record from the *current* state of a
        TemperatureModelConfiguration. Pure — reads only, no side effects.

        Replaces the record-building half of the old
        TemperatureModelConfiguration.write_to_log_file(). Callers can invoke
        this from any recompute site and hand the result to store.append()
        and writer.enqueue() without touching the model further.

        NaN handling: raw model NaNs (fit failed, gate exceeded) are preserved
        in the record. to_tsv_row() collapses them to '0' on disk to preserve
        the legacy log format. The store keeps NaNs so the widget can plot
        gaps correctly.
        """
        filename = getattr(cfg, "filename", None) or ""
        data_file = getattr(cfg, "data_img_file", None)

        # Error-gate: replicate the legacy display-side gate exactly so the
        # on-disk log matches the previous behaviour byte-for-byte for the
        # common path. Fits with T_err above the limit are blanked (both T
        # and T_err become NaN → '0' on disk).
        err_limit = float(getattr(cfg, "error_limit", float("inf")) or float("inf"))

        def _gated(t: float, terr: float) -> tuple[float, float]:
            if t is None or terr is None:
                return float("nan"), float("nan")
            if isinstance(terr, float) and math.isnan(terr):
                return t, terr
            if terr > err_limit:
                return float("nan"), float("nan")
            return t, terr

        ds_t, ds_terr = _gated(
            float(getattr(cfg, "ds_temperature", float("nan"))),
            float(getattr(cfg, "ds_temperature_error", float("nan"))),
        )
        us_t, us_terr = _gated(
            float(getattr(cfg, "us_temperature", float("nan"))),
            float(getattr(cfg, "us_temperature_error", float("nan"))),
        )

        # In single-sided mode the US column set is meaningless — blank it
        # exactly like the legacy writer did.
        mode = getattr(cfg, "mode", "dual")
        if mode == "single":
            us_t = float("nan")
            us_terr = float("nan")
            us_scaling = float("nan")
            us_counts = 0.0
        else:
            us_scaling = float(getattr(cfg, "us_scaling", float("nan")))
            us_spec = getattr(cfg, "us_data_spectrum", None)
            us_counts = float(getattr(us_spec, "counts", 0.0) or 0.0)

        ds_scaling = float(getattr(cfg, "ds_scaling", float("nan")))
        ds_spec = getattr(cfg, "ds_data_spectrum", None)
        ds_counts = float(getattr(ds_spec, "counts", 0.0) or 0.0)

        detector = getattr(data_file, "detector", "unspecified") or "unspecified"
        exposure = float(getattr(data_file, "exposure_time", 0.0) or 0.0)
        gain = float(getattr(data_file, "gain", 1.0) or 1.0)

        return cls(
            file=os.path.basename(filename),
            frame=int(frame),
            path=os.path.dirname(filename),
            T_DS=ds_t,
            T_US=us_t,
            T_DS_error=ds_terr,
            T_US_error=us_terr,
            detector=detector,
            exposure_time_sec=exposure,
            gain=gain,
            scaling_DS=ds_scaling,
            scaling_US=us_scaling,
            counts_DS=ds_counts,
            counts_US=us_counts,
        )
