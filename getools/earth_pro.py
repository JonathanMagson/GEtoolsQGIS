"""Google Earth Pro (desktop) driven from QGIS through a live KML link.

QGIS runs a tiny HTTP server on 127.0.0.1 and Google Earth Pro polls it
through the network links in ``earth_pro_kml.master_kml``. Earth polling
every 0.3 s is also how the plugin knows Earth Pro is open.

The server only answers URLs carrying a random token, so other users of a
shared machine can't read or steer the link.

Exposes the same methods as the Google Earth Web views (navigate,
set_targets, query_view, is_open), so the plugin treats them alike.
"""

import os
import secrets
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

from qgis.PyQt.QtCore import (
    QBuffer, QByteArray, QIODevice, QObject, QPointF, QTimer, QUrl, Qt, pyqtSignal)
from qgis.PyQt.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer

from . import earth_pro_kml as kml
from .camera import parse_camera
from .google_urls import earth_view_url
from .layer_export import kml_folder

_WINDOWS_PATHS = (
    (r"%ProgramFiles%", r"Google\Google Earth Pro\client\googleearth.exe"),
    (r"%ProgramFiles(x86)%", r"Google\Google Earth Pro\client\googleearth.exe"),
    (r"%LocalAppData%", r"Google\Google Earth Pro\client\googleearth.exe"),
)
_MAC_APP = "/Applications/Google Earth Pro.app"
_LINUX_NAMES = ("google-earth-pro", "googleearth")

POLL_TIMEOUT_S = 4.0     # no poll for this long: Earth Pro is closed
LAUNCH_GRACE_S = 30.0    # how long a launch may take before we try again


def find_earth_pro(custom_path=""):
    """Google Earth Pro's program, or ``None``. ``custom_path`` wins if valid."""
    if custom_path and os.path.exists(custom_path):
        return custom_path
    if sys.platform.startswith("win"):
        for root, rel in _WINDOWS_PATHS:
            base = os.path.expandvars(root)
            if base != root and os.path.isfile(os.path.join(base, rel)):
                return os.path.join(base, rel)
        return None
    if sys.platform == "darwin":
        return _MAC_APP if os.path.isdir(_MAC_APP) else None
    for name in _LINUX_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return "/opt/google/earth/pro/googleearth" if os.path.isfile(
        "/opt/google/earth/pro/googleearth") else None


