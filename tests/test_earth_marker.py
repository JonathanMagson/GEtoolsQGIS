import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from getools import earth_marker as m  # noqa: E402


def test_marker_js_embeds_targets_and_style():
    js = m.marker_js([(-33.86, 151.2), (1, 2)], "#00ff00", 24)
    assert js.rstrip().endswith(
        '([{"lat": -33.86, "lon": 151.2}, {"lat": 1.0, "lon": 2.0}], '
        '{"color": "#00ff00", "size": 24});')
    assert "%s" not in js


def test_clear_is_safe_without_marker():
    assert m.CLEAR_MARKER_JS.startswith("window.__getoolsMarker &&")
