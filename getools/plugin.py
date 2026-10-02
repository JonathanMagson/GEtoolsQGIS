"""GEtools for QGIS: Google Earth Web, Google Maps and Street View tools."""

import os

from qgis.PyQt.QtCore import QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication, QMenu, QPushButton
from qgis.core import Qgis, QgsProject, QgsVectorLayer

from . import google_urls as urls
from .earth_dock import DOCK_AREA, WEBENGINE_AVAILABLE, EarthDock
from .geo import canvas_camera, extent_camera, to_lat_lon
from .layer_export import export_layer_to_kml
from .map_tools import PointPickTool

MENU = "&GEtools"
ICONS = os.path.join(os.path.dirname(__file__), "icons")
NDVI_LAYER_NAMES = ("ndvi", "nvdi")  # the ArcGIS add-in looked for "NVDI"
SYNC_DELAY_MS = 400  # wait for panning/zooming to settle before syncing


def _icon(name):
    return QIcon(os.path.join(ICONS, name))


class GEtoolsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.actions = []
        self.dock = None
        self.tools = {}

        self.sync_timer = QTimer()
        self.sync_timer.setSingleShot(True)
        self.sync_timer.setInterval(SYNC_DELAY_MS)
        self.sync_timer.timeout.connect(self.sync_now)

    # ---- QGIS plugin hooks -------------------------------------------------

    def initGui(self):
        self.toolbar = self.iface.addToolBar("GEtools")
        self.toolbar.setObjectName("GEtoolsToolbar")

        self.open_in_ge = self._add_action(
            "icon.png", "OpenInGE", "Click the map to open that spot in Google Earth Web",
            checkable=True)
        self.sync_ge = self._add_action(
            "sync-icon.png", "SyncToGE", "Keep Google Earth Web in step with the QGIS map",
            checkable=True)
        self.layer_to_ge = self._add_action(
            "earth_icon.png", "LayerToGE",
            "Export the active vector layer to KML and open Google Earth Web on it")
        self.open_in_maps = self._add_action(
            "location-pin.png", "OpenInMaps", "Click the map to open that spot in Google Maps",
            checkable=True)
        self.open_in_street = self._add_action(
            "street-view.png", "OpenInStreet", "Click the map to open that spot in Street View",
            checkable=True)
        self.toggle_ndvi = self._add_action(
            None, "Toggle NDVI", "Show/hide the layer named 'NDVI' (or 'NVDI')")
        self.current_view = self._add_action(
            "icon.png", "Open current view in Google Earth Web",
            "Open Google Earth Web in your browser at the current map view",
            toolbar=False)

        self.tools = {
            self.open_in_ge: PointPickTool(self.canvas, self.open_in_ge, self.show_in_earth, self._warn),
            self.open_in_maps: PointPickTool(
                self.canvas, self.open_in_maps,
                lambda lat, lon: self._open_browser(urls.google_maps_url(lat, lon)), self._warn),
            self.open_in_street: PointPickTool(
                self.canvas, self.open_in_street,
                lambda lat, lon: self._open_browser(urls.street_view_url(lat, lon)), self._warn),
        }
        for action, tool in self.tools.items():
            action.triggered.connect(lambda checked, t=tool: self.canvas.setMapTool(t))

        self.sync_ge.toggled.connect(self.set_sync)
        self.layer_to_ge.triggered.connect(self.send_layer)
        self.toggle_ndvi.triggered.connect(self.toggle_ndvi_layer)
        self.current_view.triggered.connect(self.open_current_view_in_browser)

        # Right-click on the map (needs QGIS 3.16+).
        if hasattr(self.canvas, "contextMenuAboutToShow"):
            self.canvas.contextMenuAboutToShow.connect(self.populate_context_menu)

    def unload(self):
        self.set_sync(False)
        if hasattr(self.canvas, "contextMenuAboutToShow"):
            try:
                self.canvas.contextMenuAboutToShow.disconnect(self.populate_context_menu)
            except TypeError:
                pass
        for tool in self.tools.values():
            if self.canvas.mapTool() is tool:
                self.canvas.unsetMapTool(tool)
        for action in self.actions:
            self.iface.removePluginWebMenu(MENU, action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        self.toolbar.deleteLater()
        self.actions = []

    # ---- helpers -----------------------------------------------------------

    def _add_action(self, icon, text, tip, checkable=False, toolbar=True):
        action = QAction(_icon(icon) if icon else QIcon(), text, self.iface.mainWindow())
        action.setToolTip(tip)
        action.setStatusTip(tip)
        action.setCheckable(checkable)
        if toolbar:
            self.toolbar.addAction(action)
        self.iface.addPluginToWebMenu(MENU, action)
        self.actions.append(action)
        return action

    def _message(self, text, level=Qgis.Info, widget=None, duration=6):
        bar = self.iface.messageBar()
        item = bar.createMessage("GEtools", text)
        if widget is not None:
            item.layout().addWidget(widget)
        bar.pushWidget(item, level, duration)

    def _warn(self, text):
        self._message(text, Qgis.Warning)

    def _open_browser(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            self._warn(f"Couldn't open a web browser for {url}")

    def _earth_dock(self, create=True):
        """The embedded Google Earth Web panel, or None if it can't exist."""
        if not WEBENGINE_AVAILABLE:
            return None
        if self.dock is None and create:
            self.dock = EarthDock(self.iface.mainWindow())
            self.iface.addDockWidget(DOCK_AREA, self.dock)
            self.dock.visibilityChanged.connect(self._dock_visibility_changed)
        return self.dock

    def _dock_visibility_changed(self, visible):
        # Closing the panel ends syncing, like deactivating SyncToGE did.
        if not visible and self.sync_ge.isChecked() and self.dock and self.dock.isHidden():
            self.sync_ge.setChecked(False)

    def show_url_in_earth(self, url):
        """Use the Earth panel if it's open, else the system browser."""
        dock = self._earth_dock(create=False)
        if dock is not None and dock.isVisible():
            dock.navigate(url)
            dock.raise_()
        else:
            self._open_browser(url)

    # ---- tools -------------------------------------------------------------

    def show_in_earth(self, lat, lon):
        """OpenInGE: fly to a clicked point and drop a pin on it."""
        self.show_url_in_earth(urls.earth_point_url(lat, lon))

    def set_sync(self, on):
        """SyncToGE: follow every pan, zoom and rotation of the QGIS map."""
        if on:
            dock = self._earth_dock()
            if dock is None:
                # No embedded browser: the system browser can't be driven
                # live, so open the current view once and say why.
                self.sync_ge.setChecked(False)
                self.open_current_view_in_browser()
                self._warn(
                    "Live sync needs Qt WebEngine, which this QGIS install doesn't have. "
                    "Opened the current view in your browser instead; use "
                    "Web ▸ GEtools ▸ Open current view in Google Earth Web to refresh it.")
                return
            dock.show()
            dock.raise_()
            self.canvas.extentsChanged.connect(self.sync_timer.start)
            self.canvas.rotationChanged.connect(self.sync_timer.start)
            self.canvas.destinationCrsChanged.connect(self.sync_timer.start)
            self.sync_now()
        else:
            self.sync_timer.stop()
            for signal in (self.canvas.extentsChanged, self.canvas.rotationChanged,
                           self.canvas.destinationCrsChanged):
                try:
                    signal.disconnect(self.sync_timer.start)
                except TypeError:
                    pass  # wasn't connected

    def current_view_url(self):
        camera = canvas_camera(self.canvas)
        if camera is None:
            return None
        lat, lon, range_m, heading = camera
        return urls.earth_view_url(lat, lon, range_m, heading=heading)

    def sync_now(self):
        url = self.current_view_url()
        if url and self.dock is not None:
            self.dock.navigate(url)

    def open_current_view_in_browser(self):
        url = self.current_view_url()
        if url is None:
            self._warn("The map centre can't be converted to latitude/longitude.")
        else:
            self._open_browser(url)

    def send_layer(self):
        """LayerToGE: export to KML, then open Earth Web framed on the layer.

        Google Earth Web has no URL for loading a file, so the KML has to be
        imported by hand (Projects ▸ New project ▸ Import KML file from
        computer). The path is copied to the clipboard to make that quick.
        """
        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            self._warn("Select a vector layer in the Layers panel first.")
            return
        if not layer.isSpatial():
            self._warn(f"'{layer.name()}' has no geometry to show in Google Earth.")
            return

        selected_only = layer.selectedFeatureCount() > 0
        try:
            path = export_layer_to_kml(layer, selected_only)
        except RuntimeError as exc:
            self._message(f"KML export failed: {exc}", Qgis.Critical, duration=10)
            return

        extent = layer.boundingBoxOfSelected() if selected_only else layer.extent()
        camera = extent_camera(extent, layer.crs())
        if camera is not None:
            self.show_url_in_earth(urls.earth_view_url(*camera))

        QApplication.clipboard().setText(path)
        folder_button = QPushButton("Open folder")
        folder_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
        what = "selected features of " if selected_only else ""
        self._message(
            f"Exported {what}'{layer.name()}' to {path} (path copied to clipboard). "
            "In Google Earth Web: Projects ▸ New project ▸ Import KML file from computer.",
            Qgis.Success, widget=folder_button, duration=0)

    def toggle_ndvi_layer(self):
        root = QgsProject.instance().layerTreeRoot()
        nodes = [n for n in root.findLayers() if n.name().strip().lower() in NDVI_LAYER_NAMES]
        if not nodes:
            self._warn("No layer named 'NDVI' (or 'NVDI') in the Layers panel.")
            return
        visible = not nodes[0].isVisible()
        for node in nodes:
            node.setItemVisibilityChecked(visible)
            if visible:
                node.setItemVisibilityCheckedParentRecursive(True)

    # ---- right-click menu --------------------------------------------------

    def populate_context_menu(self, menu, event):
        crs = self.canvas.mapSettings().destinationCrs()
        lat_lon = to_lat_lon(event.mapPoint(), crs)
        sub = QMenu("GEtools", menu)
        sub.setIcon(_icon("icon.png"))

        def add(icon, text, slot, enabled=True):
            action = sub.addAction(_icon(icon), text)
            action.triggered.connect(slot)
            action.setEnabled(enabled)

        ok = lat_lon is not None
        lat, lon = lat_lon if ok else (0.0, 0.0)
        add("icon.png", "Open here in Google Earth Web", lambda: self.show_in_earth(lat, lon), ok)
        add("location-pin.png", "Open here in Google Maps",
            lambda: self._open_browser(urls.google_maps_url(lat, lon)), ok)
        add("street-view.png", "Open here in Street View",
            lambda: self._open_browser(urls.street_view_url(lat, lon)), ok)
        sub.addSeparator()
        sub.addAction(self.sync_ge)
        if ok:
            sub.addSeparator()
            coords = f"{lat:.6f}, {lon:.6f}"
            copy = sub.addAction(f"Copy {coords}")
            copy.triggered.connect(lambda: QApplication.clipboard().setText(coords))
        menu.addSeparator()
        menu.addMenu(sub)