def open_in_earth_pro(program, path):
    """Open a KML file in Google Earth Pro (a running Earth takes it over).

    Without a known program, falls back to whatever opens .kml files.
    """
    try:
        if program and sys.platform == "darwin":
            subprocess.Popen(["open", "-a", program, path])
            return True
        if program:
            subprocess.Popen([program, path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
    except OSError:
        pass
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def raise_earth_pro():
    """Bring Google Earth Pro's window forward, restoring it if minimized.

    Best effort: Windows via the window list, macOS via AppleScript, Linux
    via wmctrl when installed.
    """
    try:
        if sys.platform.startswith("win"):
            _raise_windows("Google Earth Pro")
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", 'tell application "Google Earth Pro" to activate'])
        elif shutil.which("wmctrl"):
            subprocess.Popen(["wmctrl", "-a", "Google Earth"])
    except OSError:
        pass


def _raise_windows(title_part):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_part in buf.value:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    for hwnd in found[:1]:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def _target_icon_png():
    """A white X with a dark outline; Earth tints the white with the
    target colour (IconStyle color multiplies)."""
    img = QImage(64, 64, QImage.Format.Format_ARGB32 if hasattr(QImage, "Format")
                 else QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing if hasattr(QPainter, "RenderHint")
                          else QPainter.Antialiasing)
    for colour, width in ((QColor(40, 40, 40), 16), (QColor(255, 255, 255), 9)):
        pen = QPen(colour, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if hasattr(Qt, "PenCapStyle") else Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(12, 12), QPointF(52, 52))
        painter.drawLine(QPointF(52, 12), QPointF(12, 52))
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly if hasattr(QIODevice, "OpenModeFlag")
             else QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


class EarthProLink(QObject):
    """Google Earth Pro, kept in step with QGIS over a local KML link."""

    #: Earth Pro stopped polling (closed, or the link was removed).
    closed = pyqtSignal(str)

    def __init__(self, program, parent=None):
        super().__init__(parent)
        self.program = program
        self.name = "Google Earth Pro"
        self.token = secrets.token_urlsafe(16)
        self.pending = None           # Camera to fly to on the next poll
        self.targets = []
        self.style = ("#e60000", 30)
        self.reported = None          # (Camera, aspect) Earth last reported
        self.last_poll = 0.0
        self.launched_at = None
        self.was_open = False
        self._icon = None
        self._buffers = {}

        self.server = QTcpServer(self)
        if not self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost
                                               if hasattr(QHostAddress, "SpecialAddress")
                                               else QHostAddress.LocalHost), 0):
            raise OSError(self.server.errorString())
        self.server.newConnection.connect(self._accept)

        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self._check_alive)
        self.watchdog.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.serverPort()}/{self.token}"

    # ---- the same interface as the Google Earth Web views -----------------

    @property
    def active(self):
        return self.is_open()

    def is_open(self):
        return time.monotonic() - self.last_poll < POLL_TIMEOUT_S

    def ensure_open(self, bring_to_front=False):
        """Open the link in Google Earth Pro unless Earth is already polling
        it (or was just asked to and is still starting)."""
        if self.is_open():
            if bring_to_front:
                raise_earth_pro()
            return
        if self.launched_at and time.monotonic() - self.launched_at < LAUNCH_GRACE_S:
            return
        path = os.path.join(kml_folder(), "GEtools_link.kml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(kml.master_kml(self.base_url))
        self.launched_at = time.monotonic()
        if not open_in_earth_pro(self.program, path):
            self.launched_at = None
            self.closed.emit("Couldn't start Google Earth Pro.")

    def navigate(self, url, bring_to_front=False, reload=False):
        """Fly Earth to the camera in an Earth Web style ``url``."""
        cam = parse_camera(url)
        if cam is None:
            return
        self.pending = cam
        self.ensure_open(bring_to_front)

    def set_targets(self, targets, color, size):
        self.targets = list(targets)
        self.style = (color, size)

    def query_view(self, callback):
        """Same shape as the web views: Earth's camera as an Earth Web URL
        plus the view's aspect, from Earth's last camera report."""
        if not self.is_open() or self.reported is None:
            callback(None)
            return
        cam, aspect = self.reported
        callback({"href": earth_view_url(cam.lat, cam.lon, cam.range, heading=cam.heading,
                                         tilt=cam.tilt, fov=cam.fov),
                  "left": 0, "top": 0, "width": 1000 * aspect, "height": 1000,
                  "canvas": True, "ready": "complete"})

    def open_kml(self, path, bring_to_front=False):
        ok = open_in_earth_pro(self.program, path)
        if ok and bring_to_front and self.is_open():
            raise_earth_pro()
        return ok

    def stop(self):
        self.watchdog.stop()
        self.server.close()

    # ---- HTTP --------------------------------------------------------------

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _drop(self, sock):
        self._buffers.pop(sock, None)
        sock.deleteLater()

    def _read(self, sock):
        if sock not in self._buffers:
            return
        self._buffers[sock] += bytes(sock.readAll())
        data = self._buffers[sock]
        if b"\r\n\r\n" not in data:
            if len(data) > 16384:
                sock.abort()
            return
        line = data.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split(" ")
        status, ctype, body = "404 Not Found", "text/plain", b"not found"
        if len(parts) >= 2 and parts[0] == "GET":
            status, ctype, body = self._route(urlsplit(parts[1]))
        head = (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-cache, no-store\r\n"
                "Connection: close\r\n\r\n").encode("latin-1")
        sock.write(head + body)
        sock.flush()
        sock.disconnectFromHost()

    def _route(self, url):
        prefix = f"/{self.token}/"
        if not url.path.startswith(prefix):
            return "404 Not Found", "text/plain", b"not found"
        name = url.path[len(prefix):]
        kml_type = "application/vnd.google-earth.kml+xml"
        if name == "view.kml":
            self.last_poll = time.monotonic()
            self.was_open = True
            self.launched_at = None
            cam, self.pending = self.pending, None
            return "200 OK", kml_type, (kml.lookat_kml(cam) if cam else kml.EMPTY_KML).encode()
        if name == "camera":
            report = kml.parse_camera_report(url.query)
            if report is not None:
                self.reported = report
            return "200 OK", kml_type, kml.EMPTY_KML.encode()
        if name == "targets.kml":
            color, size = self.style
            body = kml.targets_kml(self.targets, self.base_url + "/x.png", color, size)
            return "200 OK", kml_type, body.encode()
        if name == "x.png":
            if self._icon is None:
                self._icon = _target_icon_png()
            return "200 OK", "image/png", self._icon
        return "404 Not Found", "text/plain", b"not found"

    def _check_alive(self):
        if self.was_open and not self.is_open():
            self.was_open = False
            self.reported = None
            self.closed.emit("Google Earth Pro stopped responding (closed, or the GEtools "
                             "link was removed from its Places).")
