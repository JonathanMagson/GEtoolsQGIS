"""SyncToGE without Qt WebEngine: a Chrome/Edge window driven from QGIS.

The window runs on a profile of its own (in the QGIS settings folder), so it
remembers Google's cookie consent between sessions without touching the
user's everyday browser.
"""

import os
import time

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import QgsApplication, QgsSettings

from . import cdp
from .earth_dock import FAST_SYNC_KEY
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
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self._try_connect)

    @property
    def active(self):
        return self.conn is not None or self.poll.isActive()

    def navigate(self, url, bring_to_front=False):
        """Show ``url``, starting (or reattaching to) the browser if needed."""
        self.pending_url = url
        if self.conn is None:
            if not self.poll.isActive():
                self._start(url)
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

    def stop(self):
        """Stop driving the window. The window itself is left open."""
        self.poll.stop()
        self._disconnect()
        self.pending_url = None

    # ---- internals -----------------------------------------------------------

    def _go(self, url, bring_to_front):
        fast = QgsSettings().value(FAST_SYNC_KEY, True, type=bool)
        if fast and self.loaded_earth and "/web/@" in url:
            self.conn.call("Runtime.evaluate", expression=soft_nav_js(url))
        else:
            self.conn.call("Page.navigate", url=url)
            self.loaded_earth = True
        if bring_to_front:
            self.conn.call("Page.bringToFront")
        self.pending_url = None

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
        return True

    def _start(self, url):
        if self._attach():
            self.navigate(url, bring_to_front=True)
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
