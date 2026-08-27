# -*- coding: utf8 -*-
# PyRadiant - GUI program for analysis of thermal spectra during
# laser heated diamond anvil cell experiments
# Copyright (C) 2024 Ross Hrubiak (hrubiak@anl.gov)
# High Pressure Collaborative Access Team, Argonne National Laboratory
#
# T-log v2 — decoupled record model + non-blocking writer.
#
# This package is a spike (step 1 of the rewrite): the new components exist
# alongside the legacy log path in TemperatureModelConfiguration but are not
# wired into MainController yet. Nothing here is used at runtime until the
# controller and widget are switched over in a later step.
#
# Design summary:
#   TLogRecord — one row, frozen dataclass, single canonical schema.
#   TLogStore  — in-memory records for the currently viewed folder. Pure
#                Python + Signal; no I/O. Widget subscribes to
#                records_changed and redraws.
#   TLogWriter — file I/O in a background thread, driven by a Queue.
#                All exceptions caught; display never blocks on disk.
#
# The controller that glues these to TemperatureModelConfiguration lives in
# pyradiant.controller.TLogController (also unwired for now).

from .record import TLogRecord
from .store import TLogStore
from .writer import TLogWriter, WriterState

__all__ = ["TLogRecord", "TLogStore", "TLogWriter", "WriterState"]
