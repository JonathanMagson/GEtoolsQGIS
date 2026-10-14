"""GEtools for QGIS: Google Earth Web, Google Maps and Street View tools."""

import math
import os
import time

from qgis.PyQt.QtCore import QTimer, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication, QMenu, QPushButton
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsCsException,
    QgsDistanceArea,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsRubberBand, QgsVertexMarker

from . import google_urls as urls
from . import settings
from .camera import Camera, cameras_close, footprint, parse_camera
from .cdp import find_browser
from .earth_dock import DOCK_AREA, WEBENGINE_AVAILABLE, EarthDock
from .earth_marker import marker_js
from .external_earth import ExternalEarthWindow
from .geo import WGS84, canvas_camera, extent_camera, to_lat_lon
from .layer_export import export_layer_to_kml
from .map_tools import PointPickTool
from .settings_dialog import SettingsDialog

MENU = "&GEtools"
ICONS = os.path.join(os.path.dirname(__file__), "icons")

VIEW_POLL_MS = 600      # how often to read Google Earth's camera
ECHO_GRACE_S = 3.0      # ignore Earth moves this long after QGIS moved it
DROP_TIMEOUT_S = 40.0   # give up waiting for Earth Web to load for LayerToGE
DROP_SETTLE_S = 2.0     # let Earth Web finish starting before dropping a file

# Default shortcuts. Alt+Shift+<letter> isn't used by QGIS or its bundled
# plugins; users can change them in Settings ▸ Keyboard Shortcuts.
SHORTCUTS = {
    "OpenInGE": "Alt+Shift+G",
    "SyncToGE": "Alt+Shift+S",
    "LayerToGE": "Alt+Shift+L",
    "OpenInMaps": "Alt+Shift+M",
    "OpenInStreet": "Alt+Shift+V",
    "Clear targets": "Alt+Shift+X",
}


def _icon(name):
    return QIcon(os.path.join(ICONS, name))


class GEtoolsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.actions = []
        self.shortcut_actions = []
        self.dock = None
        self.external = None  # Chrome/Edge window, when there's no WebEngine
        self.targets = []  # (lat, lon, QgsVertexMarker) per marked spot
        self.footprint_band = None
        self.tools = {}

        # What Google Earth is showing as far as we know, and when QGIS last
        # moved it; two-way sync uses these to tell user moves from echoes.
        self.last_earth_camera = None
        self.sent_at = 0.0
        self.drop_job = None

        self.sync_timer = QTimer()
        self.sync_timer.setSingleShot(True)
        self.sync_timer.timeout.connect(self.sync_now)
        self.view_timer = QTimer()
        self.view_timer.setInterval(VIEW_POLL_MS)
        self.view_timer.timeout.connect(self.poll_view)
        self.drop_timer = QTimer()
        self.drop_timer.setInterval(500)
        self.drop_timer.timeout.connect(self._drop_step)

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
            "Send the active vector layer (or its selected features) to Google Earth Web")
        self.open_in_maps = self._add_action(
            "location-pin.png", "OpenInMaps", "Click the map to open that spot in Google Maps",
            checkable=True)
        self.open_in_street = self._add_action(
            "street-view.png", "OpenInStreet", "Click the map to open that spot in Street View",
            checkable=True)
        self.current_view = self._add_action(
            "icon.png", "Open current view in Google Earth Web",
            "Open Google Earth Web in your browser at the current map view",
            toolbar=False)
        self.clear_targets_action = self._add_action(
            None, "Clear targets", "Remove the target markers from QGIS and Google Earth",
            toolbar=False)
        self.settings_action = self._add_action(
            None, "Settings…", "GEtools options", toolbar=False)

        self.tools = {
            self.open_in_ge: PointPickTool(
                self.canvas, self.open_in_ge,
                lambda lat, lon, pt: self.open_point("earth", lat, lon, pt), self._warn),
            self.open_in_maps: PointPickTool(
                self.canvas, self.open_in_maps,
                lambda lat, lon, pt: self.open_point("maps", lat, lon, pt), self._warn),
            self.open_in_street: PointPickTool(
                self.canvas, self.open_in_street,
                lambda lat, lon, pt: self.open_point("street", lat, lon, pt), self._warn),
        }
        for action, tool in self.tools.items():
            action.triggered.connect(lambda checked, t=tool: self.canvas.setMapTool(t))

        self.sync_ge.toggled.connect(self.set_sync)
        self.layer_to_ge.triggered.connect(self.send_layer)
        self.current_view.triggered.connect(self.open_current_view_in_browser)
        self.clear_targets_action.triggered.connect(self.clear_targets)
        self.settings_action.triggered.connect(self.show_settings)
        self.canvas.destinationCrsChanged.connect(self._reproject_overlays)

        for action in self.actions:
            shortcut = SHORTCUTS.get(action.text())
            if shortcut:
                self.iface.registerMainWindowAction(action, shortcut)
                self.shortcut_actions.append(action)

        # Right-click on the map (needs QGIS 3.16+).
        if hasattr(self.canvas, "contextMenuAboutToShow"):
            self.canvas.contextMenuAboutToShow.connect(self.populate_context_menu)

        self.apply_settings()

    def unload(self):
        self.set_sync(False)
        self.view_timer.stop()
        self.drop_timer.stop()
        self.clear_targets()
        self._remove_footprint()
        if self.external is not None:
            self.external.stop()
            self.external = None
        for signal, slot in ((self.canvas.destinationCrsChanged, self._reproject_overlays),
                             (getattr(self.canvas, "contextMenuAboutToShow", None),
                              self.populate_context_menu)):
            try:
                if signal is not None:
                    signal.disconnect(slot)
            except TypeError:
                pass
        for tool in self.tools.values():
            if self.canvas.mapTool() is tool:
                self.canvas.unsetMapTool(tool)
        for action in self.shortcut_actions:
            self.iface.unregisterMainWindowAction(action)
        for action in self.actions:
            self.iface.removePluginWebMenu(MENU, action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        self.toolbar.deleteLater()
        self.actions = []
        self.shortcut_actions = []

    # ---- helpers -----------------------------------------------------------

    def _add_action(self, icon, text, tip, checkable=False, toolbar=True):
        action = QAction(_icon(icon) if icon else QIcon(), text, self.iface.mainWindow())
        action.setObjectName("mActionGEtools" + "".join(c for c in text if c.isalnum()))
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

    # ---- the Google Earth view (panel or browser window) -------------------

    def _earth_dock(self, create=True):
        """The embedded Google Earth Web panel, or None if it can't exist."""
        if not WEBENGINE_AVAILABLE:
            return None
        if self.dock is None and create:
            self.dock = EarthDock(self.iface.mainWindow())
            self.iface.addDockWidget(DOCK_AREA, self.dock)
            self.dock.visibilityChanged.connect(self._dock_visibility_changed)
            self._push_markers()
        return self.dock

    def _external_window(self, create=True):
        """The Chrome/Edge window driver, or None if no browser is set up."""
        if self.external is None and create:
            browser = find_browser(settings.get("browser"), settings.get("browser_path"))
            if browser is None:
                return None
            self.external = ExternalEarthWindow(browser)
            self.external.closed.connect(self._external_closed)
            self._push_markers()
        return self.external

    def _earth_view(self, create=True):
        """The Google Earth view the plugin controls: the QGIS panel if Qt
        WebEngine is available, else the Chrome/Edge window. ``None`` when
        neither is possible (or, with ``create=False``, not open yet)."""
        if WEBENGINE_AVAILABLE:
            return self._earth_dock(create)
        return self._external_window(create)

    def _open_view(self):
        """The controlled Earth view if it's currently showing, else None."""
        view = self._earth_view(create=False)
        return view if view is not None and view.is_open() else None

    def _show_in_view(self, view, url, bring_to_front=None):
        if bring_to_front is None:
            bring_to_front = settings.get("raise_earth_window")
        if view is self.dock:
            view.show()
            if bring_to_front:
                view.raise_()
            view.navigate(url)
        else:
            view.navigate(url, bring_to_front=bring_to_front)

    def _sent(self, camera):
        """Remember that QGIS just pointed Google Earth at ``camera``."""
        self.last_earth_camera = camera
        self.sent_at = time.monotonic()

    def _external_closed(self, reason):
        if self.sync_ge.isChecked():
            self.sync_ge.setChecked(False)
            self._warn(f"SyncToGE turned off: {reason}")
        self._remove_footprint()

    def _dock_visibility_changed(self, visible):
        if not visible and self.dock and self.dock.isHidden():
            # Closing the panel ends syncing, like deactivating SyncToGE did.
            if self.sync_ge.isChecked():
                self.sync_ge.setChecked(False)
            self._remove_footprint()

    # ---- settings ----------------------------------------------------------

    def show_settings(self):
        dialog = SettingsDialog(self.iface.mainWindow(), webengine=WEBENGINE_AVAILABLE)
        run = getattr(dialog, "exec", None) or dialog.exec_
        if run():
            browser_before = (settings.get("browser"), settings.get("browser_path"))
            dialog.save()
            if (settings.get("browser"), settings.get("browser_path")) != browser_before:
                # Use the newly chosen browser from the next Earth action on.
                if self.external is not None:
                    self.external.stop()
                    self.external = None
            self.apply_settings()

    def apply_settings(self):
        self.sync_timer.setInterval(settings.get("sync_delay_ms"))
        if self.dock is not None:
            self.dock.soft_nav.setChecked(settings.get("fast_sync"))
        if not settings.get("keep_all_targets"):
            for _, _, marker in self.targets[:-1]:
                self.canvas.scene().removeItem(marker)
            self.targets = self.targets[-1:]
        for _, _, marker in self.targets:
            self._style_marker(marker)
        self._push_markers()
        if self.footprint_band is not None:
            self._style_footprint(self.footprint_band)
        if not settings.get("show_footprint"):
            self._remove_footprint()
        self._update_view_polling()

    # ---- targets -----------------------------------------------------------

    def _style_marker(self, marker):
        marker.setIconType(QgsVertexMarker.ICON_CROSS)
        marker.setColor(QColor(settings.get("target_color")))
        marker.setIconSize(max(8, int(settings.get("target_size") * 0.6)))
        marker.setPenWidth(3)

    def add_target(self, lat, lon, map_point):
        """Mark a clicked spot (in QGIS now, and in Google Earth when it's
        next shown). Replaces the last target unless "keep every target" is on."""
        if not settings.get("keep_all_targets"):
            self._remove_qgis_targets()
        marker = QgsVertexMarker(self.canvas)
        self._style_marker(marker)
        marker.setCenter(map_point)
        self.targets.append((lat, lon, marker))
        self._push_markers()

    def _remove_qgis_targets(self):
        for _, _, marker in self.targets:
            self.canvas.scene().removeItem(marker)
        self.targets = []

    def clear_targets(self):
        self._remove_qgis_targets()
        self._push_markers()

    def _push_markers(self):
        """Send the current targets to the Google Earth view(s)."""
        js = None
        if self.targets:
            js = marker_js([(lat, lon) for lat, lon, _ in self.targets],
                           settings.get("target_color"), settings.get("target_size"))
        for view in (self.dock, self.external):
            if view is not None:
                view.set_markers(js)

    def _reproject_overlays(self):
        transform = QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(),
                                           QgsProject.instance())
        for lat, lon, marker in self.targets:
            try:
                marker.setCenter(transform.transform(QgsPointXY(lon, lat)))
            except QgsCsException:
                marker.hide()
        self._remove_footprint()  # redrawn on the next poll

    # ---- tools -------------------------------------------------------------

    def open_point(self, kind, lat, lon, map_point):
        """Mark a clicked spot, then open it in Google Earth, Maps or Street View."""
        self.add_target(lat, lon, map_point)
        if kind == "earth":
            self.open_point_in_earth(lat, lon)
        elif kind == "maps":
            self._open_browser(urls.google_maps_url(lat, lon))
        else:
            self._open_browser(urls.street_view_url(lat, lon))

    def open_point_in_earth(self, lat, lon):
        """OpenInGE: show the spot in Google Earth Web, marked with an X.

        Uses the Earth panel or the Chrome/Edge window even when sync is off,
        because only a page the plugin controls can carry the X.
        """
        view = self._earth_view()
        if view is None:
            self._open_browser(urls.earth_point_url(lat, lon, settings.get("point_range_m")))
            return
        range_m, tilt = settings.get("point_range_m"), settings.get("point_tilt")
        self._sent(Camera(lat, lon, range_m, 0.0, tilt, urls.DEFAULT_FOV, 0.0))
        self._show_in_view(view, urls.earth_view_url(lat, lon, range_m, tilt=tilt))
        self._update_view_polling()

    # ---- SyncToGE ----------------------------------------------------------

    def set_sync(self, on):
        """SyncToGE: follow every pan, zoom and rotation of the QGIS map."""
        signals = (self.canvas.extentsChanged, self.canvas.rotationChanged,
                   self.canvas.destinationCrsChanged)
        if on:
            view = self._earth_view()
            if view is None:
                # Nothing that can be driven live: open the view once.
                self.sync_ge.setChecked(False)
                self.open_current_view_in_browser()
                self._warn(
                    "Live sync needs Google Chrome or Microsoft Edge (or Qt WebEngine in QGIS). "
                    "Opened the current view in your browser instead. Choose a browser in "
                    "Web ▸ GEtools ▸ Settings.")
                return
            if view is self.dock:
                view.show()
                view.raise_()
            for signal in signals:
                signal.connect(self.sync_timer.start)
            self.last_earth_camera = None  # force the first sync
            self.sync_now()
        else:
            self.sync_timer.stop()
            for signal in signals:
                try:
                    signal.disconnect(self.sync_timer.start)
                except TypeError:
                    pass  # wasn't connected
        self._update_view_polling()

    def _canvas_camera(self):
        camera = canvas_camera(self.canvas)
        if camera is None:
            return None
        lat, lon, range_m, heading = camera
        return Camera(lat, lon, range_m, heading, 0.0, urls.DEFAULT_FOV, 0.0)

    def current_view_url(self):
        cam = self._canvas_camera()
        if cam is None:
            return None
        return urls.earth_view_url(cam.lat, cam.lon, cam.range, heading=cam.heading)

    def sync_now(self):
        if not self.sync_ge.isChecked():
            return
        cam = self._canvas_camera()
        if cam is None:
            return
        # With two-way sync on, QGIS may just have moved to match Google
        # Earth; don't bounce that back (it would undo Earth's tilt).
        if settings.get("two_way_sync") and cameras_close(cam, self.last_earth_camera):
            return
        view = self._earth_view(create=False)
        if view is None:
            return
        self._sent(cam)
        url = urls.earth_view_url(cam.lat, cam.lon, cam.range, heading=cam.heading)
        if view is self.dock:
            view.navigate(url)
        else:
            view.navigate(url, bring_to_front=False)

    def open_current_view_in_browser(self):
        url = self.current_view_url()
        if url is None:
            self._warn("The map centre can't be converted to latitude/longitude.")
        else:
            self._open_browser(url)

    # ---- reading Google Earth back: two-way sync and the footprint ---------

    def _update_view_polling(self):
        wanted = settings.get("show_footprint") or (
            settings.get("two_way_sync") and self.sync_ge.isChecked())
        has_view = self.dock is not None or self.external is not None
        if wanted and has_view:
            if not self.view_timer.isActive():
                self.view_timer.start()
        else:
            self.view_timer.stop()

    def poll_view(self):
        view = self._open_view()
        if view is None:
            self._remove_footprint()
            return
        view.query_view(self._on_view_info)

    def _on_view_info(self, info):
        cam = parse_camera(info.get("href")) if info else None
        if cam is None:
            self._remove_footprint()
            return
        if settings.get("show_footprint"):
            width, height = info.get("width") or 0, info.get("height") or 0
            self._draw_footprint(cam, width / height if width > 0 and height > 0 else 1.5)

        two_way = settings.get("two_way_sync") and self.sync_ge.isChecked()
        if (two_way and not self.sync_timer.isActive()
                and time.monotonic() - self.sent_at > ECHO_GRACE_S
                and not cameras_close(cam, self.last_earth_camera)):
            # The user moved Google Earth: bring QGIS to the same place.
            self.last_earth_camera = cam
            self.apply_camera_to_canvas(cam)

    def apply_camera_to_canvas(self, cam):
        """Centre, zoom and rotate the QGIS map to match a Google Earth camera."""
        crs = self.canvas.mapSettings().destinationCrs()
        try:
            centre = QgsCoordinateTransform(WGS84, crs, QgsProject.instance()).transform(
                QgsPointXY(cam.lon, cam.lat))
        except QgsCsException:
            return
        # Ground height covered by Earth's view (inverse of the range QGIS
        # sends), converted to map units at the centre.
        ground_h = 2.0 * cam.range * math.tan(math.radians(cam.fov) / 2.0)
        da = QgsDistanceArea()
        da.setSourceCrs(crs, QgsProject.instance().transformContext())
        da.setEllipsoid("WGS84")
        step = max(self.canvas.extent().height() * 1e-3, 1e-9)
        try:
            metres_per_unit = da.measureLine(
                centre, QgsPointXY(centre.x(), centre.y() + step)) / step
        except QgsCsException:
            return
        if not metres_per_unit > 0:
            return
        size = self.canvas.mapSettings().outputSize()
        half_h = ground_h / metres_per_unit / 2.0
        half_w = half_h * size.width() / max(size.height(), 1)
        rotation = (-cam.heading + 180.0) % 360.0 - 180.0
        self.canvas.setRotation(rotation)
        self.canvas.setExtent(QgsRectangle(centre.x() - half_w, centre.y() - half_h,
                                           centre.x() + half_w, centre.y() + half_h))
        self.canvas.refresh()

    def _style_footprint(self, band):
        colour = QColor(settings.get("footprint_color"))
        band.setStrokeColor(colour)
        fill = QColor(colour)
        fill.setAlpha(40)
        band.setFillColor(fill)
        band.setWidth(2)

    def _draw_footprint(self, cam, aspect):
        transform = QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(),
                                           QgsProject.instance())
        try:
            ring = [transform.transform(QgsPointXY(lon, lat)) for lat, lon in footprint(cam, aspect)]
        except QgsCsException:
            self._remove_footprint()
            return
        if self.footprint_band is None:
            polygon = (Qgis.GeometryType.Polygon if hasattr(Qgis, "GeometryType")
                       else QgsWkbTypes.PolygonGeometry)
            self.footprint_band = QgsRubberBand(self.canvas, polygon)
            self._style_footprint(self.footprint_band)
        self.footprint_band.setToGeometry(QgsGeometry.fromPolygonXY([ring]), None)
        self.footprint_band.show()

    def _remove_footprint(self):
        if self.footprint_band is not None:
            self.canvas.scene().removeItem(self.footprint_band)
            self.footprint_band = None

    # ---- LayerToGE ---------------------------------------------------------

    def send_layer(self):
        """LayerToGE: export the layer to KML and load it into Google Earth.

        Google Earth Web has no URL or API for loading a file, so the plugin
        drops the KML onto the Earth window it controls, the same as dragging
        it there from the file manager. Without such a window (or with the
        option off), the KML is left for importing by hand.
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
        what = ("selected features of " if selected_only else "") + f"'{layer.name()}'"

        extent = layer.boundingBoxOfSelected() if selected_only else layer.extent()
        camera = extent_camera(extent, layer.crs())
        view = self._earth_view() if settings.get("auto_import_layer") else None
        if view is None:
            if camera is not None:
                url = urls.earth_view_url(*camera)
                view = self._open_view()
                if view is not None:
                    self._sent(Camera(*camera, 0.0, 0.0, urls.DEFAULT_FOV, 0.0))
                    self._show_in_view(view, url)
                else:
                    self._open_browser(url)
            self._manual_import_message(path, f"Exported {what} to {path}.")
            return

        if camera is not None:
            self._sent(Camera(*camera, 0.0, 0.0, urls.DEFAULT_FOV, 0.0))
            self._show_in_view(view, urls.earth_view_url(*camera))
        self._update_view_polling()
        self.drop_job = {"view": view, "path": path, "what": what,
                         "started": time.monotonic(), "ready_at": None}
        self._message(f"Sending {what} to Google Earth…", duration=3)
        self.drop_timer.start()

    def _drop_step(self):
        job = self.drop_job
        if job is None:
            self.drop_timer.stop()
            return
        if time.monotonic() - job["started"] > DROP_TIMEOUT_S:
            self.drop_timer.stop()
            self.drop_job = None
            self._manual_import_message(
                job["path"], f"Google Earth didn't finish loading, so {job['what']} wasn't "
                "added automatically.")
            return
        job["view"].query_view(self._drop_check)

    def _drop_check(self, info):
        job = self.drop_job
        if job is None:
            return
        now = time.monotonic()
        ready = (info and info.get("canvas") and info.get("ready") == "complete"
                 and parse_camera(info.get("href")) is not None)
        if not ready or now - job["started"] < DROP_SETTLE_S:
            job["ready_at"] = None
            return
        if job["ready_at"] is None:
            job["ready_at"] = now
            return
        if now - job["ready_at"] < DROP_SETTLE_S:
            return
        self.drop_timer.stop()
        self.drop_job = None
        x = info["left"] + info["width"] / 2.0
        y = info["top"] + info["height"] / 2.0
        if job["view"].drop_file(job["path"], x, y):
            self._manual_import_message(
                job["path"], f"Sent {job['what']} to Google Earth.",
                fallback=" If it doesn't appear, import it by hand: ", level=Qgis.Success)
        else:
            self._manual_import_message(
                job["path"], f"Couldn't reach Google Earth to add {job['what']}.")

    def _manual_import_message(self, path, lead, fallback=" ", level=Qgis.Info):
        QApplication.clipboard().setText(path)
        folder_button = QPushButton("Open folder")
        folder_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
        self._message(
            lead + fallback + "in Google Earth Web choose Projects ▸ New project ▸ "
            "Import KML file from computer (the file path is on the clipboard).",
            level, widget=folder_button, duration=0 if level != Qgis.Success else 15)

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
        pt = QgsPointXY(event.mapPoint())
        add("icon.png", "Open here in Google Earth Web",
            lambda: self.open_point("earth", lat, lon, pt), ok)
        add("location-pin.png", "Open here in Google Maps",
            lambda: self.open_point("maps", lat, lon, pt), ok)
        add("street-view.png", "Open here in Street View",
            lambda: self.open_point("street", lat, lon, pt), ok)
        sub.addSeparator()
        sub.addAction(self.sync_ge)
        if self.targets:
            sub.addAction(self.clear_targets_action)
        if ok:
            sub.addSeparator()
            coords = f"{lat:.6f}, {lon:.6f}"
            copy = sub.addAction(f"Copy {coords}")
            copy.triggered.connect(lambda: QApplication.clipboard().setText(coords))
        sub.addSeparator()
        sub.addAction(self.settings_action)
        menu.addSeparator()
        menu.addMenu(sub)
