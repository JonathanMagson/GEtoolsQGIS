"""Dock panel hosting Google Earth Web inside QGIS.

Needs Qt WebEngine (Google Earth Web requires WebGL, which the older QtWebKit
can't provide). When WebEngine isn't available, ``WEBENGINE_AVAILABLE`` is
False and the plugin falls back to the system browser.
"""

import json
import os
import re

from qgis.PyQt.QtCore import QMimeData, QPoint, QPointF, QUrl, Qt
from qgis.PyQt.QtGui import QDesktopServices, QDragEnterEvent, QDragMoveEvent, QDropEvent
from qgis.PyQt.QtWidgets import (
    QAction,
    QCheckBox,
    QApplication,
    QDockWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsApplication

from . import settings
from .earth_marker import CLEAR_MARKER_JS, VIEW_INFO_JS
from .google_urls import soft_nav_js

# qgis.PyQt has no WebEngine wrapper, so import whichever PyQt QGIS runs on.
# QGIS sets Qt.AA_ShareOpenGLContexts at startup, which is what lets
# WebEngine be imported after the application already exists.
try:
    from qgis.PyQt.QtCore import QT_VERSION_STR

    if QT_VERSION_STR.startswith("5."):
        from PyQt5.QtWebEngineWidgets import (
            QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineView)
    else:
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_AVAILABLE = True
except (ImportError, RuntimeError):  # not installed, or loaded too late
    WEBENGINE_AVAILABLE = False


def _attr(name):
    """QWebEngineSettings attribute, scoped (Qt6) or unscoped (Qt5)."""
    scoped = getattr(QWebEngineSettings, "WebAttribute", None)
    return getattr(scoped, name) if scoped else getattr(QWebEngineSettings, name)



if WEBENGINE_AVAILABLE:

    class _Page(QWebEnginePage):
        """Open links Earth Web tries to pop out (help, sign-in) in the browser."""

        def createWindow(self, _type):
            page = QWebEnginePage(self.profile(), self)
            page.urlChanged.connect(lambda url: (QDesktopServices.openUrl(url), page.deleteLater()))
            return page


class EarthDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Google Earth Web", parent)
        self.setObjectName("GEtoolsEarthDock")
        self._current_url = None
        self._loaded = False

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QToolBar(body)
        self.soft_nav = QCheckBox("Fast sync (no reload)", toolbar)
        self.soft_nav.setChecked(settings.get("fast_sync"))
        self.soft_nav.toggled.connect(lambda on: settings.set("fast_sync", on))
        self.soft_nav.setToolTip(
            "Move the Google Earth camera without reloading the page.\n"
            "Untick this if Google Earth stops following the QGIS map."
        )
        toolbar.addWidget(self.soft_nav)
        reload_action = QAction(QgsApplication.getThemeIcon("/mActionRefresh.svg"), "Reload", toolbar)
        reload_action.triggered.connect(self.reload)
        toolbar.addAction(reload_action)
        browser_action = QAction("Open in browser", toolbar)
        browser_action.triggered.connect(self.open_in_browser)
        toolbar.addAction(browser_action)
        layout.addWidget(toolbar)

        self.view = QWebEngineView(body)
        self.view.setPage(_Page(self._profile(), self.view))
        web_settings = self.view.settings()
        for name in ("WebGLEnabled", "Accelerated2dCanvasEnabled", "LocalStorageEnabled"):
            web_settings.setAttribute(_attr(name), True)
        self.view.loadFinished.connect(self._on_load_finished)
        self._marker_js = None
        layout.addWidget(self.view)

        self.setWidget(body)

    def _profile(self):
        # A persistent profile keeps Google's cookie-consent choice and any
        # sign-in between sessions, so Earth Web doesn't ask every time.
        profile = QWebEngineProfile("GEtools", self)
        store = os.path.join(QgsApplication.qgisSettingsDirPath(), "getools_webengine")
        profile.setPersistentStoragePath(store)
        profile.setCachePath(os.path.join(store, "cache"))
        # Earth Web rejects unknown browsers; drop the QtWebEngine token so it
        # sees the Chromium the engine actually is.
        ua = re.sub(r"\s*QtWebEngine/\S+", "", profile.httpUserAgent())
        profile.setHttpUserAgent(ua)
        return profile

    def _on_load_finished(self, ok):
        self._loaded = ok
        if self._marker_js:  # a full load wiped the target X; redraw it
            self.view.page().runJavaScript(self._marker_js)

    def set_markers(self, js):
        """Draw (``js``) or clear (``None``) the target Xs over Earth Web."""
        self._marker_js = js
        self.view.page().runJavaScript(js or CLEAR_MARKER_JS)

    def is_open(self):
        return self.isVisible()

    def query_view(self, callback):
        """Call ``callback`` with the page's address and globe canvas (a
        dict, see earth_marker.VIEW_INFO_JS), or ``None`` if unavailable."""
        def done(result):
            try:
                callback(json.loads(result) if result else None)
            except ValueError:
                callback(None)
        self.view.page().runJavaScript(VIEW_INFO_JS, done)

    def drop_file(self, path, x, y):
        """Drop a local file on the page at ``x, y`` (CSS pixels), as if
        dragged from the file manager."""
        target = self.view.focusProxy() or self.view
        zoom = self.view.zoomFactor() or 1.0
        pos = QPoint(int(x * zoom), int(y * zoom))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        copy = Qt.DropAction.CopyAction if hasattr(Qt, "DropAction") else Qt.CopyAction
        no_buttons = Qt.MouseButton.NoButton if hasattr(Qt, "MouseButton") else Qt.NoButton
        no_mods = (Qt.KeyboardModifier.NoModifier if hasattr(Qt, "KeyboardModifier")
                   else Qt.NoModifier)
        for event in (QDragEnterEvent(pos, copy, mime, no_buttons, no_mods),
                      QDragMoveEvent(pos, copy, mime, no_buttons, no_mods),
                      QDropEvent(QPointF(pos), copy, mime, no_buttons, no_mods)):
            QApplication.sendEvent(target, event)
        return True

    def navigate(self, url, bring_to_front=False):
        """Show ``url``; reuses the loaded app when fast sync is on."""
        self._current_url = url
        if self._loaded and self.soft_nav.isChecked() and "/web/@" in url:
            self.view.page().runJavaScript(soft_nav_js(url))
        else:
            self._loaded = False
            self.view.setUrl(QUrl(url))

    def reload(self):
        if self._current_url:
            self._loaded = False
            self.view.setUrl(QUrl(self._current_url))

    def open_in_browser(self):
        url = self.view.url().toString() or self._current_url
        if url:
            QDesktopServices.openUrl(QUrl(url))


DOCK_AREA = Qt.DockWidgetArea.RightDockWidgetArea if hasattr(Qt, "DockWidgetArea") else Qt.RightDockWidgetArea
