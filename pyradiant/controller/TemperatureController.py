# -*- coding: utf8 -*-
# PyRadiant - GUI program for analysis of thermal spectra during
# laser heated diamond anvil cell experiments
# Copyright (C) 2024 Ross Hrubiak (hrubiak@anl.gov)
# High Pressure Collaborative Access Team, Argonne National Laboratory
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
from datetime import datetime

from PyQt6 import QtWidgets, QtCore, QtGui

from ..widget.TemperatureWidget import TemperatureWidget, SetupEpicsDialog
from ..widget.ConfigurationWidget import ConfigurationWidget
from ..widget.Widgets import open_file_dialog, open_files_dialog, save_file_dialog
from ..model.TemperatureModel import TemperatureModel
from ..model.helper.FileNameIterator import get_file_and_extension
from ..model import epics_settings as eps
from .NewFileInDirectoryWatcher import NewFileInDirectoryWatcher
from ..model.data_models.ADWatcher import ADWatcher
from ..model.data_models.SpeFile import SpeFile
from ..model.data_models.H5File import H5File
import numpy as np
from ..model.helper.HelperModule import get_partial_index , get_partial_value
from .. widget.DataHistoryWidget import dataHistoryWidget
from ..model.helper.AppSettings import AppSettings
from natsort import natsorted
import json

from .. import EPICS_INSTALLED
if EPICS_INSTALLED:
    from epics import caput, camonitor, camonitor_clear, caget, PV
else:
    caput = None
    camonitor = None
    camonitor_clear = None
    caget = None
    PV = None

from .ZmqWorkerController import ZmqWorkerController
from .ZmqPublisherController import ZmqPublisherController


