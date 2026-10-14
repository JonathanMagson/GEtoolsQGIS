import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from getools import cdp  # noqa: E402


def test_custom_browser_must_exist(tmp_path):
    exe = tmp_path / "browser"
    assert cdp.find_browser("custom", str(exe)) is None
    exe.write_text("")
    assert cdp.find_browser("custom", str(exe)) == str(exe)


def test_drop_file_events(tmp_path):
    events = cdp.drop_file_events(str(tmp_path / "a.kml"), 10, 20)
    assert [p["type"] for _, p in events] == ["dragEnter", "dragOver", "drop"]
    assert all(m == "Input.dispatchDragEvent" for m, _ in events)
    assert events[2][1]["data"]["files"] == [str(tmp_path / "a.kml")]
