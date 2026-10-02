"""Drive a Chrome or Edge window over the Chrome DevTools Protocol.

Used for live SyncToGE when QGIS has no Qt WebEngine (the usual case on
Windows). The browser runs with its own throwaway profile, so it doesn't touch
the user's normal browser, and the debugging port only listens on 127.0.0.1.

Pure Python standard library, no QGIS imports.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import urllib.request

_WINDOWS_CANDIDATES = (
    (r"%ProgramFiles%", r"Google\Chrome\Application\chrome.exe"),
    (r"%ProgramFiles(x86)%", r"Google\Chrome\Application\chrome.exe"),
    (r"%LocalAppData%", r"Google\Chrome\Application\chrome.exe"),
    (r"%ProgramFiles(x86)%", r"Microsoft\Edge\Application\msedge.exe"),
    (r"%ProgramFiles%", r"Microsoft\Edge\Application\msedge.exe"),
)
_MAC_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_LINUX_NAMES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable",
)


class CdpError(Exception):
    pass


def find_browser():
    """Path to Chrome or Edge (or Chromium), or ``None``. Chrome wins over Edge."""
    if sys.platform.startswith("win"):
        for root, rel in _WINDOWS_CANDIDATES:
            base = os.path.expandvars(root)
            if base != root:  # variable was set
                path = os.path.join(base, rel)
                if os.path.isfile(path):
                    return path
        return None
    if sys.platform == "darwin":
        return next((p for p in _MAC_CANDIDATES if os.path.isfile(p)), None)
    return next((shutil.which(n) for n in _LINUX_NAMES if shutil.which(n)), None)


def browser_name(path):
    name = os.path.basename(path or "").lower()
    if "edge" in name:
        return "Microsoft Edge"
    if "chromium" in name:
        return "Chromium"
    return "Google Chrome"


def launch(browser, profile_dir, url, extra_args=()):
    """Start ``browser`` in app mode on ``url`` with remote debugging on.

    Port 0 lets the browser pick a free port; it writes it to
    ``DevToolsActivePort`` in the profile, which ``read_port`` picks up.
    """
    # A stale DevToolsActivePort from an earlier run is harmless: its port
    # just won't answer, and the browser overwrites it once it's up.
    os.makedirs(profile_dir, exist_ok=True)
    args = [
        browser,
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--app={url}",
        *extra_args,
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console on Windows
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)


def read_port(profile_dir):
    """The debugging port of a browser running on ``profile_dir``, or ``None``."""
    try:
        with open(os.path.join(profile_dir, "DevToolsActivePort")) as f:
            return int(f.readline().strip())
    except (OSError, ValueError):
        return None


def _http_json(port, path, method="GET", timeout=2.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    # Skip any system proxy: this is always a loopback call.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_page(port, url_hint="earth.google.com"):
    """WebSocket URL of the page to drive: the Earth tab if there is one,
    else any normal page. ``None`` if the browser has no pages open."""
    try:
        targets = _http_json(port, "/json/list")
    except (OSError, ValueError):
        return None
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    for t in pages:
        if url_hint in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    return pages[0]["webSocketDebuggerUrl"] if pages else None


class CdpConnection:
    """Minimal WebSocket client, just enough for CDP request/response."""

    def __init__(self, ws_url, timeout=3.0):
        if not ws_url.startswith("ws://"):
            raise CdpError(f"unsupported DevTools URL {ws_url}")
        hostport, _, path = ws_url[len("ws://"):].partition("/")
        host, _, port = hostport.partition(":")
        self._next_id = 0
        try:
            self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
            key = base64.b64encode(os.urandom(16)).decode()
            # No Origin header: Chrome only enforces --remote-allow-origins
            # on requests that send one.
            self.sock.sendall((
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise CdpError("DevTools closed the connection during handshake")
                response += chunk
        except OSError as exc:
            raise CdpError(str(exc)) from exc
        head, _, self._buffer = response.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CdpError("DevTools refused the connection: " + head.split(b"\r\n", 1)[0].decode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, method, **params):
        """Send one CDP command and return its ``result`` (events are skipped)."""
        self._next_id += 1
        msg_id = self._next_id
        try:
            self._send(json.dumps({"id": msg_id, "method": method, "params": params}))
            while True:
                reply = json.loads(self._recv())
                if reply.get("id") == msg_id:
                    break
        except (OSError, ValueError) as exc:
            raise CdpError(str(exc)) from exc
        if "error" in reply:
            raise CdpError(reply["error"].get("message", str(reply["error"])))
        return reply.get("result", {})

    def _send(self, text):
        data = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text frame
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)  # clients must mask every frame
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + mask + masked)

    def _read_exact(self, n):
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("DevTools connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def _recv(self):
        message = b""
        while True:
            b0, b1 = self._read_exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(n)  # server frames are never masked
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise CdpError("DevTools connection closed")
            if opcode in (0x9, 0xA):  # ping/pong: ignore
                continue
            message += payload
            if b0 & 0x80:
                return message.decode("utf-8")