class TemperatureController(QtCore.QObject):

    temperature_folder_changed = QtCore.pyqtSignal()
    epics_datalog_file_changed = QtCore.pyqtSignal(str)

    def __init__(self, temperature_widget: TemperatureWidget, model: TemperatureModel, data_history_widget:dataHistoryWidget):
        """
        :param temperature_widget: reference to the temperature widget
        :type temperature_widget: TemperatureWidget
        :param temperature_model: reference to the global temperature model
        :type model: TemperatureModel
        :return:
        """
        super(TemperatureController, self).__init__()
        #self.widget: TemperatureWidget
        self.widget = temperature_widget
        #self.model: TemperatureModel
        self.model = model

        self.max_allowed_T = 20000
        self.min_allowed_T = 500

        # History-plot x-axis mode: False = frame index (1..N), True = seconds
        # relative to the first kinetics frame (i·exposure_time). Toggled by
        # the "Lab time" button; not persisted.
        # 'frame' (raw 1..N), 'time' (seconds, DS/US synced by exposure), or
        # 'sync_frame' (coincident-exposure frame index, DS/US synced).
        self._history_x_mode = 'frame'
        # Coincident-exposure index currently displayed in synced modes.
        # Kept in sync with the model's per-side readout frames.
        self._current_coincident_k = 1

        #self.data_history_widget: dataHistoryWidget
        self.data_history_widget = data_history_widget
        self.data_history_widget.setWindowTitle('Temperature Log')
        self.data_history_widget.temperatures_plot_widget.update_time_lapse_ds_temperature_txt('Downstream')
        self.data_history_widget.temperatures_plot_widget.update_time_lapse_us_temperature_txt('Upstream')

        self._exp_working_dir = ''

        # Per-purpose "last-used directory" memory. Each file-picker gets its
        # own key so loading a calibration doesn't clobber the data-file dir
        # and vice versa. Persisted in load_settings/save_settings as JSON.
        # Purposes in use: data, ds_cal, us_cal, ds_standard, us_standard,
        # wavelength_cal, dark_ds, dark_us, cal_dark_ds, cal_dark_us,
        # save_data, save_graph, import_slots.
        self._last_dirs = {}



        self.live_data = False # this is True when AD checkbox is checked and an area detector connection is established, otherwise it's False
        self._AD_watcher = None
        self.epics_available = False
        

        self.widget.frame_num_txt.clearFocus()
        self.widget.load_data_file_btn.setFocus()

        
        self._create_autoprocess_system()

        self.zmq_worker_controller = ZmqWorkerController(self.widget)
        self.zmq_worker_controller.temperature_controller = self
        self.zmq_publisher_controller = ZmqPublisherController(self.widget)

        self.create_signals()

        # File system is default: AD checkbox starts disabled until live stream mode is selected
        self.widget.connect_to_ad_cb.setEnabled(False)
        self.widget.connect_to_ad_cb.setChecked(False)

        if not EPICS_INSTALLED:
            self.widget.connect_to_epics_cb.setEnabled(False)
            self.widget.connect_to_epics_cb.setChecked(False)
            self.widget.monitor_folder_cb.setEnabled(False)
            self.widget.monitor_folder_cb.setChecked(False)
            self.widget.connect_to_ad_cb.setEnabled(False)
            self.widget.connect_to_ad_cb.setChecked(False)
            self.widget.live_stream_rb.setEnabled(False)
            
        # testing only: remove in production
        self.setup_epics_datalog_file_monitor()
        self.widget.connect_to_epics_datalog_cb.setChecked(True)

    def connect_epics(self):
        if not hasattr(self, 'setup_epics_dialog'):
            if EPICS_INSTALLED:
                self.setup_epics_dialog = SetupEpicsDialog(self.widget)
            else:
                self.widget.epics_gb.hide()

        self.epics_available = False

        if EPICS_INSTALLED:
            if self.check_pv(eps.epics_settings['T_folder']):
                self.epics_available = True
                self.widget.epics_publish_indicator.set_active()
            else:
                self.widget.connect_to_epics_cb.setChecked(False)
                self.widget.epics_publish_indicator.set_error()
                self.widget.show_error_dialog("Couldn't connect to EPICS.", "Connection Error")

    def disconnect_epics(self):
        self.epics_available = False
        self.widget.epics_publish_indicator.set_inactive()

    def connect_folder_monitor(self):
        if EPICS_INSTALLED:
            if self.check_pv(eps.epics_settings['T_folder']):
                self.setup_temperature_file_folder_monitor()
                self.widget.monitor_folder_indicator.set_active()
                folder_path = caget(eps.epics_settings['T_folder'], as_string=True) or ''
                self.widget.monitor_folder_path_lbl.setText(folder_path)
            else:
                self.widget.monitor_folder_cb.setChecked(False)
                self.widget.monitor_folder_indicator.set_error()
                self.widget.show_error_dialog("Couldn't connect to EPICS folder monitor.", "Connection Error")

    def disconnect_folder_monitor(self):
        if eps.epics_settings['T_folder'] is not None \
                and eps.epics_settings['T_folder'] != 'None':
            # DEBUG: try/except removed so failures raise with full traceback.
            camonitor_clear(eps.epics_settings['T_folder'])
        self.widget.monitor_folder_indicator.set_inactive()
        self.widget.monitor_folder_path_lbl.setText("")

    def connect_to_area_detector(self):

        if EPICS_INSTALLED:
            ad_on = self.widget.connect_to_ad_cb.isChecked()
            if ad_on:
                if self._AD_watcher is None:
                    x_cal = self.model.current_configuration.x_calibration
                    self._AD_watcher = ADWatcher(record_name=eps.epics_settings['area_detector'], x_calibration=x_cal)
                    if self._AD_watcher.initialized:
                        self._AD_watcher.file_added.connect(self.load_data_file_ad)
                        self._AD_watcher.activate()
                        self.widget.ad_indicator.set_active()
                    else:
                        self.widget.show_error_dialog("Couldn't connect to Area Detector.", "Connection Error")
                        self.widget.connect_to_ad_cb.setChecked(False)
                        self.widget.ad_indicator.set_error()
                        self._AD_watcher = None

    def disconnect_from_area_detector(self):
        if self._AD_watcher != None:
            if self._AD_watcher.initialized:
                self._AD_watcher.deactivate()
                self._AD_watcher.file_added.disconnect(self.load_data_file_ad)
                self._AD_watcher = None
        self.widget.ad_indicator.set_inactive()
        self.widget.ad_last_update_lbl.setText("")

    # Map purpose -> callable(cfg) that returns the filename most
    # recently loaded for that purpose (or None). Callables let us reach
    # into nested attributes (e.g. cfg.ds_temperature_model.standard_file_name)
    # or dict entries (photron_wavelength_calibration['source_filename'])
    # without a special-case switch in _cfg_dir_for_purpose.
    _PURPOSE_CFG_FILE_GETTERS = {
        'data':           lambda c: getattr(c.data_img_file, 'filename', None) if c.data_img_file else None,
        'ds_cal':         lambda c: getattr(c.ds_calibration_img_file, 'filename', None) if c.ds_calibration_img_file else None,
        'us_cal':         lambda c: getattr(c.us_calibration_img_file, 'filename', None) if c.us_calibration_img_file else None,
        'dark_ds':        lambda c: c.ds_dark_frame_filename,
        'dark_us':        lambda c: c.us_dark_frame_filename,
        'cal_dark_ds':    lambda c: c.ds_cal_dark_frame_filename,
        'cal_dark_us':    lambda c: c.us_cal_dark_frame_filename,
        'wavelength_cal': lambda c: (c.photron_wavelength_calibration or {}).get('source_filename'),
        'ds_standard':    lambda c: getattr(getattr(c, 'ds_temperature_model', None), 'standard_file_name', None),
        'us_standard':    lambda c: getattr(getattr(c, 'us_temperature_model', None), 'standard_file_name', None),
    }

    # Sentinel string used by SingleTemperatureModel before a standard
    # has been loaded — not an actual file path.
    _STANDARD_UNSET = 'Select File...'

    def _cfg_dir_for_purpose(self, purpose):
        """Return the parent directory of the file currently loaded for
        `purpose` on the active configuration, or '' if none.

        Highest-priority source of truth: the folder the currently-loaded
        file lives in is almost always where the user wants to browse
        next. More accurate than persisted _last_dirs (global across
        configs, loaded in restore order) or _exp_working_dir (whichever
        data file was loaded most recently)."""
        getter = self._PURPOSE_CFG_FILE_GETTERS.get(purpose)
        if getter is None:
            return ''
        cfg = self.model.current_configuration
        if cfg is None:
            return ''
        try:
            fname = getter(cfg)
        except AttributeError:
            return ''
        if not fname or fname == self._STANDARD_UNSET:
            return ''
        d = os.path.dirname(str(fname))
        return d if d and os.path.isdir(d) else ''

    def _last_dir_for(self, purpose):
        """Starting directory for a file-picker of the given purpose.
        Priority: (1) directory of the file currently loaded for this
        purpose in the active config, (2) purpose-specific persisted
        memory, (3) global _exp_working_dir, (4) OS default ('')."""
        d = self._cfg_dir_for_purpose(purpose)
        if d:
            return d
        d = self._last_dirs.get(purpose, '')
        if d and os.path.isdir(d):
            return d
        if self._exp_working_dir and os.path.isdir(self._exp_working_dir):
            return self._exp_working_dir
        return ''

    def _remember_dir(self, purpose, path):
        """Record the parent directory of `path` under `purpose`. Idempotent."""
        if not path:
            return
        d = os.path.dirname(str(path))
        if d:
            self._last_dirs[purpose] = d

    def create_signals(self):
        # File signals
        self.connect_click_function(self.widget.load_data_file_btn, self.load_data_file)
        self.widget.load_next_data_file_btn.clicked.connect(self.load_next_data_image)
        self.widget.load_previous_data_file_btn.clicked.connect(self.load_previous_data_image)
        # Left/Right arrow shortcuts mirror the previous/next file buttons.
        # WindowShortcut context: fires when the temperature widget's window
        # is active. Spinboxes use up/down (not left/right) for increment,
        # so no conflict with numeric editing.
        self._prev_file_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key.Key_Left), self.widget)
        self._prev_file_shortcut.setContext(
            QtCore.Qt.ShortcutContext.WindowShortcut)
        self._prev_file_shortcut.activated.connect(self.load_previous_data_image)
        self._next_file_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key.Key_Right), self.widget)
        self._next_file_shortcut.setContext(
            QtCore.Qt.ShortcutContext.WindowShortcut)
        self._next_file_shortcut.activated.connect(self.load_next_data_image)
        self.widget.browse_by_name_rb.clicked.connect(self.toggle_browse_mode)
        self.widget.browse_by_time_rb.clicked.connect(self.toggle_browse_mode)
        self.widget.load_next_frame_btn.clicked.connect(self.load_next_img_frame_callback)
        self.widget.load_previous_frame_btn.clicked.connect(self.load_previous_img_frame_callback)
        self.widget.frame_num_txt.editingFinished.connect( self.frame_num_txt_callback)
        self.widget.lab_time_btn.toggled.connect(self.lab_time_btn_toggled)
        self.widget.sync_frame_btn.toggled.connect(self.sync_frame_btn_toggled)
        self.widget.autoprocess_cb.toggled.connect(self.auto_process_cb_toggled)

        self.connect_click_function(self.widget.save_data_btn, self.save_data_btn_clicked)
        self.connect_click_function(self.widget.save_graph_btn, self.save_graph_btn_clicked)

        self.temperature_folder_changed.connect(self.temperature_folder_changed_emitted)
        self.epics_datalog_file_changed.connect(self.epics_datalog_file_changed_emitted)

        # File drag and drop
        self.widget.file_dragged_in.connect(self.file_dragged_in) 

        # Calibration signals
        self.connect_click_function(self.widget.load_ds_calibration_file_btn, self.load_ds_calibration_file)
        self.connect_click_function(self.widget.load_us_calibration_file_btn, self.load_us_calibration_file)
        self.connect_click_function(self.widget.clear_ds_calibration_file_btn, self.clear_ds_calibration_file)
        self.connect_click_function(self.widget.clear_us_calibration_file_btn, self.clear_us_calibration_file)

        self.connect_click_function(self.widget.load_wavelength_calibration_btn, self.load_wavelength_calibration_file)
        self.connect_click_function(self.widget.clear_wavelength_calibration_btn, self.clear_wavelength_calibration)

        self.widget.us_calibration_start_frame.editingFinished.connect(self.us_calibration_frame_range_callback)
        self.widget.ds_calibration_start_frame.editingFinished.connect(self.ds_calibration_frame_range_callback)
        self.widget.us_calibration_end_frame.editingFinished.connect(self.us_calibration_frame_range_callback)
        self.widget.ds_calibration_end_frame.editingFinished.connect(self.ds_calibration_frame_range_callback)

        self.widget.ds_standard_rb.toggled.connect(self.model.current_configuration.set_ds_calibration_modus)
        self.widget.us_standard_rb.toggled.connect(self.model.current_configuration.set_us_calibration_modus)

        self.connect_click_function(self.widget.ds_load_standard_file_btn, self.load_ds_standard_file)
        self.connect_click_function(self.widget.us_load_standard_file_btn, self.load_us_standard_file)

        self.connect_click_function(self.widget.ds_save_standard_file_btn, self.save_ds_standard_file)
        self.connect_click_function(self.widget.us_save_standard_file_btn, self.save_us_standard_file)

        self.widget.ds_temperature_txt.editingFinished.connect(self.ds_temperature_txt_changed)
        self.widget.us_temperature_txt.editingFinished.connect(self.us_temperature_txt_changed)

        self.widget.temperature_function_plank_rb.clicked.connect(self.temperature_function_callback)
        self.widget.temperature_function_wien_rb.clicked.connect(self.temperature_function_callback)
        self.widget.t_function_type_section.error_limit_sb.valueChanged.connect(self.error_limit_changed)

        self.widget.dual_mode_rb.toggled.connect(self._measurement_mode_changed)
        self.widget.single_mode_rb.toggled.connect(self._measurement_mode_changed)

        self.widget.ds_interference_filter_cb.clicked.connect(self.filter_setting_callback)
        self.widget.us_interference_filter_cb.clicked.connect(self.filter_setting_callback)
        self.widget.save_filtered_cb.clicked.connect(self.save_filtered_callback)
        self.widget.filter_freq_min_sb.valueChanged.connect(self.filter_freq_range_callback)
        self.widget.filter_freq_max_sb.valueChanged.connect(self.filter_freq_range_callback)

        # Setting signals
        self.connect_click_function(self.widget.load_setting_btn, self.load_setting_file)
        self.connect_click_function(self.widget.save_setting_btn, self.save_setting_file)
        self.connect_click_function(self.widget.import_slots_btn, self.import_slots_from_trs)
        self.widget.kinetics_gb.mode_combo.currentIndexChanged.connect(
            self._kinetics_mode_combo_changed)
        self.widget.camera_mode_gb.mode_combo.currentIndexChanged.connect(
            self._camera_mode_changed)
        self.widget.settings_cb.currentIndexChanged.connect(self.settings_cb_changed)
        self.widget.setup_epics_pb.clicked.connect(self.setup_epics_pb_clicked)

        # model signals
        self.model.data_changed_signal.connect(self.data_changed_signal_callback)
        self.model.ds_calculations_changed.connect(self.ds_calculations_changed)
        self.model.us_calculations_changed.connect(self.us_calculations_changed)

        

        self.widget.roi_widget.rois_changed.connect(self.widget_rois_changed)
        self.widget.roi_widget.wl_range_changed.connect(self.widget_wl_range_changed_callback)
        self.widget.roi_widget.cal_signal_roi_changed.connect(
            self.cal_signal_roi_dragged)
        self.widget.roi_widget.cal_bg_roi_changed.connect(
            self.cal_bg_roi_dragged)
        # Main-panel BG edits in cross-mode are cal-dim edits (the main
        # panel shows cal-dim bg when a full-chip cal is present with
        # kinetics data). Route them to the same cal-dim setter used by
        # the cal-viewer drag handler.
        self.widget.roi_widget.main_bg_edit.connect(
            self.cal_bg_roi_dragged)
        # Numeric cal-ROI panel edits route through the same cal-dim
        # setters as the 2D viewer drags.
        self.widget.cal_roi_panel.signal_edited.connect(
            self.cal_signal_roi_dragged)
        self.widget.cal_roi_panel.bg_edited.connect(
            self.cal_bg_roi_dragged)

        # mouse moved signals
        self.widget.temperature_spectrum_widget.mouse_moved.connect(self.graph_mouse_moved)
        self.widget.roi_widget.img_widget.mouse_moved.connect(self.roi_mouse_moved)

        # data hitory signals
        self.connect_click_function(self.widget.data_history_btn, self.data_history_btn_callback)
        

        # epics stuff
        self.widget.file_system_rb.toggled.connect(self.data_source_mode_changed)
        self.widget.connect_to_ad_cb.clicked.connect(self.connect_to_ad_cb_callback)
        self.widget.connect_to_epics_cb.clicked.connect(self.connect_to_epics_cb_callback)
        self.widget.monitor_folder_cb.clicked.connect(self.connect_to_monitor_folder_cb_callback)

        # Background subtraction (mode + prerecorded dark)
        self.widget.background_mode_combo.currentIndexChanged.connect(self._background_mode_changed)
        self.connect_click_function(self.widget.load_ds_dark_btn, self.load_ds_dark_file)
        self.connect_click_function(self.widget.load_us_dark_btn, self.load_us_dark_file)
        self.connect_click_function(self.widget.clear_ds_dark_btn, self.clear_ds_dark_file)
        self.connect_click_function(self.widget.clear_us_dark_btn, self.clear_us_dark_file)
        self.widget.ds_dark_scale_sb.valueChanged.connect(self._ds_dark_scale_changed)
        self.widget.us_dark_scale_sb.valueChanged.connect(self._us_dark_scale_changed)

        # Photron-only decoupled cal-bg controls
        cal_gb = self.widget.cal_background_subtraction_gb
        cal_gb.mode_combo.currentIndexChanged.connect(self._cal_background_mode_changed)
        self.connect_click_function(cal_gb.load_ds_dark_btn, self.load_ds_cal_dark_file)
        self.connect_click_function(cal_gb.load_us_dark_btn, self.load_us_cal_dark_file)
        self.connect_click_function(cal_gb.clear_ds_dark_btn, self.clear_ds_cal_dark_file)
        self.connect_click_function(cal_gb.clear_us_dark_btn, self.clear_us_cal_dark_file)
        cal_gb.ds_dark_scale_sb.valueChanged.connect(
            lambda v: self._cal_bg_scale_changed('ds', v))
        cal_gb.us_dark_scale_sb.valueChanged.connect(
            lambda v: self._cal_bg_scale_changed('us', v))

        self.widget.two_color_btn.clicked.connect(self.two_color_display_toggle_callback)

        # Multi-frame output panel — mode combobox drives range-row visibility
        # and error-metric enablement; both are shared by ZMQ, EPICS, and the
        # time-lapse plot labels. Any change should refresh the history label
        # so what the user sees on the plot matches what would be published.
        mf_gb = self.widget.multiframe_output_gb
        mf_gb.mode_cb.currentIndexChanged.connect(self._on_multiframe_mode_changed)
        mf_gb.mode_cb.currentIndexChanged.connect(lambda _i: self.redraw_time_lapse())
        mf_gb.error_metric_cb.currentIndexChanged.connect(lambda _i: self.redraw_time_lapse())
        mf_gb.range_start_sb.valueChanged.connect(lambda _v: self.redraw_time_lapse())
        mf_gb.range_end_sb.valueChanged.connect(lambda _v: self.redraw_time_lapse())
        # epicsLogger publisher — Trigger-now button (publish checkbox, load_config,
        # connect_btn are wired in ZmqPublisherController).
        self.widget.epicslogger_gb.trigger_now_btn.clicked.connect(
            lambda: self._send_temperature_trigger(force=True))


    def load_next_img_frame_callback(self):
        self._navigate_relative(+1)

    def load_previous_img_frame_callback(self):
        self._navigate_relative(-1)

    def _navigate_relative(self, delta):
        """Move by ±1 in the current axis mode's unit (readout f in 'frame'
        mode, coincident k in 'time' / 'sync_frame' modes)."""
        cfg = self.model.current_configuration
        if self._history_x_mode in ('time', 'sync_frame'):
            rng = cfg.get_coincident_frame_range()
            if rng is None:
                cfg.set_img_frame_number_to(cfg.current_frame + delta)
                self.set_frame_text(str(cfg.current_frame + 1))
                return
            k_min, k_max = rng
            k = self._current_coincident_k + delta
            if k < k_min:
                k = k_min
            elif k > k_max:
                k = k_max
            self._load_coincident_k(k)
        else:
            cfg.set_img_frame_number_to(cfg.current_frame + delta)
            self.set_frame_text(str(cfg.current_frame + 1))

    def _load_coincident_k(self, k):
        """Ask the model to load the readout frames whose union is
        coincident exposure k, then update the frame text."""
        cfg = self.model.current_configuration
        f_ds, f_us = cfg.coincident_to_readout(k)
        cfg.set_img_frame_numbers(f_ds, f_us)
        self._current_coincident_k = int(k)
        self.set_frame_text(str(int(k)))

    def two_color_display_toggle_callback(self):
        self.us_calculations_changed()
        self.ds_calculations_changed()

    def frame_num_txt_callback(self):
        self.widget.frame_num_txt.clearFocus()
        # DEBUG: try/except removed so failures raise with full traceback.
        val = int(self.widget.frame_num_txt.text())
        cfg = self.model.current_configuration
        if self._history_x_mode in ('time', 'sync_frame'):
            rng = cfg.get_coincident_frame_range()
            if rng is not None:
                k_min, k_max = rng
                k = max(k_min, min(k_max, val))
                self._load_coincident_k(k)
                return
        num = val - 1
        if 0 <= num < cfg.data_img_file.num_frames:
            cfg.load_any_img_frame(num)
            self.set_frame_text(str(cfg.current_frame + 1))

    def connect_click_function(self, emitter, function):
        emitter.clicked.connect(function)

    # ---- Background subtraction handlers -------------------------------------
    _BG_MODES_BY_INDEX = ('insitu', 'prerecorded', 'hybrid', 'kinetics_trend', 'off')

    def _background_mode_changed(self, index):
        if not (0 <= index < len(self._BG_MODES_BY_INDEX)):
            return
        mode = self._BG_MODES_BY_INDEX[index]
        cfg = self.model.current_configuration
        # Kinetics-trend requires >1 frame; ignore selection on single-frame
        # data (the entry is disabled but a stale programmatic set may still
        # arrive during config-switch sync).
        if mode == 'kinetics_trend' and not self._kinetics_trend_available(cfg):
            self._sync_background_widgets()
            return
        cfg.set_background_mode(mode)
        gb = self.widget.background_subtraction_gb
        uses_dark = mode in ('prerecorded', 'hybrid')
        gb._set_dark_rows_visible(uses_dark)
        gb.set_scale_spinboxes_enabled(mode == 'prerecorded')
        # Re-hide US row if we're in single-sided measurement mode.
        if getattr(cfg, 'mode', 'dual') == 'single':
            gb.set_us_row_visible(False)
        # Every per-frame T depends on bg subtraction — refit the whole
        # history so the plot matches the current-frame spectrum. No-op
        # for single-frame data.
        self.process_multiframe()

    def _kinetics_trend_available(self, cfg):
        f = getattr(cfg, 'data_img_file', None)
        if f is None:
            return False
        return int(getattr(f, 'num_frames', 0) or 0) > 1

    def _bg_scale_changed(self, side, value):
        cfg = self.model.current_configuration
        if side == 'ds':
            cfg.set_ds_dark_frame_scale(value)
        else:
            cfg.set_us_dark_frame_scale(value)

    def _ds_dark_scale_changed(self, v): self._bg_scale_changed('ds', v)
    def _us_dark_scale_changed(self, v): self._bg_scale_changed('us', v)

    def load_ds_dark_file(self, filename=None):
        self._load_dark_file('ds', filename)

    def load_us_dark_file(self, filename=None):
        self._load_dark_file('us', filename)

    def _load_dark_file(self, side, filename):
        purpose = f'dark_{side}'
        if filename is None or filename is False:
            filename = open_file_dialog(
                self.widget,
                caption=f"Load {side.upper()} dark frame",
                directory=self._last_dir_for(purpose),
                filter="Frames (*.spe *.SPE *.h5 *.tif *.tiff);;All files (*)",
            )
        if not filename:
            return
        self._remember_dir(purpose, filename)
        cfg = self.model.current_configuration
        # DEBUG: try/except removed so failures raise with full traceback.
        if side == 'ds':
            cfg.load_ds_dark_frame(filename)
        else:
            cfg.load_us_dark_frame(filename)
        self._sync_background_widgets()

    def clear_ds_dark_file(self):
        self.model.current_configuration.clear_ds_dark_frame()
        self._sync_background_widgets()

    def clear_us_dark_file(self):
        self.model.current_configuration.clear_us_dark_frame()
        self._sync_background_widgets()

    def _sync_background_widgets(self):
        """Sync mode combo, dark filename labels, and scale spinboxes to model state.

        Called whenever the current config changes or after a load/clear.
        """
        cfg = self.model.current_configuration
        mode = getattr(cfg, 'background_mode', 'insitu')
        # If kinetics_trend is persisted but the current data file has only
        # one frame, downgrade the mode so combo + config stay consistent.
        if mode == 'kinetics_trend' and not self._kinetics_trend_available(cfg):
            cfg.set_background_mode('insitu')
            mode = 'insitu'
        # DEBUG: try/except removed so failures raise with full traceback.
        idx = self._BG_MODES_BY_INDEX.index(mode)
        gb = self.widget.background_subtraction_gb
        # Enable/disable the kinetics_trend entry based on num_frames.
        kt_idx = self._BG_MODES_BY_INDEX.index('kinetics_trend')
        kt_avail = self._kinetics_trend_available(cfg)
        item = gb.mode_combo.model().item(kt_idx)
        if item is not None:
            item.setEnabled(kt_avail)
            item.setToolTip('' if kt_avail else 'Requires kinetics data (>1 frame)')
        # Update combo without re-emitting -> avoid recursion into set_background_mode
        gb.mode_combo.blockSignals(True)
        gb.mode_combo.setCurrentIndex(idx)
        gb.mode_combo.blockSignals(False)
        gb._set_dark_rows_visible(mode in ('prerecorded', 'hybrid'))
        gb.set_scale_spinboxes_enabled(mode == 'prerecorded')
        if getattr(cfg, 'mode', 'dual') == 'single':
            gb.set_us_row_visible(False)
        # DS row content
        ds_name = os.path.basename(cfg.ds_dark_frame_filename) if cfg.ds_dark_frame_filename else 'None'
        gb.ds_dark_filename_lbl.setText(ds_name)
        gb.ds_dark_filename_lbl.setStyleSheet('' if cfg.ds_dark_frame_img is not None else 'color: gray;')
        gb.ds_dark_scale_sb.blockSignals(True)
        gb.ds_dark_scale_sb.setValue(cfg.ds_dark_frame_scale)
        gb.ds_dark_scale_sb.blockSignals(False)
        # US row content
        us_name = os.path.basename(cfg.us_dark_frame_filename) if cfg.us_dark_frame_filename else 'None'
        gb.us_dark_filename_lbl.setText(us_name)
        gb.us_dark_filename_lbl.setStyleSheet('' if cfg.us_dark_frame_img is not None else 'color: gray;')
        gb.us_dark_scale_sb.blockSignals(True)
        gb.us_dark_scale_sb.setValue(cfg.us_dark_frame_scale)
        gb.us_dark_scale_sb.blockSignals(False)

    # ---- Photron-only decoupled cal-bg handlers ------------------------------
    # Index 0 in the cal-bg combo means "shared with data" (cal_background_mode
    # = None). Indices 1..4 map to the same modes as the data-bg combo minus
    # 'kinetics_trend' (calibration is single-frame).
    _CAL_BG_MODES_BY_INDEX = (None, 'insitu', 'prerecorded', 'hybrid', 'off')

    def _cal_background_mode_changed(self, index):
        if not (0 <= index < len(self._CAL_BG_MODES_BY_INDEX)):
            return
        mode = self._CAL_BG_MODES_BY_INDEX[index]
        cfg = self.model.current_configuration
        if cfg is None:
            return
        cfg.set_cal_background_mode(mode)
        gb = self.widget.cal_background_subtraction_gb
        uses_dark = mode in ('prerecorded', 'hybrid')
        gb._set_dark_rows_visible(uses_dark)
        gb.set_scale_spinboxes_enabled(mode == 'prerecorded')
        if getattr(cfg, 'mode', 'dual') == 'single':
            gb.set_us_row_visible(False)

    def _cal_bg_scale_changed(self, side, value):
        cfg = self.model.current_configuration
        if cfg is None:
            return
        if side == 'ds':
            cfg.set_ds_cal_dark_frame_scale(value)
        else:
            cfg.set_us_cal_dark_frame_scale(value)

    def load_ds_cal_dark_file(self, filename=None):
        self._load_cal_dark_file('ds', filename)

    def load_us_cal_dark_file(self, filename=None):
        self._load_cal_dark_file('us', filename)

    def _load_cal_dark_file(self, side, filename):
        purpose = f'cal_dark_{side}'
        if filename is None or filename is False:
            filename = open_file_dialog(
                self.widget,
                caption=f"Load {side.upper()} cal dark frame",
                directory=self._last_dir_for(purpose),
                filter="Frames (*.spe *.SPE *.h5 *.tif *.tiff);;All files (*)",
            )
        if not filename:
            return
        self._remember_dir(purpose, filename)
        cfg = self.model.current_configuration
        if side == 'ds':
            cfg.load_ds_cal_dark_frame(filename)
        else:
            cfg.load_us_cal_dark_frame(filename)
        self._sync_cal_background_widgets()

    def clear_ds_cal_dark_file(self):
        self.model.current_configuration.clear_ds_cal_dark_frame()
        self._sync_cal_background_widgets()

    def clear_us_cal_dark_file(self):
        self.model.current_configuration.clear_us_cal_dark_frame()
        self._sync_cal_background_widgets()

    def _sync_cal_background_widgets(self):
        """Sync cal-bg mode combo, filenames, scales, and visibility."""
        cfg = self.model.current_configuration
        gb = self.widget.cal_background_subtraction_gb
        # Whole group visible only in Photron centered-window mode.
        show = (getattr(cfg, 'photron_mode', 'off') == 'centered'
                if cfg is not None else False)
        gb.setVisible(show)
        if cfg is None:
            return
        mode = getattr(cfg, 'cal_background_mode', None)
        try:
            idx = self._CAL_BG_MODES_BY_INDEX.index(mode)
        except ValueError:
            idx = 0
        gb.mode_combo.blockSignals(True)
        gb.mode_combo.setCurrentIndex(idx)
        gb.mode_combo.blockSignals(False)
        gb._set_dark_rows_visible(mode in ('prerecorded', 'hybrid'))
        gb.set_scale_spinboxes_enabled(mode == 'prerecorded')
        if getattr(cfg, 'mode', 'dual') == 'single':
            gb.set_us_row_visible(False)
        ds_name = (os.path.basename(cfg.ds_cal_dark_frame_filename)
                   if cfg.ds_cal_dark_frame_filename else 'None')
        gb.ds_dark_filename_lbl.setText(ds_name)
        gb.ds_dark_filename_lbl.setStyleSheet(
            '' if cfg.ds_cal_dark_frame_img is not None else 'color: gray;')
        gb.ds_dark_scale_sb.blockSignals(True)
        gb.ds_dark_scale_sb.setValue(cfg.ds_cal_dark_frame_scale)
        gb.ds_dark_scale_sb.blockSignals(False)
        us_name = (os.path.basename(cfg.us_cal_dark_frame_filename)
                   if cfg.us_cal_dark_frame_filename else 'None')
        gb.us_dark_filename_lbl.setText(us_name)
        gb.us_dark_filename_lbl.setStyleSheet(
            '' if cfg.us_cal_dark_frame_img is not None else 'color: gray;')
        gb.us_dark_scale_sb.blockSignals(True)
        gb.us_dark_scale_sb.setValue(cfg.us_cal_dark_frame_scale)
        gb.us_dark_scale_sb.blockSignals(False)


    def connect_to_ad_cb_callback(self):
        if self.widget.connect_to_ad_cb.isChecked():
            self.connect_to_area_detector()
        else:
            self.disconnect_from_area_detector()

    def connect_to_epics_cb_callback(self):
        if self.widget.connect_to_epics_cb.isChecked():
            self.connect_epics()
        else:
            self.disconnect_epics()

    def connect_to_monitor_folder_cb_callback(self):
        if self.widget.monitor_folder_cb.isChecked():
            self.connect_folder_monitor()
        else:
            self.disconnect_folder_monitor()

    def _set_source_mode_badge(self, mode):
        """Update the source mode badge in the file navigation bar.
        mode: 'file' or 'ad'
        """
        badge = self.widget.source_mode_badge
        if mode == 'ad':
            badge.setText("AD LIVE")
            badge.setStyleSheet(
                "background-color: #4DDECD; color: #1a1a1a; border-radius: 3px;"
                " padding: 1px 5px; font-weight: bold;"
            )
        else:
            badge.setText("FILE")
            badge.setStyleSheet(
                "background-color: #505050; color: #cccccc; border-radius: 3px;"
                " padding: 1px 5px; font-weight: bold;"
            )

    def data_source_mode_changed(self, file_system_selected):
        if file_system_selected:
            # Switching to file system mode
            if self.widget.connect_to_ad_cb.isChecked():
                self.widget.connect_to_ad_cb.setChecked(False)
                self.disconnect_from_area_detector()
            self.widget.connect_to_ad_cb.setEnabled(False)
            self.widget.monitor_folder_cb.setEnabled(True)
            self._set_source_mode_badge('file')
            cfg = self.model.current_configuration
            if cfg.filename:
                fname = os.path.split(cfg.filename)[-1]
                dirname = os.path.sep.join(os.path.dirname(cfg.filename).split(os.path.sep)[-2:])
                self.widget.filename_lbl.setText(os.path.join(dirname, fname))
                if cfg.mtime:
                    self.widget.mtime.setText('Timestamp: ' + str(cfg.mtime))
            else:
                self.widget.filename_lbl.setText('Select File...')
                self.widget.mtime.setText('')
        else:
            # Switching to live AD stream mode
            if self.widget.monitor_folder_cb.isChecked():
                self.widget.monitor_folder_cb.setChecked(False)
                self.disconnect_folder_monitor()
            self.widget.monitor_folder_cb.setEnabled(False)
            self.widget.connect_to_ad_cb.setEnabled(True)
            self._set_source_mode_badge('ad')
            record_name = eps.epics_settings.get('area_detector', '?')
            if self._AD_watcher is not None and self._AD_watcher.record_name:
                record_name = self._AD_watcher.record_name
            self.widget.filename_lbl.setText("AD: " + str(record_name))
            self.widget.mtime.setText("Frame: \u2014")


    def process_multiframe(self):
        # hack, refactor later:
        # Since the entire timelapse calculation is costly,
        # it should only get recalculated when the actual file changes,
        # or any setting changes
        # - not when only the frame number is updated.

        # for not this function is called from several places:
        # file is loaded
        # next or previous file is loaded
        # Area Detector data is updated

        cfg = self.model.current_configuration
        if cfg.data_img_file is not None:
            num_frames = cfg.data_img_file.num_frames
            if num_frames>1:

                self.update_time_lapse()

    def load_data_file(self, filenames=None):
        if isinstance(filenames, str):
            filenames = [filenames]
        if filenames is None or filenames is False:
            filenames = open_files_dialog(self.widget, caption="Load Experiment SPE",
                                          directory=self._last_dir_for('data'),
                                          filter="Spectra (*.spe *.SPE *.h5 *.tif *.tiff);;All files (*)")

        for filename in filenames:
            if filename != '':
                if os.path.isfile(filename):
                    self._exp_working_dir = os.path.dirname(str(filename))
                    self._remember_dir('data', filename)
                    self._auto_switch_configuration_by_detector(filename)
                    self.model.current_configuration.load_data_image(str(filename))
                    self._directory_watcher.path = self._exp_working_dir
                    # hack, refactor later:
                    self.process_multiframe()
                    self._default_history_axis_for_kinetics()
                    self._refresh_multiframe_ui()
                    # Ensure file system mode is active and badge reflects it
                    if not self.widget.file_system_rb.isChecked():
                        self.widget.file_system_rb.setChecked(True)
                        # data_source_mode_changed(True) fires via signal and updates badge + labels
                    else:
                        self._set_source_mode_badge('file')
                    self._send_temperature_trigger()
                else:
                    pass
                    #print('file not found: ' + str(filename))

    def _auto_switch_configuration_by_detector(self, filename):
        """Switch to the configuration whose calibration files match the detector of the given file."""
        _, ext = os.path.splitext(filename)
        if ext.lower() != '.spe':
            return
        detector = SpeFile.read_detector(filename)
        ind = self.model.find_configuration_for_detector(detector)
        if ind is not None and ind != self.model.configuration_ind:
            self.model.select_configuration(ind)

    def load_data_file_ad(self, filename=None):
        if isinstance(filename, str):
            self.model.current_configuration.load_data_image_ad(self._AD_watcher)
            # hack, refactor later:
            self.process_multiframe()
            ts = datetime.now().strftime('%H:%M:%S')
            record_name = self._AD_watcher.record_name if self._AD_watcher else '?'
            self.widget.filename_lbl.setText("AD: " + record_name)
            self.widget.mtime.setText("Frame: " + ts)
            self.widget.ad_last_update_lbl.setText("Last update: " + ts)
            self._send_temperature_trigger()

    def _send_temperature_trigger(self, force=False):
        """Publish the current fit to both output channels (ZMQ trigger + EPICS PVs).

        Called on every SPE file load and by the Trigger-now button. The two
        channels are gated independently:

        - ZMQ trigger to epicsLogger fires when *force* is True (Trigger-now)
          or when the 'Publish temperatures' checkbox in the epicsLogger panel
          is on.
        - EPICS PV publish fires when 'Publish temperatures to EPICS' is on
          AND the multi-frame mode is an aggregate mode. In 'Current frame'
          mode, EPICS is served live by ds/us_calculations_changed instead.
        """
        cfg = self.model.current_configuration

        # ZMQ path
        if force or self.widget.epicslogger_gb.publish_temperatures_cb.isChecked():
            data = self._build_trigger_payload(cfg)
            self.zmq_publisher_controller.send_trigger(data)

        # EPICS path — aggregate modes only (single mode uses the live path)
        mode = self._current_multiframe_mode(cfg)
        if mode != "single" and self.widget.connect_to_epics_cb.isChecked():
            self._publish_epics_temperatures(cfg)

    # Payload precision for the epicsLogger trigger. ndigits == 0 → int in the wire
    # message (not 1234.0), which keeps the log columns readable.
    _PAYLOAD_ROUND_NDIGITS = {
        'ds_temperature':       0,
        'us_temperature':       0,
        'ds_temperature_error': 1,
        'us_temperature_error': 1,
        'ds_fringe_frequency':  4,
        'us_fringe_frequency':  4,
        'ds_fringe_nd_um':      3,
        'us_fringe_nd_um':      3,
        'exposure_time':        6,
    }

    def _current_multiframe_mode(self, cfg):
        """Return the effective aggregation mode for *cfg*.

        Single-frame files (or no file loaded) always collapse to 'single'
        regardless of what the combobox says — the aggregate math is
        undefined for a single or zero frames.
        """
        data_file = getattr(cfg, 'data_img_file', None)
        frame_count = int(getattr(data_file, 'num_frames', 0) or 0)
        mode = self.widget.multiframe_output_gb.mode_cb.currentData() or "single"
        if frame_count <= 1:
            mode = "single"
        return mode

    def _kinetics_sync_active(self, cfg):
        """True when the history-axis is in per-side-synchronized units (k or lab-time)
        AND the config has a valid coincident-frame range (kinetics setup).

        In that state, the range spinboxes represent coincident-exposure k
        (1-based), not readout-frame indices. Per-side readout ranges are
        derived via cfg.coincident_to_readout(k). Outside this state the
        range is a plain readout-frame index applied identically to DS and US.
        """
        if getattr(self, '_history_x_mode', 'frame') not in ('sync_frame', 'time'):
            return False
        rng = cfg.get_coincident_frame_range() if cfg is not None else None
        return rng is not None

    def _readout_range_for_side(self, cfg, side, ui_start, ui_end):
        """Translate the range-spinbox values into (readout_start, readout_end)
        for one side, honoring the current axis-mode interpretation.

        - In kinetics-sync axis: ui_start/end are coincident-k (1-based). Map
          each end through cfg.coincident_to_readout(k) → per-side readout f;
          skip range endpoints that fall outside the side's [0, N-1] and clamp
          to what remains.
        - Otherwise: values are readout-frame indices (0-based) applied to both
          sides identically.

        Returns (f_start, f_end) or (None, None) if the range collapses to
        nothing on that side (e.g. k range covers only frames the other side has).
        """
        n = int(getattr(getattr(cfg, 'data_img_file', None), 'num_frames', 0) or 0)
        if n <= 0:
            return None, None
        if not self._kinetics_sync_active(cfg):
            f_start = max(0, min(n - 1, int(ui_start)))
            f_end   = max(f_start, min(n - 1, int(ui_end)))
            return f_start, f_end
        # kinetics-sync branch — walk k values in the requested range and
        # collect the side's valid readout indices; the endpoints may be None.
        ks = list(range(int(ui_start), int(ui_end) + 1))
        readouts = []
        for k in ks:
            f_ds, f_us = cfg.coincident_to_readout(k)
            f = f_ds if side == 'ds' else f_us
            if f is not None:
                readouts.append(int(f))
        if not readouts:
            return None, None
        return min(readouts), max(readouts)

    def _aggregate_temperature_series(self, cfg, temps, errs, op, err_metric,
                                      start=None, end=None):
        """Aggregate a per-frame T series into a (value, error) scalar pair.

        Filters applied (all must pass for a frame to be included):
          - finite (drops NaN/inf)
          - not the 0.0 'no fit' sentinel used by fit_all_frames
          - min_allowed_T < T < max_allowed_T (controller-level sanity bounds)
          - per-frame fit error < cfg.error_limit (per-config quality gate)

        Parameters
        ----------
        cfg      : the TemperatureModelConfiguration (for error_limit)
        temps    : 1-D np array of per-frame temperatures
        errs     : 1-D np array of per-frame fit errors (may be empty)
        op       : "mean" or "median"
        err_metric: "fit_avg" (mean of per-frame errors) or "std" (spread of Ts)
        start,end: inclusive frame-index range to consider (defaults to full array)

        Returns
        -------
        (value, error) as JSON-safe floats, or (None, None) if no valid frames.
        """
        sf = ZmqWorkerController._safe_float
        temps = np.asarray(temps, dtype=float)
        errs  = np.asarray(errs,  dtype=float)
        if temps.size == 0:
            return None, None

        if start is None:
            start = 0
        if end is None:
            end = temps.size - 1

        sl = temps[start:end + 1]
        el = errs[start:end + 1] if errs.size == temps.size else np.zeros_like(sl)

        min_T = getattr(self, 'min_allowed_T', -np.inf)
        max_T = getattr(self, 'max_allowed_T',  np.inf)
        err_limit = getattr(cfg, 'error_limit', np.inf)

        valid = np.isfinite(sl) & (sl != 0.0) & (sl > min_T) & (sl < max_T)
        # Only apply error_limit if we have matching errors (else the filter
        # would be vacuously true and never trim anything).
        if el.size == sl.size:
            valid &= (el < err_limit)
        if not valid.any():
            return None, None

        sl_v = sl[valid]
        el_v = el[valid] if el.size == sl.size else np.array([])
        center = float(np.mean(sl_v)) if op == "mean" else float(np.median(sl_v))
        if err_metric == "std":
            err_out = float(np.std(sl_v, ddof=0)) if sl_v.size > 1 else 0.0
        else:  # fit_avg
            err_out = float(np.mean(el_v)) if el_v.size else None
        return sf(center), sf(err_out)

    def _compute_multiframe_output(self, cfg):
        """Aggregate per-frame temperatures per the multi-frame mode setting.

        Returns a dict with:
          ds_temperature, us_temperature — chosen aggregate (or current-frame) T
          ds_temperature_error, us_temperature_error — error metric per user setting
          error_metric — one of 'fit' (single mode) / 'fit_avg' / 'std'
          aggregation — mode string
          frame_index — for single mode, the frame this represents; else None
          frame_range_start, frame_range_end — for range_* modes; else None
          frame_count — total frames in the file

        Errors and temperatures are None if no data is available. Used by
        both the ZMQ payload builder and the EPICS PV publisher.
        """
        gb = self.widget.multiframe_output_gb
        mode = self._current_multiframe_mode(cfg)
        dual = getattr(cfg, 'mode', 'dual') == 'dual'
        sf = ZmqWorkerController._safe_float
        data_file = getattr(cfg, 'data_img_file', None)
        frame_count = int(getattr(data_file, 'num_frames', 0) or 0)

        out = {
            'frame_count':       frame_count,
            'aggregation':       mode,
            'frame_index':       None,
            'frame_range_start': None,
            'frame_range_end':   None,
            'error_metric':      None,
        }

        if mode == "single":
            out['ds_temperature']       = sf(getattr(cfg, 'ds_temperature', None))
            out['ds_temperature_error'] = sf(getattr(cfg, 'ds_temperature_error', None))
            out['us_temperature']       = sf(getattr(cfg, 'us_temperature', None))       if dual else None
            out['us_temperature_error'] = sf(getattr(cfg, 'us_temperature_error', None)) if dual else None
            out['frame_index']          = int(getattr(cfg, 'current_frame', 0) or 0)
            out['error_metric']         = 'fit'
            return out

        # Aggregate branch — per-side readout ranges (they differ in kinetics-sync mode).
        if mode in ("mean_range", "median_range"):
            ui_start, ui_end = gb.range_start_sb.value(), gb.range_end_sb.value()
            ds_range = self._readout_range_for_side(cfg, 'ds', ui_start, ui_end)
            us_range = self._readout_range_for_side(cfg, 'us', ui_start, ui_end)
            range_start_ui, range_end_ui = ui_start, ui_end
        else:
            ds_range = (0, frame_count - 1)
            us_range = (0, frame_count - 1)
            range_start_ui, range_end_ui = None, None

        ds_arr = np.asarray(getattr(cfg, 'ds_temperatures', []) or [], dtype=float)
        ds_err = np.asarray(getattr(cfg, 'ds_temperatures_errors', []) or [], dtype=float)
        us_arr = np.asarray(getattr(cfg, 'us_temperatures', []) or [], dtype=float)
        us_err = np.asarray(getattr(cfg, 'us_temperatures_errors', []) or [], dtype=float)

        op = "mean" if mode.startswith("mean") else "median"
        err_metric = gb.error_metric_cb.currentData() or "fit_avg"

        ds_t, ds_e = (self._aggregate_temperature_series(cfg, ds_arr, ds_err, op, err_metric,
                                                        ds_range[0], ds_range[1])
                      if ds_range != (None, None) else (None, None))
        us_t, us_e = ((self._aggregate_temperature_series(cfg, us_arr, us_err, op, err_metric,
                                                        us_range[0], us_range[1])
                      if us_range != (None, None) else (None, None))
                      if dual else (None, None))

        out['ds_temperature']       = ds_t
        out['ds_temperature_error'] = ds_e
        out['us_temperature']       = us_t
        out['us_temperature_error'] = us_e
        out['error_metric']         = err_metric
        out['frame_range_start']    = range_start_ui
        out['frame_range_end']      = range_end_ui
        return out

    def _publish_epics_temperatures(self, cfg):
        """caput the aggregated DS/US temperatures to the configured EPICS PVs."""
        if not self.epics_available or caput is None:
            return
        agg = self._compute_multiframe_output(cfg)
        ds_pv = eps.epics_settings.get('ds_last_temp')
        us_pv = eps.epics_settings.get('us_last_temp')
        ds_t = agg.get('ds_temperature')
        us_t = agg.get('us_temperature')
        if ds_pv and ds_pv not in ('', 'None') and ds_t is not None:
            caput(ds_pv, ds_t)
        if us_pv and us_pv not in ('', 'None') and us_t is not None:
            caput(us_pv, us_t)

    def _build_trigger_payload(self, cfg):
        """Assemble the trigger data dict, honoring the multi-frame log mode.

        Returns None only when the payload has no meaningful content (no file
        loaded and every scalar is None) — in that case the publisher still
        sends a bare {"type":"trigger"}, which is a valid 'log a row' signal.
        """
        # Start from the scalar-metadata fields (fringe/exposure/gain/filename).
        data = ZmqWorkerController.collect_temperature_values(cfg)

        if cfg.filename:
            data['filename'] = os.path.basename(cfg.filename)
            data['filepath'] = cfg.filename
        else:
            data['filepath'] = None

        # Overwrite T/error scalars with mode-aware aggregate + attach frame context.
        agg = self._compute_multiframe_output(cfg)
        for key in ('ds_temperature', 'ds_temperature_error',
                    'us_temperature', 'us_temperature_error',
                    'aggregation', 'error_metric', 'frame_count',
                    'frame_index', 'frame_range_start', 'frame_range_end'):
            data[key] = agg.get(key)

        for _key, _nd in self._PAYLOAD_ROUND_NDIGITS.items():
            _v = data.get(_key)
            if _v is None:
                continue
            try:
                _r = round(float(_v), _nd)
                data[_key] = int(_r) if _nd == 0 else _r
            except (TypeError, ValueError):
                pass

        return data if any(v is not None for v in data.values()) else None

    def _refresh_multiframe_ui(self):
        """Clamp the multi-frame range spinboxes to the current file's frame range,
        with bounds and tooltips interpreted per the active history-axis mode.

        - Kinetics-sync axis: spinboxes represent coincident-exposure k
          (1-based). Bounds come from cfg.get_coincident_frame_range().
        - Otherwise: spinboxes represent readout-frame index (0-based).
          Bound = num_frames - 1.
        """
        cfg = self.model.current_configuration
        data_file = getattr(cfg, 'data_img_file', None)
        n = int(getattr(data_file, 'num_frames', 0) or 0)
        gb = self.widget.multiframe_output_gb

        if self._kinetics_sync_active(cfg):
            k_min, k_max = cfg.get_coincident_frame_range()
            lo, hi = int(k_min), int(k_max)
            unit_hint = ("Coincident-exposure frame index k (1-based). "
                         "DS and US may aggregate different readout frames for the same k range.")
        else:
            lo, hi = 0, max(0, n - 1)
            unit_hint = "Readout-frame index (0-based). Applied identically to DS and US."

        # Bounds first; setValue is ignored when out-of-range, so re-clamp values after.
        for sb in (gb.range_start_sb, gb.range_end_sb):
            sb.setMinimum(lo)
            sb.setMaximum(hi)
        if gb.range_start_sb.value() < lo:
            gb.range_start_sb.setValue(lo)
        if gb.range_start_sb.value() > hi:
            gb.range_start_sb.setValue(hi)
        if gb.range_end_sb.value() < lo:
            gb.range_end_sb.setValue(lo)
        if gb.range_end_sb.value() > hi:
            gb.range_end_sb.setValue(hi)
        if gb.range_end_sb.value() < gb.range_start_sb.value():
            gb.range_end_sb.setValue(gb.range_start_sb.value())

        gb.range_start_sb.setToolTip(f"First frame (inclusive). {unit_hint}")
        gb.range_end_sb.setToolTip(f"Last frame (inclusive). {unit_hint}")

        has_multi = n > 1
        gb.mode_cb.setEnabled(has_multi)
        gb.range_start_sb.setEnabled(has_multi)
        gb.range_end_sb.setEnabled(has_multi)
        # Error-metric only meaningful in aggregate modes; disable in 'single'.
        mode = gb.mode_cb.currentData() or "single"
        gb.error_metric_cb.setEnabled(has_multi and mode != "single")

    def _on_multiframe_mode_changed(self, _index=None):
        """Toggle the range-row visibility + enable/disable the error-metric combo."""
        gb = self.widget.multiframe_output_gb
        mode = gb.mode_cb.currentData() or "single"
        is_range = mode in ("mean_range", "median_range")
        gb.range_lbl.setVisible(is_range)
        gb._range_row_widget.setVisible(is_range)
        gb.error_metric_cb.setEnabled(mode != "single" and gb.mode_cb.isEnabled())

    def cleanup(self):
        """Stop all background threads cleanly (called on app exit)."""
        self.zmq_worker_controller.cleanup()
        self.zmq_publisher_controller.cleanup()

    def file_dragged_in(self,files):
        self.load_data_file(filenames=files)
                
    def load_next_data_image(self):
        
        if self.widget.browse_by_name_rb.isChecked():
            mode = 'number'
        else:
            mode = 'time'
        self.model.current_configuration.load_next_data_image(mode)

        # hack, refactor later:
        self.process_multiframe()

    def load_previous_data_image(self):

        if self.widget.browse_by_name_rb.isChecked():
            mode = 'number'
        else:
            mode = 'time'
        self.model.current_configuration.load_previous_data_image(mode)

        # hack, refactor later:
        self.process_multiframe()
        
    def toggle_browse_mode(self):
        
        time_mode = self.widget.browse_by_time_rb.isChecked()
        self.model.current_configuration._filename_iterator.create_timed_file_list = time_mode
        if time_mode:
            self.model.current_configuration._filename_iterator.update_file_list()
        

    def load_ds_calibration_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Downstream Calibration SPE",
                                        directory=self._last_dir_for('ds_cal'),
                                        filter="Spectra (*.spe *.SPE *.h5 *.tif *.tiff);;All files (*)")

        if filename != '':
            self._remember_dir('ds_cal', filename)
            ds_start_frame = int(self.widget.ds_calibration_start_frame.text())
            ds_end_frame = int(self.widget.ds_calibration_end_frame.text())
            self.model.current_configuration.ds_temperature_model.calibration_frames = [ds_start_frame,ds_end_frame]
            self.model.current_configuration.load_ds_calibration_image(filename)

    def load_us_calibration_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Upstream Calibration SPE",
                                        directory=self._last_dir_for('us_cal'),
                                        filter="Spectra (*.spe *.SPE *.h5 *.tif *.tiff);;All files (*)")

        if filename != '':
            self._remember_dir('us_cal', filename)

            us_start_frame = int(self.widget.us_calibration_start_frame.text())
            us_end_frame = int(self.widget.us_calibration_end_frame.text())
            self.model.current_configuration.us_temperature_model.calibration_frames = [us_start_frame,us_end_frame]

            self.model.current_configuration.load_us_calibration_image(filename)

    def clear_ds_calibration_file(self):
        self.model.current_configuration.clear_ds_calibration_image()

    def clear_us_calibration_file(self):
        self.model.current_configuration.clear_us_calibration_image()

    def load_wavelength_calibration_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(
                self.widget,
                caption="Load Wavelength Calibration (calibration.json)",
                directory=self._last_dir_for('wavelength_cal'),
                filter="Calibration JSON (*.json);;All files (*)",
            )
        if not filename:
            return
        self._remember_dir('wavelength_cal', filename)
        cfg = self.model.current_configuration
        # DEBUG: try/except removed so failures raise with full traceback.
        cfg.load_photron_wavelength_calibration(filename)
        self._update_wavelength_calibration_label()
        self._reload_current_tif_if_any()

    def clear_wavelength_calibration(self):
        cfg = self.model.current_configuration
        cfg.clear_photron_wavelength_calibration()
        self._update_wavelength_calibration_label()
        self._reload_current_tif_if_any()

    def _update_wavelength_calibration_label(self):
        cfg = self.model.current_configuration
        wc = cfg.photron_wavelength_calibration
        lbl = self.widget.wavelength_calibration_filename_lbl
        clear_btn = self.widget.clear_wavelength_calibration_btn
        if wc is None:
            lbl.setText('None loaded')
            lbl.setStyleSheet('color: gray;')
            lbl.setToolTip('')
            clear_btn.setEnabled(False)
        else:
            src = wc.get('source_filename', '') or ''
            lbl.setText(os.path.basename(src) if src else '(from settings)')
            lbl.setStyleSheet('')
            lbl.setToolTip(src)
            clear_btn.setEnabled(True)

    def _reload_current_tif_if_any(self):
        cfg = self.model.current_configuration
        if cfg.filename and cfg.filename.lower().endswith(('.tif', '.tiff')):
            cfg.load_data_image(cfg.filename)

    def _measurement_mode_changed(self, checked):
        # Both radios' toggled signals fire per state change; act only on the
        # one that became checked to avoid double-processing.
        if not checked:
            return
        mode = 'dual' if self.widget.dual_mode_rb.isChecked() else 'single'
        cfg = self.model.current_configuration
        if cfg.mode == mode:
            return
        cfg.set_mode(mode)
        self.widget.apply_measurement_mode(mode)
        # Blank the us curve on both history tabs (Latest + Total).
        if hasattr(self.data_history_widget, 'set_mode'):
            self.data_history_widget.set_mode(mode)

    def us_calibration_frame_range_callback(self, *args):
        us_start_frame = int(self.widget.us_calibration_start_frame.text())
        us_end_frame = int(self.widget.us_calibration_end_frame.text())
        self.model.current_configuration.us_temperature_model.calibration_frames = [us_start_frame,us_end_frame]
        self.model.current_configuration.us_set_calibration_data()

    def ds_calibration_frame_range_callback(self, *args):
        ds_start_frame = int(self.widget.ds_calibration_start_frame.text())
        ds_end_frame = int(self.widget.ds_calibration_end_frame.text())
        self.model.current_configuration.ds_temperature_model.calibration_frames = [ds_start_frame,ds_end_frame]
       
        self.model.current_configuration.ds_set_calibration_data()


    def ds_temperature_txt_changed(self):
        new_temperature = float(str(self.widget.ds_temperature_txt.text()))
        self.model.current_configuration.set_ds_calibration_temperature(new_temperature)

    def us_temperature_txt_changed(self):
        new_temperature = float(str(self.widget.us_temperature_txt.text()))
        self.model.current_configuration.set_us_calibration_temperature(new_temperature)

    def temperature_function_callback(self):
        plank = self.widget.temperature_function_plank_rb.isChecked()
        if plank:
            function_type = 'plank'
        else:
            function_type = 'wien'
        self.model.current_configuration.set_temperature_fit_function(function_type)

    def error_limit_changed(self, value):
        cfg = self.model.current_configuration
        if cfg is None:
            return
        cfg.set_error_limit(value)
        # set_error_limit emits data_changed_signal → spectrum widget refresh.
        # Time-lapse plot uses the same limit; force a redraw so it re-selects.
        self.redraw_time_lapse()

    def filter_setting_callback(self):
        self.model.current_configuration.ds_filter_oscillation = \
            self.widget.ds_interference_filter_cb.isChecked()
        self.model.current_configuration.us_filter_oscillation = \
            self.widget.us_interference_filter_cb.isChecked()

    def save_filtered_callback(self):
        self.model.current_configuration.save_filtered_spectrum = \
            self.widget.save_filtered_cb.isChecked()

    def filter_freq_range_callback(self):
        step = self.widget.filter_freq_min_sb.singleStep()
        freq_min = self.widget.filter_freq_min_sb.value()
        freq_max = self.widget.filter_freq_max_sb.value()

        # Keep max > min by adjusting each spinbox's range (block signals to avoid loops)
        self.widget.filter_freq_max_sb.blockSignals(True)
        self.widget.filter_freq_max_sb.setMinimum(freq_min + step)
        self.widget.filter_freq_max_sb.blockSignals(False)

        self.widget.filter_freq_min_sb.blockSignals(True)
        self.widget.filter_freq_min_sb.setMaximum(freq_max - step)
        self.widget.filter_freq_min_sb.blockSignals(False)

        # Re-read in case clamping shifted a value
        self.model.current_configuration.filter_freq_min = \
            self.widget.filter_freq_min_sb.value()
        self.model.current_configuration.filter_freq_max = \
            self.widget.filter_freq_max_sb.value()

    def load_ds_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Downstream Standard Spectrum",
                                        directory=self._last_dir_for('ds_standard'))

        if filename != '':
            self._remember_dir('ds_standard', filename)
            self.model.current_configuration.load_ds_standard_spectrum(filename)

    def load_us_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load Upstream Standard Spectrum",
                                        directory=self._last_dir_for('us_standard'))

        if filename != '':
            self._remember_dir('us_standard', filename)
            self.model.current_configuration.load_us_standard_spectrum(filename)

    def save_ds_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(self.widget, caption="Save Downstream Standard Spectrum",
                                        directory=self._last_dir_for('ds_standard'))

        if filename != '':
            self._remember_dir('ds_standard', filename)
            self.model.current_configuration.save_ds_standard_spectrum(filename)

    def save_us_standard_file(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(self.widget, caption="Save Upstream Standard Spectrum",
                                        directory=self._last_dir_for('us_standard'))

        if filename != '':
            self._remember_dir('us_standard', filename)
            self.model.current_configuration.save_us_standard_spectrum(filename)

    def save_setting_file(self, filename=None):
        if filename is None or filename is False:
            filename = save_file_dialog(self.widget, caption="Save setting file",
                                        directory=self.model.current_configuration._setting_working_dir,
                                        filter="Settings (*.trs);;All files (*)")

        if filename != '':
            if not filename.lower().endswith('.trs'):
                filename += '.trs'
            self.model.current_configuration._setting_working_dir = os.path.dirname(filename)
            self.model.current_configuration.save_setting(filename)
            self.update_setting_combobox(filename)
            self._refresh_configuration_buttons()

    def load_setting_file(self, filename=None):
        if filename is None or filename is False:
            filename = open_file_dialog(self.widget, caption="Load setting file",
                                        directory=self.model.current_configuration._setting_working_dir)

        if filename != '':
            self.model.current_configuration._setting_working_dir = os.path.dirname(filename)
            self.model.current_configuration.load_setting(filename)

            self.update_setting_combobox(filename)
            # Reflect the new background_mode (and dark rows/scales) in the UI.
            # load_setting mutates cfg.background_mode but does not touch widgets;
            # without this the combo can lag the actual model state.
            self._sync_background_widgets()
            self._sync_cal_background_widgets()
            self._refresh_roi_panels()
            # Re-fit multi-frame data. New ROIs / cal / bg mode invalidate the
            # cached ds_temperatures / us_temperatures, so history plots would
            # otherwise show stale numbers until the user reloads the .spe by
            # hand.
            # DEBUG: try/except removed so failures raise with full traceback.
            self.process_multiframe()

    def import_slots_from_trs(self, filename=None):
        """Pull DS/US mask-slot offsets from another .trs into the current
        configuration. Only meaningful when the current cal can't supply
        them (typically a kinetics-mode cal); otherwise the derived value
        wins per _q_side precedence."""
        cfg = self.model.current_configuration
        if filename is None or filename is False:
            filename = open_file_dialog(
                self.widget, caption="Import DS/US slots from .trs",
                directory=cfg._setting_working_dir,
                filter="Settings (*.trs);;All files (*)")
        if not filename:
            print("[import_slots] cancelled (no file)")
            return
        base = os.path.basename(filename)
        print(f"[import_slots] file={filename}")
        # DEBUG: try/except removed so failures raise with full traceback.
        q_ds, q_us, source = cfg.import_slots_from_trs(filename)
        if q_ds is None and q_us is None:
            msg = (f"Could not import slots from '{base}'.\n\n"
                   "No q_ds_slot / q_us_slot attrs, and derivation from an "
                   "embedded full-chip cal failed (missing cal image, missing "
                   "kinetics_info, or cal is itself kinetics-shaped).")
            print(f"[import_slots] no attrs and no derivation possible in {base}")
            QtWidgets.QMessageBox.warning(self.widget, "Import slots", msg)
            return
        # Report what the config now uses (derived-from-cal wins over override).
        effective_ds = cfg._q_side('ds')
        effective_us = cfg._q_side('us')
        source_ds = ("cross-mode cal" if cfg.cross_mode_cal_info('ds') is not None
                     else "imported override")
        source_us = ("cross-mode cal" if cfg.cross_mode_cal_info('us') is not None
                     else "imported override")
        source_label = {'attrs': 'saved attrs',
                        'derived': 'derived from embedded cal',
                        'none': 'none'}.get(source, source)
        print(f"[import_slots] imported: q_ds={q_ds}, q_us={q_us} (source: {source})")
        print(f"[import_slots] effective: q_ds={effective_ds} ({source_ds}), "
              f"q_us={effective_us} ({source_us})")
        msg = (f"Imported from {base} ({source_label}):\n"
               f"  q_ds = {q_ds}\n  q_us = {q_us}\n\n"
               f"Effective now:\n"
               f"  q_ds = {effective_ds}  (from {source_ds})\n"
               f"  q_us = {effective_us}  (from {source_us})")
        QtWidgets.QMessageBox.information(self.widget, "Import slots", msg)
        # Sync alignment now depends on new q values; re-render history plot.
        self.redraw_time_lapse()

    def save_data_btn_clicked(self, filename=None):
        if filename is None or filename is False:
            base = os.path.basename(
                '.'.join(self.model.current_configuration.data_img_file.filename.split(".")[:-1]) + ".txt")
            filename = save_file_dialog(
                self.widget,
                caption="Save data in tabulated text format",
                directory=os.path.join(self._last_dir_for('save_data'), base)
            )
        if filename != '':
            self._remember_dir('save_data', filename)
            self.model.current_configuration.save_txt(filename)

    def save_graph_btn_clicked(self, filename=None):
        if filename is None or filename is False:
            base = os.path.basename(
                '.'.join(self.model.current_configuration.data_img_file.filename.split(".")[:-1]) + ".svg")
            filename = save_file_dialog(
                self.widget,
                caption="Save displayed graph as vector graphics or image",
                directory=os.path.join(self._last_dir_for('save_graph'), base),
                filter='Vector Graphics (*.svg);; Image (*.png)'
            )
        if filename:
            self._remember_dir('save_graph', filename)
        filename = str(filename)
        base_filename, extension = get_file_and_extension(filename)
        ds_filename = base_filename + "_ds." + extension
        us_filename = base_filename + "_us." + extension

        if filename != '':
            self.widget.temperature_spectrum_widget.save_graph(ds_filename, us_filename)

    def update_setting_combobox(self, filename):
        folder = os.path.split(filename)[0]
        self._settings_files_list = []
        self._settings_file_names_list = []
        # DEBUG: try/except removed so failures raise with full traceback.
        files = os.listdir(folder)
        files = natsorted(files)
        for file in files:
            if file.endswith('.trs') and not file.startswith('.'):
                self._settings_files_list.append(file)
                name_for_list = os.path.splitext(file)[0]
                self._settings_file_names_list.append(name_for_list)
        if not len(self._settings_files_list):
            self._settings_files_list.append(filename)
            name_for_list = os.path.splitext(os.path.basename(filename))[0]
            self._settings_file_names_list.append(name_for_list)

        self.widget.settings_cb.blockSignals(True)
        self.widget.settings_cb.clear()
        self.widget.settings_cb.addItems(self._settings_file_names_list)

        selected_name = os.path.splitext(os.path.basename(filename))[0]
        ind = self._settings_file_names_list.index(selected_name)
        self.widget.settings_cb.setCurrentIndex(ind)
        self.widget.settings_cb.blockSignals(False)

    def settings_cb_changed(self):
        current_index = self.widget.settings_cb.currentIndex()
        new_file_name = os.path.join(self.model.current_configuration._setting_working_dir,
                                     self._settings_files_list[current_index])  # therefore also one has to be deleted
        self.load_setting_file(new_file_name)
        self.widget.settings_cb.blockSignals(True)
        self.widget.settings_cb.setCurrentIndex(current_index)
        self.widget.settings_cb.blockSignals(False)
        self.use_background_update()

    def use_background_update(self):
        """Sync all background-subtraction widgets to the current config's state."""
        self._sync_background_widgets()
        self._sync_cal_background_widgets()


    def data_changed_signal_callback(self):
        self.widget.roi_widget.plot_img(self.model.current_configuration.data_img)
        if self.model.current_configuration.data_img_file is not None:
            if hasattr(self.model.current_configuration.data_img_file,'raw_ccd'):
                self.widget.roi_widget.plot_raw_ccd(self.model.current_configuration.data_img_file.raw_ccd)
        self._refresh_bg_stack()
        self._refresh_roi_panels()
        if self.model.current_configuration.x_calibration is not None and self.model.current_configuration.data_img is not None:
            wl_calibration = self.model.current_configuration.x_calibration
            #x_dim = self.model.current_configuration.data_img.shape[1]
            x = round(wl_calibration[0], 3)
            y = 0
            w = round(wl_calibration[-1]-wl_calibration[0],3)
            h = self.model.current_configuration.data_img.shape[0]

            # All three 2D viewers must share the same coordinate system so the
            # ROI x-positions (already in wavelength space) sync sensibly.
            self.widget.roi_widget.img_widget.set_wavelength_calibration((x,y,w,h))
            self.widget.roi_widget.ds_cal_img_widget.set_wavelength_calibration((x,y,w,h))
            self.widget.roi_widget.us_cal_img_widget.set_wavelength_calibration((x,y,w,h))
        rois = self.model.current_configuration.get_roi_data_list()
        self.widget.roi_widget.set_rois(self.model.current_configuration.get_roi_data_list())
        self.widget.roi_widget.set_wl_range(self.model.current_configuration.wl_range)

        # Cross-mode cal viewer setup: when data is kinetics but a side's
        # calibration is a full-chip 2D image, give that cal viewer its own
        # cal-native y-axis and show its signal ROI at cal-dim coordinates
        # (backgrounds hidden). Otherwise restore the shared behavior.
        self._refresh_cross_mode_cal_viewers()
        
        # update exp data widget
        #####################################

        if self.model.current_configuration.data_img_file is not None:
            if hasattr(self.model.current_configuration.data_img_file, 'filename') :
                self.model.current_configuration.data_img_file.filename = os.path.normpath(self.model.current_configuration.data_img_file.filename)
                fname = os.path.split(self.model.current_configuration.data_img_file.filename)[-1]
                dirname = os.path.sep.join(os.path.dirname(self.model.current_configuration.data_img_file.filename).split(os.path.sep)[-2:])
                joined = os.path.join(dirname,fname)
                self.widget.filename_lbl.setText(joined)

                #self.widget.dirname_lbl.setText(dirname)
            else:
                self.widget.filename_lbl.setText('')
                #self.widget.dirname_lbl.setText('')

            if self.model.current_configuration.data_img_file.num_frames > 1:
                self.widget.frame_widget.setVisible(True)
                self.widget.temperature_spectrum_widget.show_time_lapse_plot(True)
            else:
                self.widget.frame_widget.setVisible(False)
                self.widget.temperature_spectrum_widget.show_time_lapse_plot(False)
           
            # Frame-text unit depends on axis mode: in synced modes the box
            # shows coincident-k (matches the plot's x-axis); in frame mode it
            # shows the 1-based readout index. Reading the stale readout index
            # here would clobber a k value that _load_coincident_k just set
            # right before this callback fires (via set_img_frame_numbers).
            if self._history_x_mode in ('time', 'sync_frame'):
                self.set_frame_text(str(int(self._current_coincident_k)))
            else:
                self.set_frame_text(str(self.model.current_configuration.current_frame + 1))
       
            
            self.widget.graph_info_lbl.setText(self.model.current_configuration.file_info)
        else:
            self.widget.filename_lbl.setText('Select File...')
            #self.widget.dirname_lbl.setText('')
            self.widget.frame_widget.setVisible(False)
            self.widget.temperature_spectrum_widget.show_time_lapse_plot(False)

        self.use_background_update()

        self._update_wavelength_calibration_label()
        self._refresh_configuration_buttons()
        # Kinetics badge + strip-counter relabel, driven by the config's
        # auto-detected kinetics state (set in _sync_kinetics_from_file on
        # load, or restored from .trs attrs on workspace open).
        cfg = self.model.current_configuration
        kmode = getattr(cfg, 'kinetics_mode', 'off')
        kinfo = getattr(cfg, 'kinetics_info', {})
        self.widget.set_kinetics_badge(kmode, kinfo)
        # Refresh the consolidated Kinetics panel (visibility + info).
        # DEBUG: try/except removed so failures raise with full traceback.
        q_ds = cfg._q_side('ds')
        q_us = cfg._q_side('us')
        koverride = getattr(cfg, 'kinetics_mode_override', None)
        self.widget.kinetics_gb.apply_kinetics_state(kmode, kinfo, q_ds, q_us, koverride)
        # Sync CameraModeGB combo to cfg without re-triggering the wire, and
        # apply Photron styling so kinetics-only groups collapse in Photron mode.
        pmode = getattr(cfg, 'photron_mode', 'off')
        cam_items = self.widget.camera_mode_gb.MODE_ITEMS
        cam_idx = next((i for i, (_, m) in enumerate(cam_items) if m == pmode), 0)
        self.widget.camera_mode_gb.mode_combo.blockSignals(True)
        self.widget.camera_mode_gb.mode_combo.setCurrentIndex(cam_idx)
        self.widget.camera_mode_gb.mode_combo.blockSignals(False)
        # Hide the Camera selector for standard PI files (SPE/H5) — Photron
        # mode only applies to TIF. Leave visible for TIF and when no file is
        # loaded (so the user can still pick before loading).
        reader = getattr(cfg, 'data_img_file', None)
        is_pi_native = isinstance(reader, (SpeFile, H5File))
        self.widget.camera_mode_gb.setVisible(not is_pi_native)
        self._apply_photron_styling(pmode == 'centered')
        # Apply the (possibly config-switched) measurement mode so the UI matches.
        mode = getattr(self.model.current_configuration, 'mode', 'dual')
        self.widget.apply_measurement_mode(mode)
        if hasattr(self.data_history_widget, 'set_mode'):
            self.data_history_widget.set_mode(mode)

        # Sync the fit-error-limit spinbox to the (possibly config-switched)
        # cfg value without re-triggering the setter's data_changed emit.
        el_sb = self.widget.t_function_type_section.error_limit_sb
        el_sb.blockSignals(True)
        el_sb.setValue(float(cfg.error_limit))
        el_sb.blockSignals(False)

        self.ds_calculations_changed()
        self.us_calculations_changed()
        self._refresh_cal_roi_panel()

    def _refresh_configuration_buttons(self):
        """Sync the config-button labels (e.g. unsaved-changes asterisk) to model state."""
        self.widget.config_widget.update_configuration_btns(
            configurations=self.model.configurations,
            cur_ind=self.model.configuration_ind,
        )

        self.widget.temperature_spectrum_widget.normalize_range()
        
        mtime = self.model.current_configuration.mtime
        self.widget.mtime.setText('Timestamp: '+ str(mtime))

        settings_filename = self.model.current_configuration.setting_filename
        if settings_filename:
            self.update_setting_combobox(settings_filename)

    def _get_calibration_image(self, side):
        """Return the currently-loaded intensity calibration image for ds/us, or None.

        Prefer the calibration_img_file (fresh from disk) when available; fall
        back to the SingleTemperatureModel's cached calibration_img (which is
        what survives a .trs restore even when the source file is gone).
        """
        cfg = self.model.current_configuration
        img_file = cfg.ds_calibration_img_file if side == 'ds' else cfg.us_calibration_img_file
        model = cfg.ds_temperature_model if side == 'ds' else cfg.us_temperature_model
        if img_file is not None and getattr(img_file, 'img', None) is not None:
            img = img_file.img
            if isinstance(img, list):
                img = img[0]
            img = np.asarray(img)
            # Kinetics cal images are stored as a 3D strip stack in the .trs
            # (n_strips, window_h, width). Pick the first strip for display —
            # the 2D preview is a representative frame, not a montage.
            if img.ndim == 3:
                img = img[0]
            return img
        cached = getattr(model, 'calibration_img', None)
        if cached is not None:
            cached = np.asarray(cached)
            if cached.ndim == 3:
                cached = cached[0]
            return cached
        return None

    def _push_ds_calibration_view(self):
        """Refresh the DS Cal 2D + 1D tabs from current model state.

        Cheap: image push happens only when the image identity changes; the 1D
        spectrum is always pushed (small, and it's what the ROI drag affects).
        """
        cfg = self.model.current_configuration
        img = self._get_calibration_image('ds')
        img_changed = False
        if img is not None and id(img) != getattr(self, '_last_ds_cal_img_id', None):
            self.widget.roi_widget.plot_ds_calibration_image(img)
            self._last_ds_cal_img_id = id(img)
            img_changed = True
        elif img is None and getattr(self, '_last_ds_cal_img_id', None) is not None:
            self.widget.roi_widget.ds_cal_img_widget.pg_img_item.clear()
            self._last_ds_cal_img_id = None
            img_changed = True
        cal_x, cal_y = cfg.ds_temperature_model.calibration_spectrum.data
        self.widget.roi_widget.plot_ds_calibration_spectrum(cal_x, cal_y)
        # Cal image identity changed → cross-mode status for this side may
        # have flipped; re-sync cal viewer geometry + ROI overlay.
        if img_changed:
            self._refresh_cross_mode_cal_viewers()
            self._refresh_roi_panels()

    def _push_us_calibration_view(self):
        cfg = self.model.current_configuration
        img = self._get_calibration_image('us')
        img_changed = False
        if img is not None and id(img) != getattr(self, '_last_us_cal_img_id', None):
            self.widget.roi_widget.plot_us_calibration_image(img)
            self._last_us_cal_img_id = id(img)
            img_changed = True
        elif img is None and getattr(self, '_last_us_cal_img_id', None) is not None:
            self.widget.roi_widget.us_cal_img_widget.pg_img_item.clear()
            self._last_us_cal_img_id = None
            img_changed = True
        cal_x, cal_y = cfg.us_temperature_model.calibration_spectrum.data
        self.widget.roi_widget.plot_us_calibration_spectrum(cal_x, cal_y)
        if img_changed:
            self._refresh_cross_mode_cal_viewers()
            self._refresh_roi_panels()

    def set_frame_text(self, txt):
        self.widget.frame_num_txt.blockSignals(True)
        self.widget.frame_num_txt.setText(txt)
        self.widget.frame_num_txt.clearFocus()
        self.widget.frame_num_txt.blockSignals(False)
        self._update_time_lapse_frame_marker()

    def _is_cal_kinetics_adapted(self, cfg, side):
        """True when the cal image is a full-chip 2D frame and data is a
        kinetics readout window — i.e. cross-mode ROI mirroring is in play.
        Applies to both interleaved and non-interleaved kinetics."""
        if getattr(cfg, 'kinetics_mode', 'off') not in ('kinetics-interleaved', 'kinetics'):
            return False
        cal_file = cfg.ds_calibration_img_file if side == 'ds' else cfg.us_calibration_img_file
        if cal_file is None or getattr(cal_file, 'img', None) is None:
            return False
        # DEBUG: try/except removed so failures raise with full traceback.
        shape = cal_file.img.shape
        if len(shape) != 2:
            return False
        if cfg.data_img_file is None:
            return False
        # DEBUG: try/except removed so failures raise with full traceback.
        data_dim = cfg.data_img_file.get_dimension()
        cal_dim = (shape[1], shape[0])
        return cal_dim != data_dim

    def _cal_mode_tag(self, cfg, side):
        """Short suffix describing the cal readout mode + adaptation status,
        appended to the cal filename label so the user can tell at a glance
        whether a full-chip or kinetics-mode cal is loaded.

        Returns '' if no cal is loaded; otherwise one of:
          '(full-chip)'                — normal cal, no adaptation needed
          '(full-chip, auto-adapted)'  — normal cal + kinetics data (cross-mode)
          '(kinetics)'                 — kinetics-mode cal
        """
        cal_file = cfg.ds_calibration_img_file if side == 'ds' else cfg.us_calibration_img_file
        if cal_file is None:
            return ''
        cal_mode = str(getattr(cal_file, 'readout_mode', '') or '').lower()
        if cal_mode == 'kinetics':
            return '(kinetics)'
        # Fall back to inference for cals loaded from .trs (stub DataModel
        # has no readout_mode): if cal shape matches the currently-loaded
        # kinetics data shape, treat the cal as kinetics-mode.
        if (cal_mode == '' and cfg.kinetics_mode in ('kinetics-interleaved', 'kinetics')
                and cfg.data_img_file is not None):
            cal_shape = cal_file.img.shape
            data_dim = cfg.data_img_file.get_dimension()
            if len(cal_shape) == 2 and (cal_shape[1], cal_shape[0]) == data_dim:
                return '(kinetics)'
        if self._is_cal_kinetics_adapted(cfg, side):
            return '(full-chip, auto-adapted)'
        return '(full-chip)'

    def ds_calculations_changed(self):
        self._refresh_configuration_buttons()
        self._push_ds_calibration_view()
        cfg = self.model.current_configuration
        # Use displayed_frame('ds') — in sync-frame / lab-time modes with
        # q_ds != q_us, cfg.current_frame is DS's frame; but if we ever
        # displayed the wrong index for a side the fit-curve gate would
        # disagree with the T-text. Route both through the same accessor.
        f_ds = cfg.displayed_frame('ds')
        ds_fit_ok = f_ds is not None and cfg.frame_is_displayable('ds', f_ds)
      

        if self.model.current_configuration.ds_calibration_filename is not None:
            label = str(os.path.basename(self.model.current_configuration.ds_calibration_filename))
            tag = self._cal_mode_tag(self.model.current_configuration, 'ds')
            if tag:
                label = f"{label} {tag}"
            self.widget.ds_calibration_filename_lbl.setText(label)
        else:
            self.widget.ds_calibration_filename_lbl.setText('Select File...')

        self.widget.ds_standard_filename_lbl.setText(str(os.path.basename(self.model.current_configuration.ds_standard_filename)))
        # Programmatic sync: block signals so the toggled→set_ds_calibration_modus
        # side-effect doesn't mark the config dirty just from refreshing the UI.
        self.widget.ds_standard_rb.blockSignals(True)
        self.widget.ds_standard_rb.setChecked(self.model.current_configuration.ds_temperature_model.calibration_parameter.modus)
        self.widget.ds_standard_rb.blockSignals(False)
        self.widget.ds_temperature_txt.setText(str(self.model.current_configuration.ds_temperature_model.calibration_parameter.temperature))

        if len(self.model.current_configuration.ds_corrected_spectrum):
            ds_plot_spectrum = self.model.current_configuration.ds_corrected_spectrum
        else:
            ds_plot_spectrum = self.model.current_configuration.ds_data_spectrum

        if self.widget.two_color_btn.isChecked() and self.model.current_configuration.ds_temperature != 0 and ds_fit_ok:
            lam, temp = self.model.current_configuration.ds_2_color_temp
            self.widget.temperature_spectrum_widget.plot_ds_data(lam, temp)
        else:
            self.widget.temperature_spectrum_widget.plot_ds_data(*ds_plot_spectrum.data,mask=ds_plot_spectrum.mask)
            self.widget.temperature_spectrum_widget.plot_ds_masked_data(*ds_plot_spectrum.data,mask=ds_plot_spectrum.mask)
        if self.model.current_configuration.ds_temperature != 0 and ds_fit_ok and self.model.current_configuration.ds_temperature_error <= self.model.current_configuration.error_limit:
            if not self.widget.two_color_btn.isChecked() :
                self.widget.temperature_spectrum_widget.plot_ds_fit(*self.model.current_configuration.ds_fit_spectrum.data)
            else:
                self.widget.temperature_spectrum_widget.plot_ds_fit([],[])
            self.widget.temperature_spectrum_widget.update_ds_temperature_txt(self.model.current_configuration.ds_temperature,
                                                           self.model.current_configuration.ds_temperature_error)
           
            
        else:
            self.widget.temperature_spectrum_widget.plot_ds_fit([],[])
            self.widget.temperature_spectrum_widget.update_ds_temperature_txt(0,
                                                           0)
        self.widget.roi_widget.specra_widget.plot_ds_data(*self.model.current_configuration.ds_temperature_model.data_spectrum.data)

        
        cfg = self.model.current_configuration
        ds_sat = cfg.saturation_limit(cfg.data_img)
        ds_fmt_max = 65536 if not np.isfinite(ds_sat) else float(ds_sat) + 2
        self.widget.temperature_spectrum_widget.update_ds_roi_max_txt(
            cfg.ds_temperature_model.data_roi_max, format_max=ds_fmt_max)

        f = self.model.current_configuration.ds_fringe_frequency
        nd = self.model.current_configuration.ds_fringe_nd_um
        self.widget.filter_section.ds_fringe_lbl.setText(f'{f:.4f}' if f is not None else '—')
        self.widget.filter_section.ds_nd_lbl.setText(f'{nd:.1f}' if nd is not None else '—')

        # Live EPICS publish only in 'Current frame' mode — aggregate modes
        # publish once per file load via _send_temperature_trigger to avoid
        # flicker as the user browses frames.
        if (self.widget.connect_to_epics_cb.isChecked()
                and self._current_multiframe_mode(self.model.current_configuration) == "single"):
            if self.epics_available:
                ds_temp_pv = eps.epics_settings['ds_last_temp']
                if ds_temp_pv is not None and not ds_temp_pv == '' and not ds_temp_pv == 'None':
                    caput(ds_temp_pv, self.model.current_configuration.ds_temperature)

        # Re-normalize Y range after ds_mx / ds_fit_mx were updated by the
        # plot_* calls above. The earlier normalize_range in
        # _refresh_configuration_buttons ran with stale mx values (1-tick
        # behind), which was fine for Princeton data but pins Photron
        # spectra to the floor when a stale huge mx sticks around.
        self.widget.temperature_spectrum_widget.normalize_range()

    def us_calculations_changed(self):
        self._refresh_configuration_buttons()
        # In single-sided mode there is no us data to render or publish.
        if self.model.current_configuration.mode == 'single':
            return
        self._push_us_calibration_view()
        cfg = self.model.current_configuration
        # displayed_frame('us') — in sync modes this is US's readout frame,
        # NOT the DS frame that cfg.current_frame points at. Prior code
        # indexed us_temperatures with current_frame, which was DS's index
        # in sync mode and produced a fit-curve/T-text gate that disagreed
        # with the actual US readout.
        f_us = cfg.displayed_frame('us')
        us_fit_ok = f_us is not None and cfg.frame_is_displayable('us', f_us)
 
        if self.model.current_configuration.us_calibration_filename is not None:
            label = str(os.path.basename(self.model.current_configuration.us_calibration_filename))
            tag = self._cal_mode_tag(self.model.current_configuration, 'us')
            if tag:
                label = f"{label} {tag}"
            self.widget.us_calibration_filename_lbl.setText(label)
        else:
            self.widget.us_calibration_filename_lbl.setText('Select File...')

        self.widget.us_standard_filename_lbl.setText(str(os.path.basename(self.model.current_configuration.us_standard_filename)))
        self.widget.us_standard_rb.blockSignals(True)
        self.widget.us_standard_rb.setChecked(self.model.current_configuration.us_temperature_model.calibration_parameter.modus)
        self.widget.us_standard_rb.blockSignals(False)
        self.widget.us_temperature_txt.setText(str(self.model.current_configuration.us_temperature_model.calibration_parameter.temperature))

        if len(self.model.current_configuration.us_corrected_spectrum):
            us_plot_spectrum = self.model.current_configuration.us_corrected_spectrum
        else:
            us_plot_spectrum = self.model.current_configuration.us_data_spectrum
        if self.widget.two_color_btn.isChecked() and self.model.current_configuration.us_temperature != 0 and us_fit_ok:
            lam, temp = self.model.current_configuration.us_2_color_temp
            self.widget.temperature_spectrum_widget.plot_us_data(lam, temp)
        else:
            self.widget.temperature_spectrum_widget.plot_us_data(*us_plot_spectrum.data,mask=us_plot_spectrum.mask)
            self.widget.temperature_spectrum_widget.plot_us_masked_data(*us_plot_spectrum.data,mask=us_plot_spectrum.mask)
        if self.model.current_configuration.us_temperature != 0 and us_fit_ok and self.model.current_configuration.us_temperature_error <= self.model.current_configuration.error_limit:
            if not self.widget.two_color_btn.isChecked() :
                
                self.widget.temperature_spectrum_widget.plot_us_fit(*self.model.current_configuration.us_fit_spectrum.data)
            else:
                self.widget.temperature_spectrum_widget.plot_us_fit([],[])
            self.widget.temperature_spectrum_widget.update_us_temperature_txt(self.model.current_configuration.us_temperature,
                                                           self.model.current_configuration.us_temperature_error)
            
            lam, temp = self.model.current_configuration.us_2_color_temp

        else:
            self.widget.temperature_spectrum_widget.plot_us_fit([],[])
            self.widget.temperature_spectrum_widget.update_us_temperature_txt(0,
                                                           0)
        self.widget.roi_widget.specra_widget.plot_us_data(*self.model.current_configuration.us_temperature_model.data_spectrum.data)

        
        cfg = self.model.current_configuration
        us_sat = cfg.saturation_limit(cfg.data_img)
        us_fmt_max = 65536 if not np.isfinite(us_sat) else float(us_sat) + 2
        self.widget.temperature_spectrum_widget.update_us_roi_max_txt(
            cfg.us_temperature_model.data_roi_max, format_max=us_fmt_max)

        f = self.model.current_configuration.us_fringe_frequency
        nd = self.model.current_configuration.us_fringe_nd_um
        self.widget.filter_section.us_fringe_lbl.setText(f'{f:.4f}' if f is not None else '—')
        self.widget.filter_section.us_nd_lbl.setText(f'{nd:.1f}' if nd is not None else '—')

        # Live EPICS publish only in 'Current frame' mode — see ds_calculations_changed.
        if (self.widget.connect_to_epics_cb.isChecked()
                and self._current_multiframe_mode(self.model.current_configuration) == "single"):
            if self.epics_available:
                us_temp_pv = eps.epics_settings['us_last_temp']
                if us_temp_pv is not None and not us_temp_pv =='' and not us_temp_pv == 'None':
                    caput(us_temp_pv, self.model.current_configuration.us_temperature)

        # See ds_calculations_changed — re-normalize after plot_us_* set
        # the new us_mx / us_fit_mx so the Y range reflects this refresh.
        self.widget.temperature_spectrum_widget.normalize_range()

    def _default_history_axis_for_kinetics(self):
        """After a file load, if the newly-loaded file is a kinetics dataset AND
        the history axis is still the initial 'frame' default, switch to
        'sync_frame' so DS and US are aligned by physical exposure out of the box.

        Only bumps the mode when it's on 'frame' — this is a first-time default,
        not a policy override. Once the user has explicitly picked lab-time or
        stayed on frame mode by clicking away from sync, the choice sticks
        across subsequent file loads.
        """
        cfg = self.model.current_configuration
        if cfg is None or cfg.data_img_file is None:
            return
        if getattr(cfg, 'kinetics_mode', 'off') not in ('kinetics-interleaved', 'kinetics'):
            return
        if self._history_x_mode != 'frame':
            return
        # Route through the button toggle so all downstream wiring runs
        # (badge, _on_history_mode_changed, _refresh_multiframe_ui, redraw).
        self.widget.sync_frame_btn.setChecked(True)

    def _kinetics_mode_combo_changed(self, idx):
        """User picked a new kinetics mode from the KineticsGB combo.
        Index 0 clears the override (Auto); 1/2 force the mode. The model
        setter re-runs _sync_kinetics_from_file and emits data_changed_signal,
        which repopulates the combo — safely no-op via blockSignals in
        KineticsGB.apply_kinetics_state."""
        cfg = self.model.current_configuration
        if cfg is None:
            return
        items = self.widget.kinetics_gb.MODE_COMBO_ITEMS
        if not (0 <= idx < len(items)):
            return
        _, override = items[idx]
        cfg.set_kinetics_mode(override)

    def _camera_mode_changed(self, index):
        """User picked a new camera type in CameraModeGB. Index 0 → Default
        ('off'), index 1 → Photron ('centered'). Drives photron_mode on the
        current configuration and reshuffles which UI groups are visible.
        """
        items = self.widget.camera_mode_gb.MODE_ITEMS
        if not (0 <= index < len(items)):
            return
        _, mode = items[index]
        cfg = self.model.current_configuration
        if cfg is None:
            self._apply_photron_styling(mode == 'centered')
            return
        cfg.set_photron_mode(mode)
        self._apply_photron_styling(mode == 'centered')
        self._sync_cal_background_widgets()

    def _apply_photron_styling(self, photron_active: bool):
        """Show/hide UI groups based on whether the configuration is in
        Photron mode. Groups hidden in Photron mode: KineticsGB, the
        kinetics-strip ROI panel. Groups shown only in Photron mode: the
        decoupled cal-background subtraction group (managed separately by
        _sync_cal_background_widgets; kept here for symmetry).

        When photron is inactive, kinetics_gb and roi_kin_gb visibility is
        governed by kinetics_mode (apply_kinetics_state and _refresh_roi_panels),
        not by this method — force-showing here would override the kinetics-off
        hide and leave the panels visible for non-kinetics files (e.g. PIXIS).
        """
        if photron_active:
            self.widget.kinetics_gb.setVisible(False)
            self.widget.roi_kin_gb.setVisible(False)
        # Retitle data-side groups so their scope is unambiguous when the
        # decoupled cal-bg controls are visible alongside them.
        if photron_active:
            self.widget.background_subtraction_gb.setTitle(
                'Data background subtraction')
            self.widget.roi_gb.setTitle('Data ROIs (full-chip)')
        else:
            self.widget.background_subtraction_gb.setTitle(
                'Background subtraction')
            self.widget.roi_gb.setTitle('ROI (full-chip)')
        # Wavelength calibration is Photron-only: TIFFs carry no wavelength
        # metadata so the user loads a calibration.json to define the x-axis.
        # PI SPE files already embed calibration and the panel would be a
        # dead control.
        self.widget.wavelength_calibration_gb.setVisible(photron_active)
        # Cal-bg GB visibility is authoritative-set by
        # _sync_cal_background_widgets (it re-checks cfg.photron_mode).

    def lab_time_btn_toggled(self, checked):
        prev = self._history_x_mode
        if checked:
            self._history_x_mode = 'time'
            if self.widget.sync_frame_btn.isChecked():
                self.widget.sync_frame_btn.blockSignals(True)
                self.widget.sync_frame_btn.setChecked(False)
                self.widget.sync_frame_btn.blockSignals(False)
        elif self._history_x_mode == 'time':
            self._history_x_mode = 'frame'
        self._on_history_mode_changed(prev)
        # Axis-mode switch changes what the multi-frame range spinboxes mean
        # (readout index vs coincident-k). Refresh their bounds/tooltips.
        self._refresh_multiframe_ui()
        self.redraw_time_lapse()

    def sync_frame_btn_toggled(self, checked):
        prev = self._history_x_mode
        if checked:
            self._history_x_mode = 'sync_frame'
            if self.widget.lab_time_btn.isChecked():
                self.widget.lab_time_btn.blockSignals(True)
                self.widget.lab_time_btn.setChecked(False)
                self.widget.lab_time_btn.blockSignals(False)
        elif self._history_x_mode == 'sync_frame':
            self._history_x_mode = 'frame'
        self._on_history_mode_changed(prev)
        # Axis-mode switch changes what the multi-frame range spinboxes mean.
        self._refresh_multiframe_ui()
        self.redraw_time_lapse()

    def _on_history_mode_changed(self, prev_mode):
        """When entering a synced mode, load the coincident k that keeps DS's
        current readout frame in place. When returning to frame mode, load
        the DS readout frame as the single displayed frame."""
        cfg = self.model.current_configuration
        if cfg.data_img_file is None:
            return
        n = int(getattr(cfg.data_img_file, 'num_frames', 0) or 0)
        if n <= 1:
            return
        prev_synced = prev_mode in ('time', 'sync_frame')
        now_synced = self._history_x_mode in ('time', 'sync_frame')
        if now_synced and not prev_synced:
            q_ds = cfg._q_side('ds')
            q_max = max(q_ds, cfg._q_side('us'))
            f = int(cfg.current_frame)
            k = f - q_ds + q_max + 1
            rng = cfg.get_coincident_frame_range()
            if rng is not None:
                k = max(rng[0], min(rng[1], k))
            self._load_coincident_k(k)
        elif prev_synced and not now_synced:
            f = int(cfg.current_frame_ds if cfg.current_frame_ds is not None
                    else cfg.current_frame)
            f = max(0, min(n - 1, f))
            # Use set_img_frame_numbers (not load_any_img_frame) so both sides
            # re-run the pipeline even when DS's readout frame is unchanged;
            # US was pointing at a different readout under the synced pairing
            # and needs to be reset to match DS's frame.
            cfg.set_img_frame_numbers(f, f)
            self.set_frame_text(str(cfg.current_frame + 1))

    def _update_time_lapse_frame_marker(self):
        """Position the DS/US vertical markers on the history plot for the
        currently displayed frame. In synced modes DS and US come from the
        same coincident k so the markers collapse to a single x per side
        (and the two coincide); markers for a side with no valid readout at
        this k are hidden. Hides both when no multi-frame data is loaded."""
        cfg = self.model.current_configuration
        img = cfg.data_img_file
        if img is None or int(getattr(img, 'num_frames', 0) or 0) <= 1:
            self.widget.temperature_spectrum_widget.set_time_lapse_frame_marker(None, None)
            return
        if self._history_x_mode in ('time', 'sync_frame'):
            f_ds = cfg.current_frame_ds
            f_us = cfg.current_frame_us
            def _x_for(side, f):
                if f is None:
                    return None
                result = cfg.get_side_frame_time_axis(side)
                if result is None or f >= len(result[0]):
                    return None
                t = float(result[0][int(f)])
                if self._history_x_mode == 'sync_frame':
                    t_exp = float(getattr(img, 'exposure_time', 0) or 0.0)
                    return t / t_exp + 1.0 if t_exp > 0 else None
                return t
            ds_x = _x_for('ds', f_ds)
            us_x = _x_for('us', f_us)
        else:
            f = int(cfg.current_frame)
            ds_x = us_x = float(f + 1)
        self.widget.temperature_spectrum_widget.set_time_lapse_frame_marker(ds_x, us_x)

    def _history_axis_range_positions(self, cfg, ui_start, ui_end):
        """Convert the range-spinbox values into (x_lo, x_hi) on the
        history-plot x-axis, placed *between* points so the visible band
        strictly contains the aggregated frames.

        Interpretation follows the current axis mode:
          - 'frame' mode: ui values are readout-frame indices (0-based). Plot
            x for frame f is (f+1), so range [a, b] occupies x in [a+1, b+1];
            markers at a+0.5 and b+1.5.
          - 'sync_frame' mode: ui values are coincident k (1-based); plot x
            equals k. Markers at k_lo - 0.5 and k_hi + 0.5.
          - 'time' mode: ui values are k, plot x is lab-time seconds
            = (k - 1) * t_exp. Markers translated accordingly.
        """
        mode_axis = getattr(self, '_history_x_mode', 'frame')
        if mode_axis == 'sync_frame':
            return float(ui_start) - 0.5, float(ui_end) + 0.5
        if mode_axis == 'time':
            data_file = getattr(cfg, 'data_img_file', None)
            t_exp = float(getattr(data_file, 'exposure_time', 0) or 0.0)
            if t_exp <= 0:
                return None, None
            return (float(ui_start) - 1.5) * t_exp, (float(ui_end) - 0.5) * t_exp
        # 'frame' axis
        return float(ui_start) + 0.5, float(ui_end) + 1.5

    def _time_lapse_x_axis_side(self, side, y):
        """Return (x, y, label) for one side's history-plot data.

        Modes:
          'frame'      : raw readout frame index, x = 1..N (no per-side sync)
          'time'       : lab time in seconds, x = (f - q_side) · t_exp
          'sync_frame' : coincident-exposure frame index, x = f - q_side + 1

        In 'time' and 'sync_frame' modes DS and US share the same x when they
        come from the same physical exposure (q_side = mask offset in slots).
        All N frames are kept; the per-side valid mask is advisory only."""
        n = len(y)
        if self._history_x_mode in ('time', 'sync_frame'):
            result = self.model.current_configuration.get_side_frame_time_axis(side)
            if result is not None:
                times, _valid = result
                if len(times) >= n:
                    t_exp = float(getattr(
                        self.model.current_configuration.data_img_file,
                        'exposure_time', 0) or 0.0)
                    if self._history_x_mode == 'sync_frame' and t_exp > 0:
                        # times = (k-1)·t_exp → k = times/t_exp + 1
                        return times[:n] / t_exp + 1.0, y, 'Frame (synced)'
                    return times[:n], y, 'Time (s)'
        return np.arange(1, n + 1), y, 'Frame'

    def update_time_lapse(self):
        # this actually fits all the frames so be careful calling this willy nilly
        us_temperature, us_temperature_error, ds_temperature, ds_temperature_error = self.model.current_configuration.fit_all_frames()
        self._render_time_lapse(ds_temperature, ds_temperature_error,
                                us_temperature, us_temperature_error)

    def redraw_time_lapse(self):
        """Re-render the history plot using the cached fit results on the
        current configuration — no re-fitting. Falls back to a full
        update_time_lapse() when no cache is available."""
        cfg = self.model.current_configuration
        ds_temperature = getattr(cfg, 'ds_temperatures', None) or []
        us_temperature = getattr(cfg, 'us_temperatures', None) or []
        if not len(ds_temperature) and not len(us_temperature):
            self.update_time_lapse()
            return
        self._render_time_lapse(ds_temperature,
                                getattr(cfg, 'ds_temperatures_errors', []) or [],
                                us_temperature,
                                getattr(cfg, 'us_temperatures_errors', []) or [])

    def _render_time_lapse(self, ds_temperature, ds_temperature_error,
                           us_temperature, us_temperature_error):
        """Draw the time-lapse traces and the DS/US/Combined aggregate labels.

        The plot trace always shows every frame (with invalid frames zeroed for
        clarity). The label values respect the shared 'Multi-frame output' mode:
        in 'single' mode the labels fall back to mean+std over all valid frames
        (a scalar 'current frame' doesn't summarize the plot); in aggregate
        modes the labels report the same value that would be published to ZMQ
        and EPICS (per the mode + range + error-metric settings).
        """
        cfg = self.model.current_configuration
        mode = self._current_multiframe_mode(cfg)

        # Range for the label aggregate. In aggregate range_* modes the
        # spinbox values need per-side readout translation (kinetics-sync mode)
        # because DS and US may map to different readout frames for the same
        # coincident-k range. The plot trace itself always shows every frame.
        gb = self.widget.multiframe_output_gb
        if mode in ("mean_range", "median_range"):
            # (None, None) here means 'no valid frames on this side in the
            # requested k range' (only reachable in kinetics-sync mode).
            ui_start, ui_end = gb.range_start_sb.value(), gb.range_end_sb.value()
            ds_range = self._readout_range_for_side(cfg, 'ds', ui_start, ui_end)
            us_range = self._readout_range_for_side(cfg, 'us', ui_start, ui_end)
        elif mode in ("mean_all", "median_all"):
            # Full-array aggregate — set explicit endpoints so the combined
            # slicer includes every frame. (None, None) here would be read as
            # 'empty' and collapse the concatenation to nothing.
            ds_range = (0, len(ds_temperature) - 1) if len(ds_temperature) else (None, None)
            us_range = (0, len(us_temperature) - 1) if len(us_temperature) else (None, None)
        else:
            ds_range = (None, None)
            us_range = (None, None)
        agg_op = "mean" if mode.startswith("mean") else "median"
        err_metric = gb.error_metric_cb.currentData() or "fit_avg"

        def side_label(temps, errs, side_range):
            """Return (value, error) for the label above the plot on one side."""
            if not len(temps):
                return np.nan, np.nan
            if mode == "single":
                # Fallback: keep the informative "mean over all valid frames"
                # summary regardless of what 'current frame' would print.
                ds_arr = np.array(temps)
                ds_err_arr = np.array(errs)
                select = (
                    (ds_arr > self.min_allowed_T) & (ds_arr < self.max_allowed_T)
                    & (ds_err_arr < cfg.error_limit)
                )
                ds_v = ds_arr[select]
                if len(ds_v):
                    return float(np.mean(ds_v)), float(np.std(ds_v))
                return np.nan, np.nan
            if side_range == (None, None) and mode in ("mean_range", "median_range"):
                # k range didn't cover any readout frames on this side.
                return np.nan, np.nan
            start, end = side_range if side_range[0] is not None else (None, None)
            v, e = self._aggregate_temperature_series(cfg, temps, errs, agg_op, err_metric,
                                                     start=start, end=end)
            return (v if v is not None else np.nan,
                    e if e is not None else np.nan)

        # ---- DS trace + label ----
        if len(ds_temperature):
            ds_temperature_arr = np.array(ds_temperature)
            ds_temperature_error_arr = np.array(ds_temperature_error)
            select_ds  = (ds_temperature_arr > self.min_allowed_T) & (ds_temperature_arr < self.max_allowed_T) & (ds_temperature_error_arr < cfg.error_limit)
            ds_temperature_plot_data = ds_temperature_arr[:]
            ds_temperature_plot_data[~select_ds] = 0
            ds_x, ds_y, x_label = self._time_lapse_x_axis_side('ds', ds_temperature_plot_data)
            self.widget.temperature_spectrum_widget.plot_ds_time_lapse(ds_x, ds_y)
            self.widget.temperature_spectrum_widget.set_time_lapse_x_axis_label(x_label)
        self.widget.temperature_spectrum_widget.update_time_lapse_ds_temperature_txt(
            *side_label(ds_temperature, ds_temperature_error, ds_range))

        # ---- US trace + label ----
        if len(us_temperature):
            us_temperature_arr = np.array(us_temperature)
            us_temperature_error_arr = np.array(us_temperature_error)
            select_us  = (us_temperature_arr > self.min_allowed_T) & (us_temperature_arr < self.max_allowed_T) & (us_temperature_error_arr < cfg.error_limit)
            us_temperature_plot_data = us_temperature_arr[:]
            us_temperature_plot_data[~select_us] = 0
            us_x, us_y, _ = self._time_lapse_x_axis_side('us', us_temperature_plot_data)
            self.widget.temperature_spectrum_widget.plot_us_time_lapse(us_x, us_y)
        self.widget.temperature_spectrum_widget.update_time_lapse_us_temperature_txt(
            *side_label(us_temperature, us_temperature_error, us_range))

        # ---- Combined DS+US label ----
        if mode == "single":
            # Concatenate the display-filtered sides and take mean+std (today's semantics).
            ds_v = us_v = np.array([])
            if len(ds_temperature):
                ds_arr = np.array(ds_temperature); ds_err_arr = np.array(ds_temperature_error)
                sel = ((ds_arr > self.min_allowed_T) & (ds_arr < self.max_allowed_T)
                       & (ds_err_arr < cfg.error_limit))
                ds_v = ds_arr[sel]
            if len(us_temperature):
                us_arr = np.array(us_temperature); us_err_arr = np.array(us_temperature_error)
                sel = ((us_arr > self.min_allowed_T) & (us_arr < self.max_allowed_T)
                       & (us_err_arr < cfg.error_limit))
                us_v = us_arr[sel]
            if len(ds_v) or len(us_v):
                conc = np.concatenate((ds_v, us_v))
                combined_out = float(np.mean(conc)), float(np.std(conc))
            else:
                combined_out = np.nan, np.nan
        else:
            # Aggregate modes: slice each side by its own per-side readout range
            # first (so kinetics offsets are respected), then concatenate and
            # reduce. Passing start=None/end=None to the helper means "use
            # everything" — that's what we want for mean_all/median_all.
            def _sliced(temps, side_range):
                a = np.asarray(temps, dtype=float)
                if a.size == 0 or side_range == (None, None):
                    return np.array([], dtype=float)
                s, e = side_range
                if s is None:
                    return a
                return a[s:e + 1]
            ds_slice_t = _sliced(ds_temperature,       ds_range)
            ds_slice_e = _sliced(ds_temperature_error, ds_range)
            us_slice_t = _sliced(us_temperature,       us_range)
            us_slice_e = _sliced(us_temperature_error, us_range)
            combined_temps = np.concatenate((ds_slice_t, us_slice_t))
            combined_errs  = np.concatenate((ds_slice_e, us_slice_e))
            v, e = self._aggregate_temperature_series(cfg, combined_temps, combined_errs,
                                                     agg_op, err_metric)
            combined_out = (v if v is not None else np.nan,
                            e if e is not None else np.nan)

        # Annotation string under the big combined value — tells the user what
        # was reduced. Uses the same unit the user sees in the mode panel
        # (readout-frame index vs coincident-k, per the active history axis).
        if mode == "single":
            annotation = "mean · all valid frames"
        else:
            op_label = "mean" if agg_op == "mean" else "median"
            unit = "synced frames" if self._kinetics_sync_active(cfg) else "frames"
            if mode in ("mean_range", "median_range"):
                ui_start, ui_end = gb.range_start_sb.value(), gb.range_end_sb.value()
                span = f"{unit} {ui_start}–{ui_end}"
            else:
                span = f"all {unit}"
            err_label = "fit err" if err_metric == "fit_avg" else "std"
            annotation = f"{op_label} · {span} · {err_label}"

        self.widget.temperature_spectrum_widget.update_time_lapse_combined_temperature_txt(
            *combined_out, annotation=annotation)

        # Range-overlay markers: visible only in range_* modes. Placed between
        # points so the visible band strictly contains the aggregated frames.
        if mode in ("mean_range", "median_range"):
            ui_start, ui_end = gb.range_start_sb.value(), gb.range_end_sb.value()
            x_lo, x_hi = self._history_axis_range_positions(cfg, ui_start, ui_end)
            self.widget.temperature_spectrum_widget.set_time_lapse_range_markers(x_lo, x_hi)
        else:
            self.widget.temperature_spectrum_widget.set_time_lapse_range_markers(None, None)

        self._update_time_lapse_frame_marker()

    def data_history_btn_callback(self):
        self.data_history_widget.raise_widget()

    

    def widget_rois_changed(self, roi_list):
        if self.model.current_configuration.has_data():


            self.model.current_configuration.set_rois(roi_list)
            wl_range = self.model.current_configuration.wl_range
            self.widget.roi_widget.set_wl_range(wl_range)
            # Data-viewer drag updates the cal-dim ROI in the model via the
            # quotient-preserving mirror; refresh cross-mode cal viewers so
            # the cal 2D overlay follows.
            self._refresh_cross_mode_cal_viewers()
            # bg-ROI move changes the diagnostic BG Trend view (crop shifts).
            self._refresh_bg_stack()
            self._refresh_roi_panels()
            # Data-viewer drags (or cal-viewer drags mirrored back in non
            # cross-mode) can change the same-dim ROI values shown in the
            # cal-ROI panel; refresh it here since drag paths do not fire
            # data_changed_signal.
            self._refresh_cal_roi_panel()

    def _refresh_roi_panels(self):
        """Sync the ROI widget's kinetics-projection context to the current
        configuration. Drives visibility of the read-only 'ROI (kinetics
        strip)' panel and full-chip vs strip-Y display in the main panel
        (signal projected via window_y; bg from cal-dim cross-mode info
        when available, else disabled with 'N/A').

        The strip panel is only shown for interleaved kinetics, where a
        strip↔full-chip Y projection is needed. Non-interleaved 'kinetics'
        has one strip per frame — the main panel shows raw frame Y, which
        equals the physical chip row when window_y=0."""
        cfg = self.model.current_configuration
        rw = self.widget.roi_widget
        if cfg is None:
            rw.set_kinetics_context(False, 0, 0, None, None)
            return
        active = (cfg.kinetics_mode == 'kinetics-interleaved')
        ki = cfg.kinetics_info or {}
        win_y = int(ki.get('window_y', 0) or 0)
        win_h = int(ki.get('window_height', 0) or 0)
        ds_cross = cfg.cross_mode_cal_info('ds')
        us_cross = cfg.cross_mode_cal_info('us')
        ds_bg = ds_cross['bg_roi_limits'] if ds_cross else None
        us_bg = us_cross['bg_roi_limits'] if us_cross else None
        rw.set_kinetics_context(active, win_y, win_h, ds_bg, us_bg)

    def _refresh_bg_stack(self):
        """Rebuild the 'BG Trend' diagnostic image (vstacked bg-ROI slices).
        Called on data change and ROI change. Hides the tab when the
        current data doesn't support it (single-frame, no data)."""
        cfg = self.model.current_configuration
        if cfg is None or cfg.data_img_file is None:
            self.widget.roi_widget.plot_bg_stack(None)
            return
        # DS bg-ROI (index 2) — conventionally identical to US bg in kinetics.
        img = cfg.compute_bg_stack_image('ds')
        self.widget.roi_widget.plot_bg_stack(img)

    def _refresh_cross_mode_cal_viewers(self):
        """Re-apply per-side cross-mode setup on the cal 2D viewers so a
        drag on either the data viewer or the cal viewer keeps both
        displays in sync with the current cal-dim / kinetics-dim ROI
        pair stored in RoiDataManager. Also handles the transition back
        from cross-mode → same-dim (e.g. after switching to a kinetics
        cal) by restoring the shared wavelength rect + shared signal ROI
        on the cal viewer."""
        cfg = self.model.current_configuration
        if cfg is None or cfg.x_calibration is None or cfg.data_img is None:
            return
        wl = cfg.x_calibration
        x = round(wl[0], 3)
        w = round(wl[-1] - wl[0], 3)
        data_h = cfg.data_img.shape[0]
        shared_rect = (x, 0, w, data_h)
        rois = cfg.get_roi_data_list()
        for side in ('ds', 'us'):
            info = cfg.cross_mode_cal_info(side)
            if info is not None:
                cal_h = info['cal_shape'][0]
                self.widget.roi_widget.set_cross_mode_cal(
                    side, info['cal_shape'],
                    info['signal_roi_limits'],
                    (x, 0, w, cal_h),
                    bg_roi_limits=info['bg_roi_limits'])
            else:
                signal_idx = 0 if side == 'ds' else 1
                bg_idx = 2 if side == 'ds' else 3
                self.widget.roi_widget.clear_cross_mode_cal(
                    side, shared_rect, rois[signal_idx],
                    shared_bg_roi=rois[bg_idx])

    def cal_signal_roi_dragged(self, side, cal_dim_limits):
        """User dragged the signal ROI on a cal 2D viewer while that side
        is in cross-mode. Update the cal-dim ROI in the model, which
        re-derives the kinetics-dim ROI via _sync_cross_mode_rois, then
        re-run the pipeline and refresh the kinetics-side view."""
        cfg = self.model.current_configuration
        if cfg is None:
            return
        cfg.set_cal_dim_signal_roi(side, cal_dim_limits)
        # Push the (now updated) kinetics-dim ROI back into the data viewer
        # by re-running through set_rois — that fires the normal pipeline
        # and keeps the ROI text boxes / data-viewer overlay consistent.
        rois = cfg.get_roi_data_list()
        cfg.set_rois(rois)
        self.widget.roi_widget.set_rois(rois)
        self._refresh_cal_roi_panel()

    def cal_bg_roi_dragged(self, side, cal_dim_limits):
        """User dragged the bg ROI on a cal 2D viewer in cross-mode. The
        cal-dim bg is the bg that applies to the full-chip cal image
        itself and is stored independently of the kinetics-data bg (no
        physical projection between the two — see _sync_cross_mode_rois).
        Update cal-dim only; leave data-dim bg alone."""
        cfg = self.model.current_configuration
        if cfg is None:
            return
        cfg.set_cal_dim_bg_roi(side, cal_dim_limits)
        self._refresh_cal_roi_panel()

    def _pull_data_dim_limits(self, cfg, side):
        """Return {'signal':[x0,x1,y0,y1], 'bg':[...]} at data_dim for
        `side`. Used by the cal-ROI panel in NON cross-mode, where the
        cal extraction shares the data ROIs. Returns None if data_img
        is not loaded."""
        if cfg.data_img_file is None:
            return None
        try:
            data_dim = cfg.data_img_file.get_dimension()
        except Exception:
            return None
        sig_idx = 0 if side == 'ds' else 1
        bg_idx = 2 if side == 'ds' else 3
        sig = cfg.roi_data_manager.get_roi(sig_idx, data_dim)
        bg = cfg.roi_data_manager.get_roi(bg_idx, data_dim)
        return {
            'signal': [int(sig.x_min), int(sig.x_max),
                       int(sig.y_min), int(sig.y_max)],
            'bg': [int(bg.x_min), int(bg.x_max),
                   int(bg.y_min), int(bg.y_max)],
        }

    def _refresh_cal_roi_panel(self):
        """Push cal-dim (cross-mode) or data-dim (same-dim) ROI limits
        into the numeric cal-ROI panel. Called from data_changed."""
        cfg = self.model.current_configuration
        panel = self.widget.cal_roi_panel
        if cfg is None:
            panel.apply_state(None, None, None, None, False, False)
            return
        ds_present = cfg.ds_calibration_img_file is not None
        us_present = cfg.us_calibration_img_file is not None
        ds_cross = cfg.cross_mode_cal_info('ds') if ds_present else None
        us_cross = cfg.cross_mode_cal_info('us') if us_present else None
        ds_data = self._pull_data_dim_limits(cfg, 'ds') if ds_present else None
        us_data = self._pull_data_dim_limits(cfg, 'us') if us_present else None
        panel.apply_state(ds_cross, us_cross, ds_data, us_data,
                          ds_present, us_present)


    def widget_wl_range_changed_callback(self, wl_range):
        if self.model.current_configuration.has_data():
            self.model.current_configuration.wl_range = wl_range
            rois = self.model.current_configuration.get_roi_data_list()
            self.model.current_configuration.set_rois(rois)
            self.widget.roi_widget.set_rois(rois)

    def graph_mouse_moved(self, x, y):
        self.widget.graph_mouse_pos_lbl.setText("X: {:8.2f}  Y: {:8.2f}".format(x, y))

    def roi_mouse_moved(self, x, y):
        x = int(np.floor(x))
        y = int(np.floor(y))
        # DEBUG: try/except removed so failures raise with full traceback.
        if self.model.current_configuration.data_img is not None:
            s = self.model.current_configuration.data_img.shape
            if int(y)< s[0] and int(x)< s[1] and int(x) >= 0 and int(y) >= 0:
                self.widget.roi_widget.pos_lbl.setText("X: {:5.0f}  Y: {:5.0f}    Int: {:6.0f}    Wavelength: {:5.2f} nm".
                                                format(x, y,
                                                        self.model.current_configuration.data_img[int(y), int(x)],
                                                        self.model.current_configuration.data_img_file.x_calibration[int(x)]))

    def save_settings(self, settings):
        settings : AppSettings

        # Save
        
        configs = []
        for ind, conf in enumerate(self.model.configurations):
            if conf.setting_filename is None:
                continue
            # DEBUG: try/except removed so failures raise with full traceback.
            set_fname = os.path.split(conf.setting_filename)[-1]
            name_for_list = os.path.splitext(set_fname)[0]
            conf_dict = {
                "temperature settings directory": conf._setting_working_dir,
                "temperature settings file": name_for_list,
            }
            if conf.data_img_file:
                conf_dict["temperature data file"] = conf.data_img_file.filename
            configs.append(conf_dict)

        config_txt = json.dumps(configs)
        
        settings.set("temperature configurations",
                          config_txt)
        
        configuration_ind = self.model.configuration_ind
        settings.set("temperature configuration_ind",
                          configuration_ind)


        settings.set("temperature autoprocessing",
                          self.widget.autoprocess_cb.isChecked())

        settings.set("temperature epics connected",
                          self.widget.connect_to_epics_cb.isChecked())
        settings.set("temperature epics monitor folder",
                          self.widget.monitor_folder_cb.isChecked())

        zmq_config_path = self.zmq_worker_controller._config.get("_path", "")
        settings.set("zmq_config_path", zmq_config_path)
        settings.set("zmq_worker_listening",
                     self.zmq_worker_controller._thread is not None
                     and self.zmq_worker_controller._thread.isRunning())

        settings.set("zmq_publisher_config_path", self.zmq_publisher_controller._config_path)
        settings.set("zmq_publisher_connected",
                     self.zmq_publisher_controller._push_socket is not None)
        settings.set("zmq_publish_temperatures",
                     self.widget.epicslogger_gb.publish_temperatures_cb.isChecked())
        mf_gb = self.widget.multiframe_output_gb
        settings.set("multiframe_output_mode",         mf_gb.mode_cb.currentData() or "single")
        settings.set("multiframe_output_range_start",  mf_gb.range_start_sb.value())
        settings.set("multiframe_output_range_end",    mf_gb.range_end_sb.value())
        settings.set("multiframe_output_error_metric", mf_gb.error_metric_cb.currentData() or "fit_avg")

        # Persist the last-used experiment folder so file browse dialogs
        # (data / cal / dark / standard) open where the user last was,
        # not the OS default, on next launch.
        settings.set("temperature exp_working_dir", self._exp_working_dir or "")

        # Per-purpose last-used directories. Each file-picker keeps its own
        # slot so loading a wavelength calibration doesn't clobber the
        # data-file dir (see _last_dirs docstring).
        settings.set("temperature last_dirs",
                     json.dumps(self._last_dirs or {}))

        settings.dump()

    def load_conf_settings(self, conf):
        settings_file_path = os.path.join(str(conf["temperature settings directory"]),
                                          str(conf["temperature settings file"]) + ".trs")
        loaded_setting = False
        if os.path.exists(settings_file_path):
            self.load_setting_file(settings_file_path)
            loaded_setting = True
        if 'temperature data file' in conf:
            temperature_data_path = str(conf["temperature data file"])
            if os.path.exists(temperature_data_path) and len(temperature_data_path)>4:
                self.load_data_file(temperature_data_path)
        # Workspace restore just re-hydrates the previously-saved state; not user edits.
        if loaded_setting:
            self.model.current_configuration.dirty = False
            self._refresh_configuration_buttons()

    def load_settings(self, settings):
        settings : AppSettings
        settings.dump()

        # Restore the last-used experiment folder before loading configs,
        # so any early dialog (or a config restore that doesn't itself
        # touch a data file) still has a sensible starting point.
        saved_wd = str(settings.get("temperature exp_working_dir", "") or "")
        if saved_wd and os.path.isdir(saved_wd):
            self._exp_working_dir = saved_wd

        # Restore per-purpose last-used directories.
        saved_last_dirs = str(settings.get("temperature last_dirs", "") or "")
        if saved_last_dirs:
            try:
                parsed = json.loads(saved_last_dirs)
                if isinstance(parsed, dict):
                    self._last_dirs = {
                        str(k): str(v) for k, v in parsed.items()
                        if isinstance(v, str) and v
                    }
            except (ValueError, TypeError):
                pass

        # Load
        conf_list = json.loads(settings.get("temperature configurations", "[]"))
        
        if len(conf_list):
            # DEBUG: try/except removed so failures raise with full traceback.
            def _safe_restore(conf, ind):
                self.load_conf_settings(conf)

            _safe_restore(conf_list[0], 0)

            more_configurations = len(conf_list) - 1
            for n in range(more_configurations):
                self.model.add_configuration()
                self.model.select_configuration(n + 1)
                _safe_restore(conf_list[n + 1], n + 1)

            configuration_ind = settings.get("temperature configuration_ind", 0)
            self.model.select_configuration(configuration_ind)

        try_epics = str.lower(str(settings.get("temperature epics connected") )) == 'true'
        if try_epics:
            self.connect_epics()
            if not self.epics_available:
                settings.set("temperature epics connected", False)
                self.widget.connect_to_epics_cb.setChecked(False)
            else:
                self.widget.connect_to_epics_cb.setChecked(True)

        try_monitor_folder = str.lower(str(settings.get("temperature epics monitor folder"))) == 'true'
        if try_monitor_folder:
            self.widget.monitor_folder_cb.setChecked(True)
            self.connect_folder_monitor()
        temperature_autoprocessing = str.lower(str(settings.get("temperature autoprocessing")) )== 'true'
        if temperature_autoprocessing:
            self.widget.autoprocess_cb.setChecked(True)

        zmq_config_path = settings.get("zmq_config_path", "")
        if zmq_config_path and os.path.exists(zmq_config_path):
            self.zmq_worker_controller.load_config(zmq_config_path)
            if str.lower(str(settings.get("zmq_worker_listening", "false"))) == "true" \
                    and self.widget.zmq_gb.listen_btn.isEnabled():
                self.zmq_worker_controller._start_listening()

        zmq_publisher_config_path = settings.get("zmq_publisher_config_path", "")
        if zmq_publisher_config_path and os.path.exists(zmq_publisher_config_path):
            self.zmq_publisher_controller.load_config(zmq_publisher_config_path)
            # Auto-connect BEFORE restoring the publish checkbox so that
            # _publish_toggled(True) sees a live socket and activates the indicator.
            if str.lower(str(settings.get("zmq_publisher_connected", "false"))) == "true" \
                    and self.widget.epicslogger_gb.connect_btn.isEnabled():
                self.zmq_publisher_controller._connect()

        zmq_publish = str.lower(str(settings.get("zmq_publish_temperatures", "false"))) == "true"
        self.widget.epicslogger_gb.publish_temperatures_cb.setChecked(zmq_publish)

        # Multi-frame output panel (mode / range / error metric) — shared by
        # the ZMQ publisher and the EPICS PV publish path.
        mf_gb = self.widget.multiframe_output_gb
        mode_key = str(settings.get("multiframe_output_mode", "single") or "single")
        for i in range(mf_gb.mode_cb.count()):
            if mf_gb.mode_cb.itemData(i) == mode_key:
                mf_gb.mode_cb.setCurrentIndex(i)
                break
        try:
            mf_gb.range_start_sb.setValue(int(settings.get("multiframe_output_range_start", 0) or 0))
            mf_gb.range_end_sb.setValue(int(settings.get("multiframe_output_range_end", 0) or 0))
        except (TypeError, ValueError):
            pass
        err_key = str(settings.get("multiframe_output_error_metric", "fit_avg") or "fit_avg")
        for i in range(mf_gb.error_metric_cb.count()):
            if mf_gb.error_metric_cb.itemData(i) == err_key:
                mf_gb.error_metric_cb.setCurrentIndex(i)
                break
        # Fire once to reflect the restored mode in range-row + error-metric enablement.
        self._on_multiframe_mode_changed()

    def auto_process_cb_toggled(self):

        connect_to_ad = self.widget.connect_to_ad_cb.isChecked()

        if connect_to_ad:
            if self.widget.autoprocess_cb.isChecked():
                self.widget.autoprocess_lbl.setText('AD')
                self._AD_watcher.activate()
                
            else:
                self._AD_watcher.deactivate()
                self.widget.autoprocess_lbl.setText('')

            self._directory_watcher.deactivate()
        else:
            if self.widget.autoprocess_cb.isChecked():
                self.widget.autoprocess_lbl.setText('FS')
                self._directory_watcher.activate()
            else:
                self._directory_watcher.deactivate()
                self.widget.autoprocess_lbl.setText('')

            if self._AD_watcher != None:
                self._AD_watcher.deactivate()

    def _create_autoprocess_system(self):
        self._directory_watcher = NewFileInDirectoryWatcher(file_types=['.spe'])
        self._directory_watcher.file_added.connect(self.load_data_file)

        
        

    def check_pv(self, pv_name):
        # DEBUG: try/except removed so failures raise with full traceback.
        value = caget(pv_name, timeout=0.2)  # Short timeout for quick response
        return value is not None
 
    def setup_epics_datalog_file_monitor(self):
        pv = eps.epics_settings.get('epics_datalog')
        if pv is not None and pv != 'None':
            if camonitor_clear is not None:
                camonitor_clear(pv)
                camonitor(pv, callback=self.epics_datalog_changed)

    def setup_temperature_file_folder_monitor(self):
        if eps.epics_settings['T_folder'] is not None \
                and eps.epics_settings['T_folder'] != 'None':
  
            if camonitor_clear is not None:
                camonitor_clear(eps.epics_settings['T_folder'])
                camonitor(eps.epics_settings['T_folder'], callback=self.temperature_file_folder_changed)

  
    def epics_datalog_changed(self, *args, **kwargs):
        
        if 'value' in kwargs:
            value = kwargs['value']
            print(value)
            if self.widget.connect_to_epics_datalog_cb.isChecked() and self.widget.autoprocess_cb.isChecked():
                self.epics_datalog_file_changed.emit(value)

    def temperature_file_folder_changed(self, *args, **kwargs):
        
        if self.widget.connect_to_epics_cb.isChecked() and self.widget.autoprocess_cb.isChecked():
            self.temperature_folder_changed.emit()

    def temperature_folder_changed_emitted(self):
        if self.epics_available:
            self._exp_working_dir = caget(eps.epics_settings['T_folder'], as_string=True)
            self._directory_watcher.path = self._exp_working_dir
            # Route the folder change through the model's single funnel so
            # the T-log switches over immediately — even before the first
            # file in the new folder is auto-loaded.
            self.model.current_configuration.set_data_folder(self._exp_working_dir)
        if self.widget.monitor_folder_cb.isChecked():
            folder_path = caget(eps.epics_settings['T_folder'], as_string=True) or ''
            self.widget.monitor_folder_path_lbl.setText(folder_path)

    def epics_datalog_file_changed_emitted(self, filename):
        
        print(f'epics_datalog_file_changed_emitted {filename}')
        
        current_file = self.model.current_configuration.data_img_file.filename
        print(f'current_file {current_file}')
        current_folder = os.path.split(current_file)[0]
        new_file = os.path.join(current_folder, filename)
        exists = os.path.isfile(new_file)
        if exists:
            print(f'{new_file} exists -> loading')
            self.load_data_file(new_file)
        

    def setup_epics_pb_clicked(self):
        if not hasattr(self, 'setup_epics_dialog'):
            self.setup_epics_dialog = SetupEpicsDialog(self.widget)
        self.setup_epics_dialog.ok_btn.setEnabled(True)
        self.setup_epics_dialog.us_temp_pv = eps.epics_settings['us_last_temp']
        self.setup_epics_dialog.ds_temp_pv = eps.epics_settings['ds_last_temp']

        self.setup_epics_dialog.temperature_file_folder_pv = eps.epics_settings['T_folder']
        self.setup_epics_dialog.area_detector_pv = eps.epics_settings['area_detector']
        self.setup_epics_dialog.exec()
        if self.setup_epics_dialog.approved:
            with open('pyradiant/model/epics_settings.py', 'w') as outfile:
                outfile.write('epics_settings = {\n')
                outfile.write("    'us_last_temp': '" + self.setup_epics_dialog.us_temp_pv + "',\n")
                outfile.write("    'ds_last_temp': '" + self.setup_epics_dialog.ds_temp_pv + "',\n")
              
                outfile.write("    'T_folder': '" + self.setup_epics_dialog.temperature_file_folder_pv + "',\n")
                outfile.write("    'area_detector': '" + self.setup_epics_dialog.area_detector_pv + "',\n")
                outfile.write("}\n")
            eps.epics_settings['us_last_temp'] = self.setup_epics_dialog.us_temp_pv
            eps.epics_settings['ds_last_temp'] = self.setup_epics_dialog.ds_temp_pv

            eps.epics_settings['T_folder'] = self.setup_epics_dialog.temperature_file_folder_pv
            eps.epics_settings['area_detector'] = self.setup_epics_dialog.area_detector_pv
