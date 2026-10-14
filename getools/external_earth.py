"""SyncToGE without Qt WebEngine: a Chrome/Edge window driven from QGIS.

The window runs on a profile of its own (in the QGIS settings folder), so it
remembers Google's cookie consent between sessions without touching the
user's everyday browser.
"""

import ctypes
import json
import os
import sys
import time

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import QgsApplication

from . import cdp, settings
from .earth_marker import CLEAR_MARKER_JS, VIEW_INFO_JS
from .google_urls import soft_nav_js

STARTUP_TIMEOUT_S = 20.0
POLL_MS = 250


class ExternalEarthWindow(QObject):
    """Google Earth Web in a separate browser window that follows QGIS."""

    #: The window went away (user closed it, or the browser couldn't start).
    closed = pyqtSignal(str)

    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.name = cdp.browser_name(browser)
        self.profile_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "getools_browser")
        self.conn = None
        self.pending_url = None
        self.process = None
        self.started_at = None
        self.launched_url = None
        self.loaded_earth = False
        self.marker_js = None  # script drawing the target X, if one is set
        self.marker_script_id = None
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self._try_connect)

    @property
    def active(self):
        return self.conn is not None or self.poll.isActive()

    def is_open(self):
        """Connected to a live Earth window."""
        return self.conn is not None

    def navigate(self, url, bring_to_front=False):
        """Show ``url``, starting (or reattaching to) the browser if needed."""
        self.pending_url = url
        if self.conn is None:
            if not self.poll.isActive():
                self._start(url, bring_to_front)
            return
        try:
            self._go(url, bring_to_front)
        except cdp.CdpError:
            # The tab may have been replaced (e.g. a reload); look again.
            self._disconnect()
            if not self._attach():
                self.closed.emit(f"The {self.name} window for Google Earth was closed.")
                return
            try:
                self._go(url, bring_to_front)
            except cdp.CdpError as exc:
                self._disconnect()
                self.closed.emit(f"Lost contact with {self.name}: {exc}")

    def set_markers(self, js):
        """Draw (``js``) or clear (``None``) the target Xs in the Earth window.

        The script is also registered to run on every page load, so the X
        survives full reloads of Earth Web.
        """
        self.marker_js = js
        if self.conn is None:
            return  # applied by _attach once connected
        try:
            self._install_marker()
        except cdp.CdpError:
            self._disconnect()

    def query_view(self, callback):
        """Call ``callback`` with the page's address and globe canvas (a
        dict, see earth_marker.VIEW_INFO_JS), or ``None`` if unavailable."""
        info = None
        if self.conn is not None:
            try:
                result = self.conn.call("Runtime.evaluate", expression=VIEW_INFO_JS,
                                        returnByValue=True)
                info = json.loads(result.get("result", {}).get("value") or "null")
            except (cdp.CdpError, ValueError):
                self._disconnect()
        callback(info)

    def drop_file(self, path, x, y):
        """Drop a local file on the page at ``x, y``, as if dragged from the
        file manager. Returns False if the window couldn't be reached."""
        if self.conn is None:
            return False
        try:
            for method, params in cdp.drop_file_events(path, x, y):
                self.conn.call(method, **params)
        except cdp.CdpError:
            self._disconnect()
            return False
        return True

    def stop(self):
        """Stop driving the window. The window itself is left open."""
        self.poll.stop()
        self._disconnect()
        self.pending_url = None

    # ---- internals -----------------------------------------------------------

    def _go(self, url, bring_to_front):
        fast = settings.get("fast_sync")
        if fast and self.loaded_earth and "/web/@" in url:
            self.conn.call("Runtime.evaluate", expression=soft_nav_js(url))
        else:
            self.conn.call("Page.navigate", url=url)
            self.loaded_earth = True
        if bring_to_front:
            self._raise_window()
        self.pending_url = None

    def _install_marker(self):
        if self.marker_script_id is not None:
            self.conn.call("Page.removeScriptToEvaluateOnNewDocument",
                           identifier=self.marker_script_id)
            self.marker_script_id = None
        if self.marker_js:
            # Load-time scripts only run while the Page domain is enabled.
            self.conn.call("Page.enable")
            result = self.conn.call("Page.addScriptToEvaluateOnNewDocument", source=self.marker_js)
            self.marker_script_id = result.get("identifier")
        self.conn.call("Runtime.evaluate", expression=self.marker_js or CLEAR_MARKER_JS)

    def _raise_window(self):
        """Restore the Earth window if minimized and put it in front.

        Page.bringToFront alone only picks the tab; it leaves a minimized
        window minimized.
        """
        if sys.platform.startswith("win"):
            # Windows only lets the foreground app (QGIS, which was just
            # clicked) hand focus to another process; grant it.
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            except (AttributeError, OSError):
                pass
        try:
            window = self.conn.call("Browser.getWindowForTarget")
            if window.get("bounds", {}).get("windowState") == "minimized":
                self.conn.call("Browser.setWindowBounds", windowId=window["windowId"],
                               bounds={"windowState": "normal"})
        except cdp.CdpError:
            pass  # browser without the Browser domain: just pick the tab
        self.conn.call("Page.bringToFront")

    def _attach(self):
        """Connect to a browser already running on our profile, if any."""
        port = cdp.read_port(self.profile_dir)
        if port is None:
            return False
        ws_url = cdp.find_page(port)
        if ws_url is None:
            return False
        try:
            self.conn = cdp.CdpConnection(ws_url)
        except cdp.CdpError:
            self.conn = None
            return False
        # Whatever is showing may not be Earth Web yet; the first move is a
        # real navigation, then fast sync takes over.
        self.loaded_earth = False
        # Scripts registered on a previous connection died with it.
        self.marker_script_id = None
        if self.marker_js:
            try:
                self._install_marker()
            except cdp.CdpError:
                self._disconnect()
                return False
        return True

    def _start(self, url, bring_to_front):
        if self._attach():
            self.navigate(url, bring_to_front)
            return
        try:
            self.process = cdp.launch(self.browser, self.profile_dir, url)
        except OSError as exc:
            self.closed.emit(f"Couldn't start {self.name}: {exc}")
            return
        self.started_at = time.monotonic()
        self.launched_url = url
        self.poll.start()

    def _try_connect(self):
        if self._attach():
            self.poll.stop()
            # The window opened on the URL it was launched with, so it's
            # already on Earth Web; only move it if QGIS has moved since.
            self.loaded_earth = True
            url, self.pending_url = self.pending_url, None
            if url and url != self.launched_url:
                self.navigate(url)
            return
        exited = self.process is not None and self.process.poll() is not None
        timed_out = time.monotonic() - self.started_at > STARTUP_TIMEOUT_S
        if timed_out or (exited and cdp.read_port(self.profile_dir) is None and
                         time.monotonic() - self.started_at > 5):
            self.poll.stop()
            self.closed.emit(f"{self.name} didn't start in time for live sync.")

    def _disconnect(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
