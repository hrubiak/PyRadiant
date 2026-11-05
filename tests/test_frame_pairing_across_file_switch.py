"""Regression: per-side readout indices must not survive a data-file switch.

Bug: a kinetics configuration (q_ds=4, q_us=11) puts DS and US on readout
frames 7 apart via the sync-frame axis. Opening a NON-kinetics file in the
same configuration used to keep those stale indices, and
_rebuild_records_cache's finally block re-applied them to the new file on
every rebuild — pinning US to a frame DS wasn't reading (no fit), or, when
the index fell outside the new file's frame count, feeding US a NaN blank.
Loading a different .trs could not clear it; only an app restart did.

Run from the repo root:  python3 tests/test_frame_pairing_across_file_switch.py

Uses the two real .trs files from the original report when present; skips
otherwise. No app / Qt event loop required.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyradiant.model.TemperatureModelConfiguration import TemperatureModelConfiguration

CAL_DIR = ('/Users/hrubiak/Library/CloudStorage/Box-Box/0 File pass/LH/'
           'Calibrations')
KINETICS_TRS = os.path.join(CAL_DIR, '20250220-PIMAX4-kinetic.trs')
FULLCHIP_TRS = os.path.join(CAL_DIR, '20250915_Pt-wire_calibration_PIMAX4.trs')

# Matches the x_calibration embedded in both .trs files.
XCAL = np.linspace(612.7487360596731, 787.1524568011935, 1024)


class StubReader:
    """Minimal stand-in for SpeFile — only what the config actually reads.

    Fed to the config through load_data_image's area-detector branch so the
    real _load_raw_data runs (no file I/O, no SPE parsing).
    """

    def __init__(self, img, kinetics=False, window_height=64, name='stub.spe'):
        self.img = img
        self.x_calibration = XCAL
        self.filename = name
        self.num_frames = img.shape[0] if img.ndim == 3 else 1
        self.readout_mode = 'Kinetics' if kinetics else 'Normal'
        self.sensor_height = 1024
        self.sensor_width = 1024
        self.kinetics_window_height = window_height if kinetics else 0
        self.kinetics_window_y = 0
        self.exposure_time = 1.0
        self.grating = 'test'
        self.detector = 'PIMAX4'

    def get_dimension(self):
        shape = self.img.shape
        return (shape[-1], shape[-2]) if self.img.ndim == 3 else (shape[1], shape[0])

    def update_data(self):
        pass


def load_data(cfg, reader):
    """Load `reader` through the real _load_raw_data (area-detector branch)."""
    cfg.load_data_image(reader.filename, area_detector=reader)


def process_multiframe(cfg):
    """Mirror TemperatureController.process_multiframe for a 'frame' axis.

    The controller's _reapply_history_axis_pairing is a no-op outside the
    synced axes, which is the state this scenario ends up in.
    """
    if cfg.data_img_file is not None and cfg.data_img_file.num_frames > 1:
        cfg.fit_all_frames()


def kinetics_stack(n_strips=32):
    img = np.random.poisson(80, (n_strips, 64, 1024)).astype(np.uint16)
    img[:, 10:22, :] += 4000   # DS band, per the kinetics .trs ROI
    img[:, 26:38, :] += 4000   # US band
    return img


def fullchip_stack(n_frames, hot_frames):
    img = np.random.poisson(80, (n_frames, 1024, 1024)).astype(np.uint16)
    for f in hot_frames:
        img[f, 265:282, :] += 4000   # DS band, per the Pt-wire .trs ROI
        img[f, 726:745, :] += 4000   # US band
    return img


def run_case(n_frames, hot_frames, label):
    normal = fullchip_stack(n_frames, hot_frames)

    # --- the reported sequence, in one configuration ----------------------
    cfg = TemperatureModelConfiguration()
    cfg.load_setting(KINETICS_TRS)
    load_data(cfg, StubReader(kinetics_stack(), kinetics=True, name='kin.spe'))
    process_multiframe(cfg)
    # Loading a kinetics file auto-enables the sync-frame axis; the user
    # navigates to a coincident exposure, splitting DS/US by q_us - q_ds.
    f_ds, f_us = cfg.coincident_to_readout(8)
    cfg.set_img_frame_numbers(f_ds, f_us)
    assert f_ds != f_us, 'kinetics .trs should split DS/US (q_ds=4, q_us=11)'

    load_data(cfg, StubReader(normal, name='normal.spe'))
    process_multiframe(cfg)
    cfg.load_setting(FULLCHIP_TRS)
    process_multiframe(cfg)

    # --- the same end state reached by restarting the app -----------------
    fresh = TemperatureModelConfiguration()
    fresh.load_setting(FULLCHIP_TRS)
    load_data(fresh, StubReader(normal, name='normal.spe'))
    process_multiframe(fresh)

    failures = []
    if cfg.current_frame_ds != cfg.current_frame_us:
        failures.append(f'DS/US readout split survived the file switch: '
                        f'ds={cfg.current_frame_ds} us={cfg.current_frame_us}')
    for side in ('ds', 'us'):
        got = getattr(cfg, f'{side}_temperature_model').temperature
        want = getattr(fresh, f'{side}_temperature_model').temperature
        if not np.isfinite(got) or got <= 0:
            failures.append(f'{side.upper()} did not fit: T={got}')
        elif not np.isclose(got, want, rtol=0.05):
            failures.append(f'{side.upper()} T={got:.1f} differs from the '
                            f'restart result T={want:.1f}')

    status = 'FAIL' if failures else 'ok'
    print(f'[{status}] {label}: ds_f={cfg.current_frame_ds} '
          f'us_f={cfg.current_frame_us} '
          f'DS={cfg.ds_temperature_model.temperature:.1f} '
          f'US={cfg.us_temperature_model.temperature:.1f} '
          f'(restart: DS={fresh.ds_temperature_model.temperature:.1f} '
          f'US={fresh.us_temperature_model.temperature:.1f})')
    for f in failures:
        print(f'         {f}')
    return not failures


def main():
    missing = [p for p in (KINETICS_TRS, FULLCHIP_TRS) if not os.path.exists(p)]
    if missing:
        print('SKIP — calibration .trs files not available:')
        for p in missing:
            print('   ', p)
        return 0

    np.random.seed(0)
    ok = all([
        run_case(5, range(5), 'stale US index out of range (5 frames)'),
        run_case(20, range(20), 'stale US index in range, all frames hot'),
        run_case(20, range(6), 'stale US index in range, only frames 0-5 hot'),
    ])
    print('\nPASS' if ok else '\nFAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
