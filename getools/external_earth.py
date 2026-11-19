"""SyncToGE without Qt WebEngine: a Chrome/Edge window driven from QGIS.

The window runs on a profile of its own (in the QGIS settings folder, or the
folder chosen in Settings), so it remembers Google's cookie consent between
sessions without touching the user's everyday browser.

On Linux and macOS the window is driven over a private pipe by default (see
cdp.py); it then closes with QGIS, since nothing else can drive it. With the
local port (always on Windows) it stays open, and QGIS reattaches to it.
"""

import ctypes
import json
import os
import sys
import time

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from . import cdp, chromium_download, settings
from .earth_marker import CLEAR_MARKER_JS, VIEW_INFO_JS, marker_js
from .google_urls import soft_nav_js

STARTUP_TIMEOUT_S = cdp.STARTUP_TIMEOUT_S
POLL_MS = 250
DRAIN_MS = 1000           # pipe: read events, and notice the window closing
SOFT_NAV_CHECK_MS = 2500  # time Earth Web gets to react to a fast-sync move
HEALTH_CHECK_MS = 4000    # after the window opens: did Earth load, is there WebGL?


def profile_dir():
    """The browser profile folder for the Google Earth window."""
    return os.path.join(settings.browser_data_dir(), "getools_browser")


def find_earth_browser(choice, custom_path, data_dir=None):
    """The browser for the Google Earth window: the one chosen in Settings;
    with "auto", Chrome, else Edge, else a Chromium downloaded from Settings."""
    found = cdp.find_browser(choice, custom_path)
    if found is None and choice == "auto":
        found = chromium_download.find_installed(data_dir or settings.browser_data_dir())
    return found


def launch_options():
    """(connection, no_sandbox, software_gl) for a launch, from the settings."""
    connection = cdp.resolve_connection(settings.get("connection"))
    software_gl = cdp.resolve_software_gl(settings.get("software_webgl"))
    return connection, settings.get("no_sandbox"), software_gl


