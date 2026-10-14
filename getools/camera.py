"""Google Earth Web camera maths: read it from a URL, compare, footprint.

Earth Web keeps its camera in the page address as
``/@lat,lon,<alt>a,<range>d,<fov>y,<heading>h,<tilt>t,<roll>r``. Two-way sync
reads it to move QGIS, and the footprint outlines what that camera sees.

Pure Python, no QGIS imports. Uses the same pinhole model as the target X
(earth_marker.py), so the footprint and the X agree.
"""

import math
import re
from collections import namedtuple

from .google_urls import DEFAULT_FOV

EARTH_RADIUS_M = 6371008.8

Camera = namedtuple("Camera", "lat lon range heading tilt fov altitude")

_CAMERA_RE = re.compile(r"/@(-?[\d.]+),(-?[\d.]+),([^/?#]*)")


def parse_camera(url):
    """The ``Camera`` in an Earth Web URL, or ``None`` if it has none."""
    m = _CAMERA_RE.search(url or "")
    if not m:
        return None
    values = {"a": 0.0, "d": 0.0, "y": DEFAULT_FOV, "h": 0.0, "t": 0.0}
    for part in m.group(3).split(","):
        key = part[-1:]
        if key in values:
            try:
                values[key] = float(part[:-1])
            except ValueError:
                pass
    lat, lon = float(m.group(1)), float(m.group(2))
    if values["d"] <= 0 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return Camera(lat, lon, values["d"], values["h"] % 360.0, values["t"],
                  values["y"], values["a"])


def ground_distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def cameras_close(a, b, position=0.02, distance=0.05, heading_deg=1.0):
    """True when two cameras show essentially the same view.

    Tolerances are relative to the camera distance, so they work at any zoom.
    Tilt and altitude are ignored: QGIS has no tilt, and Earth Web rewrites
    the altitude with the terrain height.
    """
    if a is None or b is None:
        return False
    scale = max(a.range, b.range)
    if ground_distance_m(a.lat, a.lon, b.lat, b.lon) > position * scale:
        return False
    if abs(a.range - b.range) > distance * scale:
        return False
    dh = abs((a.heading - b.heading + 180.0) % 360.0 - 180.0)
    return dh <= heading_deg


def _offset(lat, lon, east, north):
    """Move ``east``/``north`` metres from a point (spherical; fine for views)."""
    lat2 = lat + math.degrees(north / EARTH_RADIUS_M)
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    lon2 = lon + math.degrees(east / (EARTH_RADIUS_M * cos_lat))
    lat2 = max(-89.9, min(89.9, lat2))
    lon2 = (lon2 + 180.0) % 360.0 - 180.0
    return lat2, lon2


def footprint(cam, aspect, steps=8):
    """Outline of the ground Earth Web shows: a list of ``(lat, lon)``.

    ``aspect`` is the globe canvas width / height. The view's corner rays are
    intersected with the ground plane at the look-at point; rays that pass
    over the horizon (steep tilts) are cut off at ``4 x`` the camera distance.
    Each edge is split into ``steps`` points so it reprojects smoothly.
    """
    h, t = math.radians(cam.heading), math.radians(cam.tilt)
    view = (math.sin(t) * math.sin(h), math.sin(t) * math.cos(h), -math.cos(t))
    up = (math.cos(t) * math.sin(h), math.cos(t) * math.cos(h), math.sin(t))
    right = (math.cos(h), -math.sin(h), 0.0)
    cam_pos = tuple(-cam.range * v for v in view)  # look-at point is the origin
    tan_y = math.tan(math.radians(cam.fov) / 2)
    tan_x = tan_y * aspect
    max_reach = 4.0 * cam.range

    def ground(sx, sy):
        ray = tuple(view[i] + sx * tan_x * right[i] + sy * tan_y * up[i] for i in range(3))
        if ray[2] < -1e-9:
            k = -cam_pos[2] / ray[2]
            e, n = cam_pos[0] + k * ray[0], cam_pos[1] + k * ray[1]
        else:
            k = float("inf")
        if k == float("inf") or math.hypot(e, n) > max_reach:
            horizontal = math.hypot(ray[0], ray[1]) or 1.0
            e, n = ray[0] / horizontal * max_reach, ray[1] / horizontal * max_reach
        return _offset(cam.lat, cam.lon, e, n)

    corners = [(-1, 1), (1, 1), (1, -1), (-1, -1)]
    ring = []
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]):
        for i in range(steps):
            f = i / steps
            ring.append(ground(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    ring.append(ring[0])
    return ring
