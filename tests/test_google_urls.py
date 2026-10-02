import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "getools"))

import google_urls as u  # noqa: E402


def test_point_url_drops_pin():
    assert u.earth_point_url(-33.8688, 151.2093) == "https://earth.google.com/web/search/-33.8688,151.2093/"


def test_view_url_format():
    url = u.earth_view_url(-33.8688, 151.2093, 1500, heading=-90, tilt=0)
    assert url == "https://earth.google.com/web/@-33.8688,151.2093,0a,1500d,35y,270h,0t,0r"


def test_view_url_clamps_range():
    assert ",50d," in u.earth_view_url(0, 0, 1)
    assert ",30000000d," in u.earth_view_url(0, 0, 1e12)


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_rejects_bad_coordinates(lat, lon):
    for fn in (u.earth_point_url, u.google_maps_url, u.street_view_url):
        with pytest.raises(ValueError):
            fn(lat, lon)


def test_maps_and_street_view_urls():
    assert u.google_maps_url(1.5, -2.25) == "https://www.google.com/maps?q=1.5,-2.25"
    assert u.street_view_url(1.5, -2.25) == (
        "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=1.5,-2.25")


def test_range_fits_ground_height():
    r = u.range_for_ground_height(1000)
    assert math.isclose(2 * r * math.tan(math.radians(u.DEFAULT_FOV / 2)), 1000)


@pytest.mark.parametrize("rotation,heading", [(0, 0), (90, 270), (-90, 90), (180, 180), (450, 270)])
def test_heading_from_rotation(rotation, heading):
    assert u.heading_from_rotation(rotation) == heading


def test_no_negative_zero():
    assert u._fmt(-0.0000001, 5) == "0"
