import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "getools"))

import earth_marker as m  # noqa: E402


def test_marker_js_embeds_target():
    js = m.marker_js(-33.86, 151.2)
    assert js.rstrip().endswith('({"lat": -33.86, "lon": 151.2});')
    assert "%s" not in js


def test_clear_is_safe_without_marker():
    assert m.CLEAR_MARKER_JS.startswith("window.__getoolsMarker &&")
