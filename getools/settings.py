"""All GEtools options, stored in the QGIS user profile (QgsSettings)."""

import os

from qgis.core import QgsApplication, QgsSettings

from .earth_marker import DEFAULT_COLOR, DEFAULT_SIZE

_PREFIX = "getools/"

# name: (default, type). Keys keep the names earlier versions used.
DEFAULTS = {
    # Which Google Earth: "web" (Google Earth Web) or "pro" (Google Earth Pro)
    "earth_app": ("web", str),
    "pro_path": ("", str),               # Earth Pro program; empty = find it
    # Google Earth window
    "browser": ("auto", str),            # cdp.BROWSER_CHOICES
    "browser_path": ("", str),           # used when browser == "custom"
    "connection": ("auto", str),         # cdp.CONNECTION_CHOICES
    "no_sandbox": (False, bool),         # start the browser with --no-sandbox
    "software_webgl": ("auto", str),     # cdp.SOFTWARE_GL_CHOICES
    "browser_data_dir": ("", str),       # browser profile, downloaded Chromium; "" = QGIS profile
    "raise_earth_window": (True, bool),
    # Sync
    "fast_sync": (True, bool),
    "sync_delay_ms": (400, int),
    "two_way_sync": (False, bool),
    "show_footprint": (False, bool),
    "footprint_color": ("#ff8c00", str),
    # OpenInGE
    "point_range_m": (1000.0, float),
    "point_tilt": (0.0, float),
    # Targets
    "keep_all_targets": (False, bool),
    "target_color": (DEFAULT_COLOR, str),
    "target_size": (DEFAULT_SIZE, int),
    # LayerToGE
    "auto_import_layer": (True, bool),
}


def browser_data_dir(path=None):
    """Folder for the Google Earth browser's profile and a downloaded
    Chromium: the setting (or ``path``), else the QGIS profile folder."""
    path = get("browser_data_dir") if path is None else path
    return os.path.expanduser(path) if path else QgsApplication.qgisSettingsDirPath()


def get(name):
    default, kind = DEFAULTS[name]
    return QgsSettings().value(_PREFIX + name, default, type=kind)


def set(name, value):  # noqa: A001 - mirrors get()
    QgsSettings().setValue(_PREFIX + name, value)


def reset():
    for name in DEFAULTS:
        QgsSettings().remove(_PREFIX + name)
