# -*- coding: utf8 -*-
# PyRadiant - T-log v2: manual per-spectrum labels.
#
# Labels are user annotations on a (file, frame), e.g. "this fit is junk
# because the signal is too low". They are stored next to the fit results
# in T_history.json but are owned by the user, not the fit pipeline: a
# re-fit replaces the fit fields and never touches the labels.
#
# On disk the only addition to the T_history.json schema is an optional
# per-entry block, shaped like the one in Dioptas' XRD_history.json:
#   "labels": {"values": ["low_signal", ...], "updated_local": "..."}
# Entries without labels are written exactly as before.

from __future__ import annotations


DEFAULT_LABEL_DEFINITIONS: tuple[dict, ...] = (
    {"key": "bad_fit",     "display_name": "bad fit",     "color": "#e34948"},
    {"key": "low_signal",  "display_name": "low signal",  "color": "#b8b7b2"},
    {"key": "noisy",       "display_name": "noisy",       "color": "#f0a01f"},
    {"key": "non_thermal", "display_name": "non-thermal", "color": "#a35bd6"},
)

