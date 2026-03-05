"""
Shared configuration loader for the T-view worker ecosystem.

Usage in any worker::

    from config import load_config, get_worker_port, get_results_port

    config = load_config()                         # finds config.yaml automatically
    my_port     = get_worker_port("imaging", config)
    results_port = get_results_port(config)
"""

import os
import yaml


_DEFAULT_SEARCH_PATHS = [
    "config.yaml",
    os.path.join(os.path.dirname(__file__), "config.yaml"),
    os.path.expanduser("~/.config/t-view/config.yaml"),
]


def load_config(path=None):
    """Load config.yaml and return the parsed dict.

    Parameters
    ----------
    path : str or None
        Explicit path to config.yaml.  When *None* the function searches
        the default locations in order and returns the first match.
        Returns an empty dict when no file is found.
    """
    if path is None:
        for candidate in _DEFAULT_SEARCH_PATHS:
            if os.path.exists(candidate):
                path = candidate
                break

    if path is None or not os.path.exists(path):
        return {}

    with open(path, "r") as fh:
        return yaml.safe_load(fh) or {}


def get_worker_port(worker_name, config):
    """Return the ZMQ port for *worker_name*, or None if not found."""
    return config.get("workers", {}).get(worker_name, {}).get("port")


def get_results_port(config):
    """Return the coordinator results port, or None if not configured."""
    return config.get("ports", {}).get("coordinator_results")


def get_worker_health_port(worker_name, config):
    """Return the ZMQ health-check (REP) port for *worker_name*, or None if not set."""
    return config.get("workers", {}).get(worker_name, {}).get("health_port")


def get_worker_names(config):
    """Return the list of worker names defined in the config."""
    return list(config.get("workers", {}).keys())