class ExternalEarthWindow(QObject):
    """Google Earth Web in a separate browser window that follows QGIS."""

    #: The window went away (user closed it, or the browser couldn't start).
    closed = pyqtSignal(str)
    #: Something worth telling the user, e.g. fast sync turned itself off.
    notice = pyqtSignal(str)
    #: A problem the user should fix: the browser didn't start, Earth didn't
    #: load, or there's no WebGL.
    problem = pyqtSignal(str)

    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.name = cdp.browser_name(browser)
        self.profile_dir = profile_dir()
        self.connection = cdp.resolve_connection(settings.get("connection"))
        self.no_sandbox = False
        self.software_gl = False
        self.pipe = None  # cdp.PipeBrowser, when driven over the private pipe
        self.conn = None
        self.pending_url = None
        self.process = None
        self.started_at = None
        self.launched_url = None
        self.loaded_earth = False
        self.marker_js = None  # script drawing the target X, if one is set
        self.marker_script_id = None
        # Fast sync is only trusted once Earth Web is seen to follow it.
        self.soft_nav_works = None  # None: not known yet
        self.soft_nav_check = None
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self._try_connect)
        self.drain_timer = QTimer(self)
        self.drain_timer.setInterval(DRAIN_MS)
        self.drain_timer.timeout.connect(self._drain)

    @property
    def active(self):
        return self.conn is not None or self.poll.isActive()

    def is_open(self):
        """Connected to a live Earth window."""
        return self.conn is not None

    def navigate(self, url, bring_to_front=False, reload=False):
        """Show ``url``, starting (or reattaching to) the browser if needed.
        ``reload`` forces a full page load instead of a fast-sync move."""
        self.pending_url = url
        if self.conn is None:
            if not self.poll.isActive():
                self._start(url, bring_to_front)
            return
        try:
            self._go(url, bring_to_front, reload)
        except cdp.CdpError:
            # The tab may have been replaced (e.g. a reload); look again.
            self._disconnect()
            if not self._attach():
                self.closed.emit(f"The {self.name} window for Google Earth was closed.")
                return
            try:
                self._go(url, bring_to_front, reload)
            except cdp.CdpError as exc:
                self._disconnect()
                self.closed.emit(f"Lost contact with {self.name}: {exc}")

    def set_targets(self, targets, color, size):
        """Show an X at each ``(lat, lon)`` (none clears them)."""
        self.set_markers(marker_js(targets, color, size) if targets else None)

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
        """Stop driving the window. With the local port the window is left
        open (QGIS can reattach to it); with the private pipe it closes,
        since nothing could drive it any more."""
        self.poll.stop()
        self.drain_timer.stop()
        self._disconnect()
        self.pending_url = None
        if self.pipe is not None:
            self.pipe.close_browser()
            self.pipe = None

    # ---- internals -----------------------------------------------------------

    def _go(self, url, bring_to_front, reload=False):
        fast = (settings.get("fast_sync") and self.soft_nav_works is not False
                and not reload)
        if fast and self.loaded_earth and "/web/@" in url:
            self.conn.call("Runtime.evaluate", expression=soft_nav_js(url))
            if self.soft_nav_works is None:
                self.soft_nav_check = url
                QTimer.singleShot(SOFT_NAV_CHECK_MS, self._check_soft_nav)
        else:
            result = self.conn.call("Page.navigate", url=url)
            self.loaded_earth = True
            if result.get("errorText"):
                self.problem.emit(self._load_failed_text(result["errorText"]))
        if bring_to_front:
            self._raise_window()
        self.pending_url = None

    def _check_soft_nav(self):
        """Earth Web rewrites its address in its own format once it has moved.
        If it still shows exactly the address we pushed, it ignored the move:
        reload there, and stop using fast sync for this session."""
        url, self.soft_nav_check = self.soft_nav_check, None
        if url is None or self.conn is None:
            return
        try:
            href = self.conn.call("Runtime.evaluate", expression="location.href",
                                  returnByValue=True).get("result", {}).get("value")
            if href == url:
                self.soft_nav_works = False
                self.conn.call("Page.navigate", url=url)
                self.notice.emit("Google Earth didn't follow fast sync, so it now reloads at "
                                 "each move (slower, but it follows).")
            else:
                self.soft_nav_works = True
        except cdp.CdpError:
            self._disconnect()

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
        """Connect to the page of our browser, if it's up."""
        if self.connection == "pipe":
            if self.pipe is None:
                return False
            try:
                if not self.pipe.ready():
                    return False
                self.conn = self.pipe.attach_page(
                    new_url=self.pending_url or self.launched_url)
            except cdp.CdpError:
                self.conn = None
            if self.conn is None:
                return False
        else:
            # A browser already running on our profile, if any.
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
        if self.connection == "pipe":
            self.drain_timer.start()
        return True

    def _start(self, url, bring_to_front):
        self.connection, self.no_sandbox, self.software_gl = launch_options()
        if self.connection == "port" and self._attach():
            self.navigate(url, bring_to_front)
            return
        args = cdp.browser_args(self.profile_dir, url, self.connection,
                                no_sandbox=self.no_sandbox, software_gl=self.software_gl)
        try:
            if self.connection == "pipe":
                if self.pipe is not None:
                    self.pipe.close_browser()
                self.pipe = cdp.launch_pipe(self.browser, self.profile_dir, args)
                self.process = self.pipe.process
            else:
                self.process = cdp.launch(self.browser, self.profile_dir, url, args)
        except OSError as exc:
            self.pipe = None
            self.problem.emit(f"Couldn't start {self.name} ({self.browser}): {exc}")
            self.closed.emit(f"Couldn't start {self.name}.")
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
            QTimer.singleShot(HEALTH_CHECK_MS, self._health_check)
            url, self.pending_url = self.pending_url, None
            if url and url != self.launched_url:
                self.navigate(url)
            return
        exited = self.process is not None and self.process.poll() is not None
        timed_out = time.monotonic() - self.started_at > STARTUP_TIMEOUT_S
        if self.connection == "pipe":
            # Nothing can take over a pipe browser: if it's gone, it failed.
            failed = timed_out or exited or self.pipe is None or self.pipe.closed
        else:
            failed = timed_out or (exited and cdp.read_port(self.profile_dir) is None and
                                   time.monotonic() - self.started_at > 5)
        if failed:
            self.poll.stop()
            if self.pipe is not None:
                self.pipe.close_browser(wait=1.0)
                self.pipe = None
            code = self.process.poll() if self.process is not None else None
            self.problem.emit(cdp.start_failure_message(self.name, code, self.profile_dir,
                                                        self.no_sandbox))
            self.closed.emit(f"{self.name} didn't start.")

    def _drain(self):
        """Pipe only: keep the pipe empty, and notice when the browser quits
        (e.g. the user closed the Earth window)."""
        if self.pipe is None:
            self.drain_timer.stop()
            return
        try:
            self.pipe.drain()
        except cdp.CdpError:
            self.drain_timer.stop()
            self.conn = None
            self.pipe.close()
            self.pipe = None
            self.closed.emit(f"The {self.name} window for Google Earth was closed.")

    def _health_check(self):
        """Once per window: tell the user if Google Earth couldn't load
        (no internet, proxy) or the browser has no WebGL to draw it with."""
        if self.conn is None:
            return
        try:
            href = self.conn.call("Runtime.evaluate", expression="location.href",
                                  returnByValue=True).get("result", {}).get("value") or ""
            webgl = self.conn.call("Runtime.evaluate", expression=cdp.WEBGL_JS,
                                   returnByValue=True).get("result", {}).get("value")
        except cdp.CdpError:
            return
        if href.startswith("chrome-error:"):
            self.problem.emit(self._load_failed_text())
        elif webgl is None:
            if self.software_gl:
                self.problem.emit(
                    f"{self.name} has no WebGL even with software WebGL, so Google Earth "
                    f"can't draw the globe. Browser log: "
                    f"{os.path.join(self.profile_dir, cdp.LOG_NAME)}")
            else:
                self.problem.emit(
                    f"{self.name} has no WebGL here (no usable GPU, or a remote/X11 "
                    "display), so Google Earth can't draw the globe. In GEtools Settings, "
                    "set Software WebGL to On.")

    def _load_failed_text(self, error=""):
        detail = f" ({error})" if error else ""
        return (f"Google Earth didn't load{detail}. This computer may have no internet "
                "access, or may need a proxy: set https_proxy (e.g. export "
                "https_proxy=http://proxy.example:8080) before starting QGIS, or run QGIS "
                "where there is internet access.")

    def _disconnect(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
