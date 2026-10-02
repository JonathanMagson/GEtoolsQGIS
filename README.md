# GEtools for QGIS

A QGIS port of the [GEtools ArcGIS Pro add-in](https://github.com/JonathanMagson/GEtoolsAppNWD),
rebuilt around **Google Earth Web** (earth.google.com/web) instead of Google
Earth Pro. You don't need Google Earth Pro installed.

Requires QGIS 3.16 or newer. The tools are on the **GEtools** toolbar, under
**Web ▸ GEtools**, and on the map canvas right-click menu (**GEtools** submenu).

| Tool | ArcGIS Pro add-in | QGIS plugin |
|---|---|---|
| **OpenInGE** | Wrote a placemark KML and opened it in Google Earth Pro | Click the map: Google Earth Web flies there and drops a pin |
| **SyncToGE** | KML NetworkLink that Google Earth Pro re-read every 0.3 s | A **Google Earth Web panel** docked in QGIS that follows pan, zoom and rotation |
| **LayerToGE** | `arcpy` LayerToKML, opened in Google Earth Pro | Exports the active layer (or just its selected features) to KML in WGS84 and opens Google Earth Web framed on it |
| **OpenInMaps** | Google Maps in the browser | Same |
| **OpenInStreet** | Street View in the browser | Same |
| **Toggle NDVI** | Show/hide the `NVDI` layer | Show/hide a layer named `NDVI` or `NVDI` |

Extras: **Open current view in Google Earth Web** (Web ▸ GEtools) opens your
browser at the current map view, and the right-click menu has **Copy lat, lon**.

## Install

1. Download `getools.zip` (or zip the `getools/` folder yourself).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, pick the zip, click **Install Plugin**.

## How the Google Earth Web tools work

**SyncToGE** opens a "Google Earth Web" panel on the right side of QGIS. Each
time the map settles after a pan, zoom or rotation, the panel moves to match.
To stop, turn SyncToGE off or close the panel. If the panel stops following the
map, untick **Fast sync (no reload)**. Sync is then slower but more reliable,
because the page reloads at each new view. **Open in browser** opens the
panel's current view in your normal browser.

The panel needs **Qt WebEngine**. Google Earth Web needs WebGL, and QGIS's older
QtWebKit can't provide it. If your QGIS install has no Qt WebEngine,
SyncToGE opens the current view in your browser once and says why. Your
browser can't be driven live from QGIS, so use **Open current view in Google
Earth Web** to update it. To get Qt WebEngine:
- Linux: install your distro's `python3-pyqt5.qtwebengine` package.
- Windows (OSGeo4W): install the `qt5-webengine` / `pyqt5-webengine` package if your installer offers it.

**OpenInGE** uses the panel when it's open, and your default browser otherwise.

**LayerToGE**: Google Earth Web has no URL or API that loads a file, so the
KML has to be imported by hand. The plugin writes the KML to
`Documents/OpenInGE_kml_temp`, the same folder the add-in used, and clears old
KMLs first. It copies the path to the clipboard and opens Google Earth Web on
the layer. In Google Earth Web, choose **Projects ▸ New project ▸ Import KML file
from computer** and paste the path. Placemarks are named from the layer's label
field, or its display field if there's no label field.

The Google Earth Pro tip "set Fly-To Speed to 5" no longer applies.

## Development

`getools/google_urls.py` has no QGIS dependency and is unit tested:

```bash
pip install pytest
pytest tests
```

## Licence

See `getools/LICENSE.txt`. SyncToGE descends from Chris Stayte's MIT-licensed
[ArcPro_To_GoogleEarth](https://github.com/chrisstayte/ArcPro_To_GoogleEarth),
and the icon credits from the original add-in are carried over.
