"""KML for the live link between QGIS and Google Earth Pro.

Google Earth Pro is given one KML file (``master_kml``) holding three
network links to a small HTTP server inside QGIS:

- **view**: polled every 0.3 s with ``flyToView``. The server answers with a
  ``LookAt`` only when QGIS has somewhere new to show, so Earth flies there
  once and the user can still move around (Google Earth Pro re-flies to a
  view on every refresh that contains one).
- **camera**: requested by Earth whenever its camera stops moving, carrying
  the view in the query string. That drives two-way sync and the footprint.
- **targets**: the target placemarks, refreshed every second.

Pure Python, no QGIS imports.
"""

import math
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

from .camera import Camera

# What Earth sends back on the camera link. KML view placeholders:
# https://developers.google.com/kml/documentation/kmlreference#viewformat
VIEW_FORMAT = ("CAMERA=[lookatLon],[lookatLat],[lookatRange],[lookatTilt],[lookatHeading]"
               "&VIEW=[horizFov],[vertFov],[horizPixels],[vertPixels]")

EMPTY_KML = '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>\n'


def _num(value, digits=7):
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") or "0"


def master_kml(base_url):
    """The file opened in Google Earth Pro; ``base_url`` ends without '/'."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Folder>
  <name>GEtools (QGIS)</name>
  <open>1</open>
  <description>Live link to QGIS. Remove this folder to disconnect.</description>
  <NetworkLink>
    <name>QGIS view</name>
    <flyToView>1</flyToView>
    <Link>
      <href>{escape(base_url)}/view.kml</href>
      <refreshMode>onInterval</refreshMode>
      <refreshInterval>0.3</refreshInterval>
    </Link>
  </NetworkLink>
  <NetworkLink>
    <name>Google Earth view (for QGIS)</name>
    <Link>
      <href>{escape(base_url)}/camera</href>
      <viewRefreshMode>onStop</viewRefreshMode>
      <viewRefreshTime>0.5</viewRefreshTime>
      <viewFormat>{escape(VIEW_FORMAT)}</viewFormat>
    </Link>
  </NetworkLink>
  <NetworkLink>
    <name>Targets</name>
    <Link>
      <href>{escape(base_url)}/targets.kml</href>
      <refreshMode>onInterval</refreshMode>
      <refreshInterval>1</refreshInterval>
    </Link>
  </NetworkLink>
</Folder>
</kml>
"""


def lookat_kml(cam):
    """Network link answer that makes Earth fly to ``cam``."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<NetworkLinkControl>
  <LookAt>
    <longitude>{_num(cam.lon)}</longitude>
    <latitude>{_num(cam.lat)}</latitude>
    <altitude>0</altitude>
    <heading>{_num(cam.heading % 360.0, 2)}</heading>
    <tilt>{_num(cam.tilt, 2)}</tilt>
    <range>{_num(cam.range, 2)}</range>
    <altitudeMode>relativeToGround</altitudeMode>
  </LookAt>
</NetworkLinkControl>
<Document/>
</kml>
"""


def kml_color(hex_rgb):
    """``#rrggbb`` -> KML ``aabbggrr`` (opaque)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        h = "e60000"
    return ("ff" + h[4:6] + h[2:4] + h[0:2]).lower()


def targets_kml(targets, icon_url, color, size):
    """Placemarks with a tinted X icon at each ``(lat, lon)``."""
    scale = max(0.3, size / 32.0)  # Earth draws icons 32 px at scale 1
    marks = "".join(
        f"""  <Placemark>
    <name>Target {i}</name>
    <styleUrl>#getools-target</styleUrl>
    <Point><coordinates>{_num(lon)},{_num(lat)},0</coordinates></Point>
  </Placemark>
""" for i, (lat, lon) in enumerate(targets, 1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <Style id="getools-target">
    <IconStyle>
      <color>{kml_color(color)}</color>
      <scale>{_num(scale, 3)}</scale>
      <Icon><href>{escape(icon_url)}</href></Icon>
      <hotSpot x="0.5" y="0.5" xunits="fraction" yunits="fraction"/>
    </IconStyle>
    <LabelStyle><scale>0</scale></LabelStyle>
  </Style>
{marks}</Document>
</kml>
"""


def parse_camera_report(query):
    """``(Camera, aspect)`` from a camera-link query string, or ``None``.

    ``aspect`` is width / height of Earth's 3D view. The camera's ``fov`` is
    the vertical field of view, as elsewhere in the plugin.
    """
    params = parse_qs(query)
    try:
        lon, lat, rng, tilt, heading = (float(v) for v in params["CAMERA"][0].split(","))
    except (KeyError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or rng <= 0:
        return None
    fov, aspect = 35.0, 1.5
    try:
        hfov, vfov, width, height = (float(v) for v in params["VIEW"][0].split(","))
        if 0 < vfov < 180:
            fov = vfov
        if width > 0 and height > 0:
            aspect = width / height
        elif 0 < hfov < 180 and 0 < vfov < 180:
            aspect = math.tan(math.radians(hfov) / 2) / math.tan(math.radians(vfov) / 2)
    except (KeyError, ValueError):
        pass
    return Camera(lat, lon, rng, heading % 360.0, tilt, fov, 0.0), aspect
