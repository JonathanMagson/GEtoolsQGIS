"""URL builders for Google Earth Web, Google Maps and Street View.

Pure Python, no QGIS imports, so it can be unit tested on its own.
"""

import math

GOOGLE_EARTH_WEB = "https://earth.google.com/web"

# Google Earth Web's default vertical field of view, in degrees.
DEFAULT_FOV = 35.0

# Google Earth Web refuses a camera closer than this, and the whole globe is
# visible from roughly the upper value.
MIN_RANGE_M = 50.0
MAX_RANGE_M = 30_000_000.0


def _fmt(value, digits):
    """Format a number without trailing zeros (``1.50000`` -> ``1.5``)."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _check_lat_lon(lat, lon):
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude {lat} is outside -90..90")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude {lon} is outside -180..180")


def earth_point_url(lat, lon):
    """Google Earth Web flown to a coordinate, with a pin dropped on it.

    The ``/search/<lat>,<lon>`` form is what replaces the "Target" KML
    placemark the ArcGIS add-in used to write for Google Earth Pro.
    """
    _check_lat_lon(lat, lon)
    return f"{GOOGLE_EARTH_WEB}/search/{_fmt(lat, 7)},{_fmt(lon, 7)}/"


def earth_view_url(lat, lon, range_m, heading=0.0, tilt=0.0, fov=DEFAULT_FOV,
                   altitude=0.0):
    """Google Earth Web camera looking at ``lat, lon`` from ``range_m`` metres.

    Format: ``/web/@lat,lon,<alt>a,<range>d,<fov>y,<heading>h,<tilt>t,0r``.
    """
    _check_lat_lon(lat, lon)
    range_m = min(max(range_m, MIN_RANGE_M), MAX_RANGE_M)
    heading = heading % 360.0
    return (
        f"{GOOGLE_EARTH_WEB}/@{_fmt(lat, 7)},{_fmt(lon, 7)},"
        f"{_fmt(altitude, 2)}a,{_fmt(range_m, 2)}d,{_fmt(fov, 2)}y,"
        f"{_fmt(heading, 2)}h,{_fmt(tilt, 2)}t,0r"
    )


def google_maps_url(lat, lon):
    _check_lat_lon(lat, lon)
    return f"https://www.google.com/maps?q={_fmt(lat, 7)},{_fmt(lon, 7)}"


def street_view_url(lat, lon):
    _check_lat_lon(lat, lon)
    return (
        "https://www.google.com/maps/@?api=1&map_action=pano"
        f"&viewpoint={_fmt(lat, 7)},{_fmt(lon, 7)}"
    )


def range_for_ground_height(ground_height_m, fov=DEFAULT_FOV):
    """Camera distance at which ``ground_height_m`` fills the vertical view."""
    half = math.radians(fov) / 2.0
    return ground_height_m / (2.0 * math.tan(half))


def heading_from_rotation(rotation):
    """Convert a QGIS canvas rotation into a Google Earth heading.

    QGIS rotates the map clockwise by ``rotation`` degrees, so the top of the
    screen points ``-rotation`` degrees from north. Google Earth's heading is
    the bearing of the top of the screen (0 = north, 90 = east).
    """
    return (-rotation) % 360.0
