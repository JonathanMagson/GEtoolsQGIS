"""All GEtools options, stored in the QGIS user profile (QgsSettings)."""

from qgis.core import QgsSettings

from .earth_marker import DEFAULT_COLOR, DEFAULT_SIZE

_PREFIX = "getools/"

# name: (default, type). Keys keep the names earlier versions used.
DEFAULTS = {
    # Google Earth window
    "browser": ("auto", str),            # cdp.BROWSER_CHOICES
    "browser_path": ("", str),           # used when browser == "custom"
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


def get(name):
    default, kind = DEFAULTS[name]
    return QgsSettings().value(_PREFIX + name, default, type=kind)


def set(name, value):  # noqa: A001 - mirrors get()
    QgsSettings().setValue(_PREFIX + name, value)


def reset():
    for name in DEFAULTS:
        QgsSettings().remove(_PREFIX + name)
