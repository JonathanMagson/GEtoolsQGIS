import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from getools import camera as c  # noqa: E402
from getools import google_urls as u  # noqa: E402


def test_parse_round_trips_our_urls():
    url = u.earth_view_url(-33.8688, 151.2093, 1500, heading=270, tilt=45)
    cam = c.parse_camera(url)
    assert cam == c.Camera(-33.8688, 151.2093, 1500.0, 270.0, 45.0, 35.0, 0.0)


def test_parse_earth_web_style_url():
    url = ("https://earth.google.com/web/@48.8583701,2.2944813,35.1a,812.5d,35y,"
           "-20.3h,60t,0r/data=CgRCAggB")
    cam = c.parse_camera(url)
    assert (cam.lat, cam.lon, cam.range, cam.tilt, cam.altitude) == (
        48.8583701, 2.2944813, 812.5, 60.0, 35.1)
    assert math.isclose(cam.heading, 339.7)


def test_parse_rejects_urls_without_camera():
    assert c.parse_camera("https://earth.google.com/web/") is None
    assert c.parse_camera("https://earth.google.com/web/search/x/") is None
    assert c.parse_camera(None) is None


def test_cameras_close():
    a = c.Camera(-33.86, 151.2, 1000, 0, 0, 35, 0)
    assert c.cameras_close(a, a._replace(range=1030, heading=0.5, tilt=60, altitude=40))
    assert not c.cameras_close(a, a._replace(range=1200))
    assert not c.cameras_close(a, a._replace(lat=-33.85))   # ~1.1 km away
    assert not c.cameras_close(a, a._replace(heading=10))
    assert c.cameras_close(a._replace(heading=359.8), a._replace(heading=0.1))
    assert not c.cameras_close(a, None)


def test_footprint_straight_down_matches_view_size():
    cam = c.Camera(0.0, 0.0, 1000.0, 0.0, 0.0, 35.0, 0.0)
    ring = c.footprint(cam, aspect=2.0, steps=1)
    assert len(ring) == 5 and ring[0] == ring[-1]
    half_h = 1000 * math.tan(math.radians(17.5))
    top_left = ring[0]
    assert math.isclose(c.ground_distance_m(0, 0, top_left[0], 0), half_h, rel_tol=1e-3)
    assert math.isclose(c.ground_distance_m(0, 0, 0, top_left[1]), 2 * half_h, rel_tol=1e-3)
    assert top_left[0] > 0 and top_left[1] < 0  # north-west corner


def test_footprint_follows_heading_and_tilt():
    cam = c.Camera(0.0, 0.0, 1000.0, 90.0, 0.0, 35.0, 0.0)  # east at the top
    top_left = c.footprint(cam, aspect=1.0, steps=1)[0]
    assert top_left[1] > 0 and top_left[0] > 0  # top-left is north-east
    tilted = c.footprint(cam._replace(heading=0.0, tilt=80.0), aspect=1.0, steps=1)
    far = max(c.ground_distance_m(0, 0, lat, lon) for lat, lon in tilted)
    assert far <= 4.0 * 1000 * 1.01  # over-the-horizon rays are capped
