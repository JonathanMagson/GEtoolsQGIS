# GEtools for QGIS

A QGIS port of the [GEtools ArcGIS Pro add-in](https://github.com/JonathanMagson/GEtoolsAppNWD),
rebuilt around **Google Earth Web** (earth.google.com/web) instead of Google
Earth Pro. You don't need Google Earth Pro installed.

Requires QGIS 3.16 or newer. The tools are on the **GEtools** toolbar, under
**Web ▸ GEtools**, and on the map canvas right-click menu (**GEtools** submenu).

| Tool | Shortcut | ArcGIS Pro add-in | QGIS plugin |
|---|---|---|---|
| **OpenInGE** | Alt+Shift+G | Wrote a placemark KML and opened it in Google Earth Pro | Click the map: Google Earth Web opens on that spot, marked with a red X |
| **SyncToGE** | Alt+Shift+S | KML NetworkLink that Google Earth Pro re-read every 0.3 s | Google Earth Web follows pan, zoom and rotation; optionally QGIS follows Google Earth too |
| **LayerToGE** | Alt+Shift+L | `arcpy` LayerToKML, opened in Google Earth Pro | Exports the active layer (or its selected features) to KML and loads it into Google Earth Web |
| **OpenInMaps** | Alt+Shift+M | Google Maps in the browser | Same |
| **OpenInStreet** | Alt+Shift+V | Street View in the browser | Same |
| **Clear targets** | Alt+Shift+X | | Removes the target markers |

The shortcuts use Alt+Shift, which neither QGIS nor its bundled plugins use.
Change them in **Settings ▸ Keyboard Shortcuts** (or the button in GEtools
Settings).

Extras: **Open current view in Google Earth Web** (Web ▸ GEtools) opens your
normal browser at the current map view, and the right-click menu has **Copy lat, lon**.

## Install

1. Download [`getools.zip`](https://github.com/JonathanMagson/GEtoolsQGIS/raw/main/getools.zip). After changing the code, rebuild it with `./build_zip.sh`.
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, pick the zip, click **Install Plugin**.

## The Google Earth window

Google Earth Web runs in a window the plugin controls, so it can move it, mark
it and read it back:

1. **A panel inside QGIS**, if your QGIS has Qt WebEngine (common on Linux;
   `python3-pyqt5.qtwebengine`).
2. **A dedicated Chrome or Edge window** otherwise. This is the usual case on
   Windows, where the QGIS installers don't include Qt WebEngine. Edge comes
   with Windows, so nothing extra needs installing. The plugin steers the
   window over the browser's local DevTools connection.
   - The window has its own browser profile (`getools_browser` in your QGIS
     profile folder), separate from your everyday browser.
   - The DevTools port only listens on `127.0.0.1` (this computer).

If neither is available, the tools fall back to opening links in your default
browser (no live sync, no X, no automatic layer loading).

## Tools in detail

**SyncToGE**: each time the QGIS map settles after a pan, zoom or rotation,
Google Earth moves to match. Close the Google Earth window, or turn SyncToGE
off, to stop.

- **Two-way sync** (Settings, off by default): while SyncToGE is on, moving
  Google Earth moves the QGIS map to the same place, zoom and rotation. QGIS has
  no tilt, so a tilted Google Earth view is matched on the point it looks at.
- **Show Google Earth's view on the QGIS map** (Settings, off by default):
  outlines the ground Google Earth is showing, including tilted views. This
  works whenever the Google Earth window is open, synced or not.
- Both read the camera position Google Earth keeps in its address, which it
  updates once the view settles.

**OpenInGE** always uses the Google Earth window (even with sync off) and
draws an **X** on the clicked spot. Google Earth Web can't be given a placemark
from outside, so the plugin draws the X over the globe itself, positioned from
Google Earth's camera. The X stays on the spot as you pan, zoom and tilt; it
hides while you drag or scroll and comes back once the view settles.

The spot is also marked on the QGIS map with a cross (for OpenInMaps and
OpenInStreet too). The next click replaces it, unless **Keep every target** is
on in Settings. **Clear targets** removes them from QGIS and Google Earth.

**LayerToGE** exports the active vector layer, or only its selected features,
to KML (in `Documents/OpenInGE_kml_temp`, clearing old KMLs first). Then:

- It opens Google Earth framed on the layer and **drops the KML onto it**, the
  same as dragging the file there from Explorer.
- If the layer doesn't appear, or automatic loading is off in Settings, the
  KML path is on the clipboard: in Google Earth Web choose **Projects ▸ New
  project ▸ Import KML file from computer**.

Placemarks are named from the layer's label field, or its display field if
there's no label field.

## Settings

**Web ▸ GEtools ▸ Settings…** (also on the right-click menu):

| Setting | Default |
|---|---|
| Browser: Automatic (Chrome, else Edge), Chrome, Edge, or another Chromium browser | Automatic |
| Bring Google Earth to front when OpenInGE or LayerToGE moves it (restores a minimized window; syncing never does) | On |
| Fast sync: move Google Earth without reloading. Untick if it stops following the map | On |
| Sync delay: how long the map must stay still before Google Earth follows | 400 ms |
| Two-way sync | Off |
| Show Google Earth's view on the QGIS map, and its outline colour | Off, orange |
| OpenInGE view distance | 1000 m |
| OpenInGE view tilt (0° looks straight down) | 0° |
| Keep every target | Off |
| Target colour and size | Red, 30 px |
| LayerToGE: load the layer into Google Earth automatically | On |

**Restore Defaults** resets them all.

The Google Earth Pro tip "set Fly-To Speed to 5" no longer applies.

## Development

`getools/google_urls.py`, `camera.py`, `earth_marker.py` and `cdp.py` have no
QGIS dependency and are unit tested:

```bash
pip install pytest
pytest tests
```

## Licence

See `getools/LICENSE.txt`. SyncToGE descends from Chris Stayte's MIT-licensed
[ArcPro_To_GoogleEarth](https://github.com/chrisstayte/ArcPro_To_GoogleEarth),
and the icon credits from the original add-in are carried over.
