"""Dock panel hosting Google Earth Web inside QGIS.

Needs Qt WebEngine (Google Earth Web requires WebGL, which the older QtWebKit
can't provide). When WebEngine isn't available, ``WEBENGINE_AVAILABLE`` is
False and the plugin falls back to the system browser.
"""

import os
import re

from qgis.PyQt.QtCore import QUrl, Qt
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAction,
    QCheckBox,
    QDockWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsApplication

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


# Moves the Google Earth camera without reloading the app: Earth Web is a
# single-page app that follows its own history, so push the new "@..." URL
# and announce it the way the browser's back/forward buttons would.
_SOFT_NAV_JS = """
(function (url) {
  if (location.pathname.indexOf('/web') !== 0) { location.href = url; return; }
  history.pushState(history.state, '', url);
  window.dispatchEvent(new PopStateEvent('popstate', {state: history.state}));
})(%s);
"""


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
        self.soft_nav.setChecked(True)
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
        settings = self.view.settings()
        for name in ("WebGLEnabled", "Accelerated2dCanvasEnabled", "LocalStorageEnabled"):
            settings.setAttribute(_attr(name), True)
        self.view.loadFinished.connect(self._on_load_finished)
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

    def navigate(self, url):
        """Show ``url``; reuses the loaded app when fast sync is on."""
        self._current_url = url
        if self._loaded and self.soft_nav.isChecked() and "/web/@" in url:
            self.view.page().runJavaScript(_SOFT_NAV_JS % _js_string(url))
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


def _js_string(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


DOCK_AREA = Qt.DockWidgetArea.RightDockWidgetArea if hasattr(Qt, "DockWidgetArea") else Qt.RightDockWidgetArea
