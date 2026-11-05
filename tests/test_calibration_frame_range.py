"""Regression: the calibration-stack frame range (`calibration_frames`).

Covers the four defects cleaned up in this pass:
  * a range past the end of a shorter stack used to slice empty and make
    np.mean produce an all-NaN calibration image (that side then never fit);
  * the range is 1-based inclusive, matching the frame navigator — it used to
    be applied 0-based, so "1,1" picked the *second* frame;
  * it now round-trips through the .trs instead of silently reverting to
    "average all frames" on reload;
  * clearing the calibration drops it.

Run from the repo root:  python3 tests/test_calibration_frame_range.py
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyradiant.model.TemperatureModelConfiguration import TemperatureModelConfiguration

CAL_DIR = ('/Users/hrubiak/Library/CloudStorage/Box-Box/0 File pass/LH/'
           'Calibrations')
LEGACY_TRS = os.path.join(CAL_DIR, '20250915_Pt-wire_calibration_PIMAX4.trs')

XCAL = np.linspace(612.7487360596731, 787.1524568011935, 1024)

_results = []


def check(condition, label, detail=''):
    _results.append(bool(condition))
    print(f'[{"ok" if condition else "FAIL"}] {label}'
          + (f'\n         {detail}' if detail and not condition else ''))


class CalStub:
    """Stand-in for a multi-frame SpeFile used as a calibration image."""

    def __init__(self, img, name='cal.spe'):
        self.img = img
        self.x_calibration = XCAL
        self.filename = name
        self.num_frames = img.shape[0] if img.ndim == 3 else 1


def cal_stack(n_frames, height=64, width=1024):
    """Each frame is a constant plane equal to its 1-based frame number, so
    the mean of a selection is just the mean of the selected numbers."""
    stack = np.empty((n_frames, height, width), dtype=np.float64)
    for f in range(n_frames):
        stack[f, :, :] = f + 1
    return stack


def fresh_config_with_cal(stack, frames=(None, None)):
    cfg = TemperatureModelConfiguration()
    cfg.ds_calibration_img_file = CalStub(stack)
    cfg.ds_calibration_filename = 'cal.spe'
    cfg.set_ds_calibration_frames(*frames)
    cfg.ds_set_calibration_data()
    return cfg


def applied_mean(cfg):
    """The constant value of the averaged DS calibration image."""
    return float(np.asarray(cfg.ds_temperature_model.calibration_img)[0, 0])


def test_one_based():
    cfg = fresh_config_with_cal(cal_stack(5), frames=(1, 1))
    check(applied_mean(cfg) == 1.0,
          '"1,1" selects the FIRST frame (1-based)',
          f'got frame value {applied_mean(cfg)}, expected 1.0')
    check(cfg.ds_temperature_model.calibration_frames_applied == (1, 1),
          'applied range reported as (1, 1)')

    cfg = fresh_config_with_cal(cal_stack(5), frames=(2, 4))
    check(applied_mean(cfg) == 3.0,
          '"2,4" averages frames 2,3,4',
          f'got {applied_mean(cfg)}, expected 3.0')


def test_all_frames_default():
    cfg = fresh_config_with_cal(cal_stack(5))
    check(applied_mean(cfg) == 3.0,
          'unset range averages all 5 frames',
          f'got {applied_mean(cfg)}, expected 3.0')
    check(cfg.ds_temperature_model.calibration_frames_applied is None,
          'unset range reports applied=None')


def test_out_of_range_falls_back():
    # The reported failure mode: a range left over from a 32-frame kinetics
    # cal applied to a 5-frame stack.
    cfg = fresh_config_with_cal(cal_stack(5), frames=(10, 20))
    img = np.asarray(cfg.ds_temperature_model.calibration_img)
    check(np.isfinite(img).all(),
          'range entirely past the stack does not produce a NaN cal image')
    check(applied_mean(cfg) == 3.0,
          'range entirely past the stack falls back to all frames',
          f'got {applied_mean(cfg)}, expected 3.0')
    check(cfg.ds_temperature_model.calibration_frames_applied is None,
          'fallback is reported as applied=None')

    # Partially past the end: clamp to what exists.
    cfg = fresh_config_with_cal(cal_stack(5), frames=(4, 99))
    check(applied_mean(cfg) == 4.5,
          'range overhanging the end clamps to frames 4,5',
          f'got {applied_mean(cfg)}, expected 4.5')
    check(cfg.ds_temperature_model.calibration_frames_applied == (4, 5),
          'clamped range is visible as (4, 5)',
          f'got {cfg.ds_temperature_model.calibration_frames_applied}')


def test_reversed_and_single_frame():
    cfg = fresh_config_with_cal(cal_stack(5), frames=(4, 2))
    check(applied_mean(cfg) == 3.0,
          'reversed ends are swapped, not sliced empty',
          f'got {applied_mean(cfg)}, expected 3.0')

    # 2D single-frame cal: the range is meaningless and must be ignored.
    cfg = fresh_config_with_cal(np.full((64, 1024), 7.0), frames=(3, 4))
    check(applied_mean(cfg) == 7.0, 'single-frame cal ignores the range')
    check(cfg.ds_temperature_model.calibration_frames_applied is None,
          'single-frame cal reports applied=None')


def test_trs_roundtrip():
    stack = cal_stack(5)
    cfg = fresh_config_with_cal(stack, frames=(2, 4))
    # save_setting needs a standard spectrum on both sides.
    for m in (cfg.ds_temperature_model, cfg.us_temperature_model):
        m.calibration_parameter.standard_file_name = 'none'
    before = applied_mean(cfg)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'roundtrip.trs')
        cfg.save_setting(path)
        reloaded = TemperatureModelConfiguration()
        reloaded.load_setting(path)

    check(reloaded.ds_temperature_model.calibration_frames == [2, 4],
          '.trs round-trip restores the frame range',
          f'got {reloaded.ds_temperature_model.calibration_frames}')
    after = float(np.asarray(reloaded.ds_temperature_model.calibration_img)[0, 0])
    check(after == before,
          '.trs round-trip reproduces the same calibration image',
          f'got {after}, expected {before}')


def test_clear_drops_the_range():
    cfg = fresh_config_with_cal(cal_stack(5), frames=(2, 4))
    cfg.clear_ds_calibration_image()
    check(cfg.ds_temperature_model.calibration_frames == [None, None],
          'clearing the calibration drops the frame range')
    check(cfg.ds_temperature_model.calibration_frames_applied is None,
          'clearing the calibration clears the applied range')


def test_legacy_trs_unchanged():
    if not os.path.exists(LEGACY_TRS):
        print('[skip] legacy .trs not available:', LEGACY_TRS)
        return
    cfg = TemperatureModelConfiguration()
    cfg.load_setting(LEGACY_TRS)
    check(cfg.ds_temperature_model.calibration_frames == [None, None],
          'legacy .trs (no attrs) still means "average all frames"',
          f'got {cfg.ds_temperature_model.calibration_frames}')
    # The Pt-wire cal is a 5-frame stack; all frames averaged.
    expected = np.mean(np.asarray(cfg.ds_calibration_img_file.img), axis=0)
    check(np.allclose(cfg.ds_temperature_model.calibration_img, expected),
          'legacy .trs calibration image is the full-stack average')


def main():
    np.random.seed(0)
    test_one_based()
    test_all_frames_default()
    test_out_of_range_falls_back()
    test_reversed_and_single_frame()
    test_trs_roundtrip()
    test_clear_drops_the_range()
    test_legacy_trs_unchanged()
    ok = all(_results)
    print('\nPASS' if ok else '\nFAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
