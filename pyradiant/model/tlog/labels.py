# -*- coding: utf8 -*-
# PyRadiant - T-log v2: manual per-spectrum labels.
#
# Labels are user annotations on a (file, frame), e.g. "this fit is junk
# because the signal is too low". They are stored next to the fit results
# in T_history.json but are owned by the user, not the fit pipeline: a
# re-fit replaces the fit fields and never touches the labels.
#
# Labels are per side, because DS can be fine while US is junk. On disk the
# only addition to the T_history.json schema is an optional per-entry block
# (side keys match T_DS / T_US):
#   "labels": {"DS": ["bad_fit"], "US": ["low_signal"], "updated_local": "..."}
# A side without labels is omitted; the block is omitted when both are empty.
# The earlier single-list form {"values": [...]} is read as both sides.
# In memory a record's labels are {"ds": (keys...), "us": (keys...)}.

from __future__ import annotations

from datetime import datetime
from typing import Optional

SIDES = ("ds", "us")


DEFAULT_LABEL_DEFINITIONS: tuple[dict, ...] = (
    {"key": "bad_fit",     "display_name": "bad fit",     "color": "#e34948"},
    {"key": "low_signal",  "display_name": "low signal",  "color": "#b8b7b2"},
    {"key": "noisy",       "display_name": "noisy",       "color": "#f0a01f"},
    {"key": "non_thermal", "display_name": "non-thermal", "color": "#a35bd6"},
)



def sides_from_block(block) -> dict:
    """{"ds": (...), "us": (...)} from an on-disk "labels" block; empty
    sides omitted. Accepts the earlier {"values": [...]} form."""
    if not isinstance(block, dict):
        return {}

    def _keys(v):
        return tuple(k for k in (v or ()) if isinstance(k, str) and k)

    if "values" in block and not ("DS" in block or "US" in block):
        both = _keys(block.get("values"))
        return {side: both for side in SIDES} if both else {}
    out = {}
    for side in SIDES:
        keys = _keys(block.get(side.upper()))
        if keys:
            out[side] = keys
    return out


def block_from_sides(sides: dict, updated_local: Optional[str] = None) -> Optional[dict]:
    """On-disk "labels" block for {"ds": ..., "us": ...}, or None if empty."""
    block = {}
    for side in SIDES:
        keys = [k for k in (sides.get(side) or ()) if k]
        if keys:
            block[side.upper()] = keys
    if not block:
        return None
    block["updated_local"] = updated_local or datetime.now().isoformat(timespec="microseconds")
    return block
