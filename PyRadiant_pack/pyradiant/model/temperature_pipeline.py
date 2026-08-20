"""
Temperature calculation pipeline for PyRadiant.

This module is the single, canonical place that lists every stage of the
temperature computation, from raw CCD data on disk to the final fitted
temperature and spectrum.

Stages (in execution order)
----------------------------
LOAD        Read SPE / H5 file from disk → populates data_img_file, _data_img,
            x_calibration, and stores the image on each SingleTemperatureModel.
DATA_SPEC   Extract data spectrum from the ROI (± background subtraction).
CALIB_SPEC  Extract calibration spectrum from the calibration ROI.
CORRECT     Divide data by detector response; apply optional FFT fringe filter
            → corrected_spectrum, response, fringe_frequency, fringe_nd_um.
FIT         Fit Planck / Wien blackbody to corrected spectrum
            → temperature, temperature_error, fit_spectrum.

Entry-point mapping
--------------------
Trigger                         | from_stage   | sides
New data file                   | LOAD         | both
Frame navigation (multi-frame)  | DATA_SPEC    | both
ROI / background-ROI change     | DATA_SPEC    | one
Calibration file load           | CALIB_SPEC   | one
Calibration modus / temperature | CORRECT      | one
Interference filter toggle/freq | CORRECT      | one
Fit-function change (Wien/Plank)| FIT          | both

Usage
------
    from .temperature_pipeline import pipeline, Stage

    # Full pipeline from disk:
    pipeline.run(conf, Stage.LOAD, filepath)

    # Re-extract and re-fit after DS ROI drag:
    pipeline.run_ds(conf, Stage.DATA_SPEC)

    # Re-correct and re-fit after DS filter toggle:
    pipeline.run_ds(conf, Stage.CORRECT)

    # Both sides, re-fit only (fit-function change):
    pipeline.run(conf, Stage.FIT)

No signals are emitted here — the caller remains responsible for that.
"""

from enum import IntEnum


class Stage(IntEnum):
    LOAD       = 0   # File I/O: read SPE/H5 → data_img_file, _data_img
    DATA_SPEC  = 1   # Extract data spectrum from ROI (+ background subtraction)
    CALIB_SPEC = 2   # Extract calibration spectrum from ROI
    CORRECT    = 3   # Divide data by detector response; optional FFT fringe filter
    FIT        = 4   # Planck/Wien fit → temperature, temperature_error, fit_spectrum


class TemperaturePipeline:
    """
    Explicit, ordered pipeline for temperature calculation.

    All computation passes through one of the three entry methods below.
    No side effects beyond updating state on the TemperatureModelConfiguration
    and its SingleTemperatureModel children.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, conf, from_stage: Stage = Stage.LOAD, filepath: str = None):
        """Run both DS and US from *from_stage* through FIT.

        In single-sided mode (conf.mode == 'single') the US-side stages are
        skipped so we don't waste work on a spectrum the user has hidden.
        """
        dual = getattr(conf, 'mode', 'dual') == 'dual'
        if from_stage <= Stage.LOAD:
            self._stage_load(conf, filepath)
        if from_stage <= Stage.DATA_SPEC:
            conf.ds_temperature_model._update_data_spectrum()
            if dual:
                conf.us_temperature_model._update_data_spectrum()
        if from_stage <= Stage.CALIB_SPEC:
            conf.ds_temperature_model._update_calibration_spectrum()
            if dual:
                conf.us_temperature_model._update_calibration_spectrum()
        if from_stage <= Stage.CORRECT:
            conf.ds_temperature_model._update_corrected_spectrum()
            if dual:
                conf.us_temperature_model._update_corrected_spectrum()
        if from_stage <= Stage.FIT:
            conf.ds_temperature_model.fit_data()
            if dual:
                conf.us_temperature_model.fit_data()

    def run_ds(self, conf, from_stage: Stage = Stage.DATA_SPEC):
        """Run DS only from *from_stage* through FIT."""
        self._run_one(conf.ds_temperature_model, from_stage)

    def run_us(self, conf, from_stage: Stage = Stage.DATA_SPEC):
        """Run US only from *from_stage* through FIT. No-op in single-sided mode."""
        if getattr(conf, 'mode', 'dual') == 'single':
            return
        self._run_one(conf.us_temperature_model, from_stage)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _stage_load(self, conf, filepath):
        """Stage LOAD: file I/O — populates data_img_file, _data_img, x_calibration."""
        conf._load_raw_data(filepath)

    def _run_one(self, model, from_stage: Stage):
        if from_stage <= Stage.DATA_SPEC:
            model._update_data_spectrum()
        if from_stage <= Stage.CALIB_SPEC:
            model._update_calibration_spectrum()
        if from_stage <= Stage.CORRECT:
            model._update_corrected_spectrum()
        if from_stage <= Stage.FIT:
            model.fit_data()


# Module-level singleton — import and use directly.
pipeline = TemperaturePipeline()
