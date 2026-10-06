# GEtools for QGIS

A QGIS port of the [GEtools ArcGIS Pro add-in](https://github.com/JonathanMagson/GEtoolsAppNWD),
rebuilt around **Google Earth Web** (earth.google.com/web) instead of Google
Earth Pro. You don't need Google Earth Pro installed.

Requires QGIS 3.16 or newer. The tools are on the **GEtools** toolbar, under
**Web ▸ GEtools**, and on the map canvas right-click menu (**GEtools** submenu).

| Tool | ArcGIS Pro add-in | QGIS plugin |
|---|---|---|
| **OpenInGE** | Wrote a placemark KML and opened it in Google Earth Pro | Click the map: Google Earth Web opens looking down on that spot, with a pin on it |
| **SyncToGE** | KML NetworkLink that Google Earth Pro re-read every 0.3 s | Google Earth Web follows pan, zoom and rotation, in a QGIS panel or a dedicated Chrome/Edge window |
| **LayerToGE** | `arcpy` LayerToKML, opened in Google Earth Pro | Exports the active layer (or just its selected features) to KML in WGS84 and opens Google Earth Web framed on it |
| **OpenInMaps** | Google Maps in the browser | Same |
| **OpenInStreet** | Street View in the browser | Same |
| **Toggle NDVI** | Show/hide the `NVDI` layer | Show/hide a layer named `NDVI` or `NVDI` |

The spot you click with OpenInGE, OpenInMaps or OpenInStreet (toolbar or
right-click menu) is marked on the QGIS map with a red cross. The next click
replaces it; **Web ▸ GEtools ▸ Clear target marker** (also on the right-click
menu) removes it.

Extras: **Open current view in Google Earth Web** (Web ▸ GEtools) opens your
browser at the current map view, and the right-click menu has **Copy lat, lon**.

## Install

1. Download [`getools.zip`](https://github.com/JonathanMagson/GEtoolsQGIS/raw/main/getools.zip). After changing the code, rebuild it with `./build_zip.sh`.
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, pick the zip, click **Install Plugin**.

## How the Google Earth Web tools work

**SyncToGE** keeps Google Earth Web in step with the QGIS map. Each time the
map settles after a pan, zoom or rotation, Google Earth moves to match. It runs
in one of two ways, chosen automatically:

1. **Google Earth Web panel inside QGIS**, if your QGIS has Qt WebEngine
   (common on Linux; `python3-pyqt5.qtwebengine`). Turn SyncToGE off or close
   the panel to stop.
2. **A dedicated Chrome or Edge window** otherwise. This is the usual case on
   Windows, where the QGIS installers don't include Qt WebEngine. Edge comes
   with Windows, so nothing extra needs installing; Chrome is used if it's
   there. The plugin opens Google Earth Web in its own app-style window and
   steers it from QGIS over the browser's local DevTools connection. Close the
   window or turn SyncToGE off to stop.
   - The window has its own browser profile (`getools_browser` in your QGIS
     profile folder), separate from your everyday browser, so it remembers
     Google's cookie prompt between sessions.
   - The DevTools port only listens on `127.0.0.1` (this computer).

If Google Earth stops following the map, untick **Web ▸ GEtools ▸ Fast sync (no
reload)** (or the checkbox on the panel). Sync is then slower but more reliable,
because the page reloads at each new view.

If neither Qt WebEngine nor Chrome/Edge is available, SyncToGE opens the
current view in your default browser once and says why.

**OpenInGE** and **LayerToGE** use the synced view (panel or window) when sync is on, and your default browser otherwise.

**LayerToGE**: Google Earth Web has no URL or API that loads a file, so the
KML has to be imported by hand. The plugin writes the KML to
`Documents/OpenInGE_kml_temp`, the same folder the add-in used, and clears old
KMLs first. It copies the path to the clipboard and opens Google Earth Web on
the layer. In Google Earth Web, choose **Projects ▸ New project ▸ Import KML file
from computer** and paste the path. Placemarks are named from the layer's label
field, or its display field if there's no label field.

The Google Earth Pro tip "set Fly-To Speed to 5" no longer applies.

## Development

`getools/google_urls.py` and `getools/cdp.py` have no QGIS dependency; the URL code is unit tested:

```bash
pip install pytest
pytest tests
```

## Licence

See `getools/LICENSE.txt`. SyncToGE descends from Chris Stayte's MIT-licensed
[ArcPro_To_GoogleEarth](https://github.com/chrisstayte/ArcPro_To_GoogleEarth),
and the icon credits from the original add-in are carried over.
