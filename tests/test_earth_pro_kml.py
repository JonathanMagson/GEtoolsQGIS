import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from getools import earth_pro_kml as k  # noqa: E402
from getools.camera import Camera  # noqa: E402

NS = {"k": "http://www.opengis.net/kml/2.2"}


def test_master_kml_links_and_view_format():
    root = ET.fromstring(k.master_kml("http://127.0.0.1:5000/tok"))
    hrefs = [e.text for e in root.iterfind(".//k:href", NS)]
    assert hrefs == ["http://127.0.0.1:5000/tok/view.kml", "http://127.0.0.1:5000/tok/camera",
                     "http://127.0.0.1:5000/tok/targets.kml"]
    assert root.find(".//k:NetworkLink/k:flyToView", NS).text == "1"
    assert root.find(".//k:viewFormat", NS).text == k.VIEW_FORMAT  # & survives escaping


def test_lookat_kml():
    root = ET.fromstring(k.lookat_kml(Camera(-33.86, 151.2, 1500, 370, 30, 35, 0)))
    look = root.find("k:NetworkLinkControl/k:LookAt", NS)
    got = {child.tag.split("}")[1]: child.text for child in look}
    assert got["longitude"] == "151.2" and got["latitude"] == "-33.86"
    assert got["range"] == "1500" and got["heading"] == "10" and got["tilt"] == "30"


def test_empty_kml_is_valid_and_has_no_view():
    root = ET.fromstring(k.EMPTY_KML)
    assert root.find(".//k:LookAt", NS) is None


def test_targets_kml():
    root = ET.fromstring(k.targets_kml([(1, 2), (3.5, -4)], "http://h/x.png", "#00ff80", 48))
    coords = [e.text for e in root.iterfind(".//k:coordinates", NS)]
    assert coords == ["2,1,0", "-4,3.5,0"]
    style = root.find(".//k:IconStyle", NS)
    assert style.find("k:color", NS).text == "ff80ff00"  # aabbggrr
    assert style.find("k:scale", NS).text == "1.5"
    assert ET.fromstring(k.targets_kml([], "u", "#000000", 30)).find(".//k:Placemark", NS) is None


def test_parse_camera_report():
    cam, aspect = k.parse_camera_report(
        "CAMERA=151.2,-33.86,2500.5,45,350&VIEW=60,35,1600,900")
    assert (cam.lat, cam.lon, cam.range, cam.tilt, cam.heading, cam.fov) == (
        -33.86, 151.2, 2500.5, 45.0, 350.0, 35.0)
    assert abs(aspect - 1600 / 900) < 1e-9
    assert k.parse_camera_report("CAMERA=1,2,3") is None
    assert k.parse_camera_report("BBOX=1,2,3,4") is None
    assert k.parse_camera_report("CAMERA=1,95,100,0,0") is None  # lat out of range
