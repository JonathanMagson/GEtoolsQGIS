# GEtools for QGIS

A QGIS port of my [GEtools add-in for ArcGIS Pro](https://github.com/JonathanMagson/GEtoolsAppNWD).
It works with either **Google Earth Web** (earth.google.com/web, in a browser)
or the **Google Earth Pro** desktop app: pick one in **Settings ▸ Google Earth
version**. Google Earth Web is the default, so Google Earth Pro doesn't need to
be installed.

Requires QGIS 3.16 or newer. The tools are on the **GEtools** toolbar, under
**Web ▸ GEtools**, and on the map canvas right-click menu (**GEtools** submenu).

| Tool | Shortcut | What it does |
|---|---|---|
| **OpenInGE** | Alt+Shift+G | Click the map: Google Earth opens on that spot, marked with a red X |
| **SyncToGE** | Alt+Shift+S | Google Earth follows the map's pan, zoom and rotation; optionally the map follows Google Earth too |
| **LayerToGE** | Alt+Shift+L | Exports the active layer (or its selected features) to KML and loads it into Google Earth |
| **OpenInMaps** | Alt+Shift+M | Click the map: opens that spot in Google Maps |
| **OpenInStreet** | Alt+Shift+V | Click the map: opens that spot in Street View |
| **Clear targets** | Alt+Shift+X | Removes the target markers |

The shortcuts use Alt+Shift, which neither QGIS nor its bundled plugins use.
Change them in **Settings ▸ Keyboard Shortcuts** (or the button in GEtools
Settings).

Extras: **Open current view in Google Earth** (Web ▸ GEtools) shows the
current map view in Google Earth Pro, or in your normal browser for Google
Earth Web, and the right-click menu has **Copy lat, lon**.

## Install

1. Download [`getools-1.5.1.zip`](https://github.com/JonathanMagson/GEtoolsQGIS/raw/main/getools-1.5.1.zip). After changing the code (and the version in `getools/metadata.txt`), rebuild it with `./build_zip.sh`, which names the zip after the version and updates this link.
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, pick the zip, click **Install Plugin**.

## The Google Earth window

### Google Earth Web

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

**Fast sync** moves Google Earth Web without reloading the page. The first time
it's used, the plugin checks that Google Earth actually moved. If it didn't,
the plugin reloads instead from then on (slower, but it follows) and says so.

### Google Earth Pro

The plugin opens one small file, `GEtools_link.kml`, in Google Earth Pro. It
appears under Temporary Places as **GEtools (QGIS)** and keeps a live link to
QGIS while both are open:

- QGIS sends Google Earth where to look, only when there's somewhere new to
  go. Google Earth doesn't keep snapping back to the map's view, so you
  can move around in it while sync is on.
- Google Earth reports its view to QGIS whenever it stops moving (for two-way
  sync and the view outline).
- Targets appear as placemarks with an X icon.

The link runs through a small server inside QGIS that only answers on
`127.0.0.1` (this computer), and only to addresses containing a random key, so
other users of a shared machine can't use it.

The plugin finds Google Earth Pro in its usual install location; set the
program in Settings if it's elsewhere. For quicker syncing, set Google Earth
Pro's **Tools ▸ Options ▸ Navigation ▸ Fly-To Speed** to the fastest setting.
If you remove the **GEtools (QGIS)** folder from Google Earth, or close Google
Earth, the plugin turns SyncToGE off; the next Google Earth action reconnects.

## Tools in detail

**SyncToGE**: turning it on opens Google Earth at the current map view (or
brings it forward if it's already open). After that, each time the QGIS map
settles after a pan, zoom or rotation, Google Earth moves to match. Close
Google Earth, or turn SyncToGE off, to stop.

- **Two-way sync** (Settings, off by default): while SyncToGE is on, moving
  Google Earth moves the QGIS map to the same place, zoom and rotation. QGIS has
  no tilt, so a tilted Google Earth view is matched on the point it looks at.
- **Show Google Earth's view on the QGIS map** (Settings, off by default):
  outlines the ground Google Earth is showing, including tilted views. This
  works whenever the Google Earth window is open, synced or not.
- With Google Earth Web, both read the camera position Google Earth keeps in
  its address, which it updates once the view settles. Google Earth Pro reports
  its view over the link instead.

**OpenInGE** always uses Google Earth (even with sync off) and marks the
clicked spot with an **X**. In Google Earth Pro the X is a placemark. Google
Earth Web can't be given a placemark from outside, so there the plugin draws the
X over the globe itself, positioned from
Google Earth's camera. The X stays on the spot as you pan, zoom and tilt; it
hides while you drag or scroll and comes back once the view settles.

The spot is also marked on the QGIS map with a cross (for OpenInMaps and
OpenInStreet too). The next click replaces it, unless **Keep every target** is
on in Settings. **Clear targets** removes them from QGIS and Google Earth.

**LayerToGE** exports the active vector layer, or only its selected features,
to KML (in `Documents/OpenInGE_kml_temp`, clearing old KMLs first). Then:

- **Google Earth Pro** opens the KML and flies to it.
- **Google Earth Web**: the plugin opens Google Earth framed on the layer and
  **drops the KML onto it**, the same as dragging the file there from Explorer.
- If the layer doesn't appear, or automatic loading is off in Settings, the
  KML path is on the clipboard: in Google Earth Web choose **Projects ▸ New
  project ▸ Import KML file from computer**.

Placemarks are named from the layer's label field, or its display field if
there's no label field.

## Settings

**Web ▸ GEtools ▸ Settings…** (also on the right-click menu):

| Setting | Default |
|---|---|
| Google Earth version: Google Earth Web or Google Earth Pro (and the Earth Pro program, if not found automatically) | Google Earth Web |
| Browser (Google Earth Web): Automatic (Chrome, else Edge), Chrome, Edge, or another Chromium browser | Automatic |
| Bring Google Earth to front when OpenInGE or LayerToGE moves it (restores a minimized window; syncing never does) | On |
| Fast sync (Google Earth Web): move Google Earth without reloading | On |
| Sync delay: how long the map must stay still before Google Earth follows | 400 ms |
| Two-way sync | Off |
| Show Google Earth's view on the QGIS map, and its outline colour | Off, orange |
| OpenInGE view distance | 1000 m |
| OpenInGE view tilt (0° looks straight down) | 0° |
| Keep every target | Off |
| Target colour and size | Red, 30 px |
| LayerToGE (Google Earth Web): load the layer automatically | On |

**Restore Defaults** resets them all.

Settings that only apply to one version are greyed out while the other is
chosen.

## Development

`getools/google_urls.py`, `camera.py`, `earth_marker.py`, `earth_pro_kml.py`
and `cdp.py` have no QGIS dependency and are unit tested:

```bash
pip install pytest
pytest tests
```

## Licence

GEtools is released under the [MIT License](LICENSE), copyright Jonathan
Magson. The toolbar icons are by other authors and keep their own terms; see
[`getools/LICENSE.txt`](getools/LICENSE.txt) for their credits.
