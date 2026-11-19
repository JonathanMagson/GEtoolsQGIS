"""Drive a Chrome or Edge window over the Chrome DevTools Protocol.

Used for live SyncToGE when QGIS has no Qt WebEngine (the usual case on
Windows). The browser runs with a profile of its own, so it doesn't touch the
user's normal browser.

Two ways to talk to it:

* **Private pipe** (``--remote-debugging-pipe``, Linux and macOS): the browser
  reads commands on file descriptor 3 and answers on 4. Only QGIS holds the
  other ends, so nobody else on a shared machine can drive the window.
* **Local port** (``--remote-debugging-port``, the default on Windows): the
  browser listens on 127.0.0.1. Any local program can connect to it, which is
  fine on a personal computer.

Pure Python standard library, no QGIS imports.
"""

import base64
import json
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request

# Where each browser lives, per platform. Order within a list is preference.
_WINDOWS_CANDIDATES = {
    "chrome": (
        (r"%ProgramFiles%", r"Google\Chrome\Application\chrome.exe"),
        (r"%ProgramFiles(x86)%", r"Google\Chrome\Application\chrome.exe"),
        (r"%LocalAppData%", r"Google\Chrome\Application\chrome.exe"),
    ),
    "edge": (
        (r"%ProgramFiles(x86)%", r"Microsoft\Edge\Application\msedge.exe"),
        (r"%ProgramFiles%", r"Microsoft\Edge\Application\msedge.exe"),
    ),
}
_MAC_CANDIDATES = {
    "chrome": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium"),
    "edge": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
}
_LINUX_NAMES = {
    "chrome": ("google-chrome", "google-chrome-stable", "google-chrome-beta",
               "chromium", "chromium-browser", "chrome"),
    "edge": ("microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta"),
}
# Linux installs that aren't always on PATH (e.g. when QGIS is started from a
# module or a desktop launcher with a minimal PATH).
_LINUX_PATHS = {
    "chrome": ("/opt/google/chrome/chrome", "/usr/lib/chromium/chromium",
               "/usr/lib64/chromium/chromium", "/usr/lib64/chromium-browser/chromium-browser",
               "/snap/bin/chromium"),
    "edge": ("/opt/microsoft/msedge/msedge",),
}

#: Choices for the browser setting. "auto" prefers Chrome, then Edge.
BROWSER_CHOICES = ("auto", "chrome", "edge", "custom")
#: Choices for the connection setting; "auto" is the pipe except on Windows.
CONNECTION_CHOICES = ("auto", "pipe", "port")
#: Choices for the software WebGL setting.
SOFTWARE_GL_CHOICES = ("auto", "on", "off")

STARTUP_TIMEOUT_S = 20.0

#: Page script: the WebGL renderer's name, "yes" if unnamed, null if none.
WEBGL_JS = """(() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return null;
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  return info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : 'yes';
})()"""
LOG_NAME = "getools_browser.log"  # the browser's own messages, per launch
CACHE_BYTES = 256 * 1024 * 1024   # cap on the browser's disk cache (Linux/macOS)


class CdpError(Exception):
    pass


def _find_kind(kind):
    if sys.platform.startswith("win"):
        for root, rel in _WINDOWS_CANDIDATES[kind]:
            base = os.path.expandvars(root)
            if base != root:  # variable was set
                path = os.path.join(base, rel)
                if os.path.isfile(path):
                    return path
        return None
    if sys.platform == "darwin":
        return next((p for p in _MAC_CANDIDATES[kind] if os.path.isfile(p)), None)
    found = next((shutil.which(n) for n in _LINUX_NAMES[kind] if shutil.which(n)), None)
    return found or next((p for p in _LINUX_PATHS[kind] if os.access(p, os.X_OK)), None)


def find_browser(choice="auto", custom_path=""):
    """Path of the browser to drive, or ``None`` if it isn't installed.

    ``choice`` is one of ``BROWSER_CHOICES``; "auto" takes Chrome, else Edge.
    """
    if choice == "custom":
        return custom_path if custom_path and os.path.isfile(custom_path) else None
    if choice in ("chrome", "edge"):
        return _find_kind(choice)
    return _find_kind("chrome") or _find_kind("edge")


def browser_name(path):
    name = os.path.basename(path or "").lower()
    if "edge" in name:
        return "Microsoft Edge"
    if "chromium" in name:
        return "Chromium"
    if "for testing" in name or os.sep + "chrome-linux" in (path or ""):
        return "Chrome for Testing"
    return "Google Chrome"


def pipe_supported():
    """The private pipe needs POSIX file descriptors (not Windows)."""
    return os.name == "posix"


def resolve_connection(choice):
    """"pipe" or "port" for a connection setting ("auto", "pipe", "port")."""
    if choice == "port" or not pipe_supported():
        return "port"
    return "pipe"


def remote_display(env=None):
    """True if the X display isn't on this computer's own screen and GPU:
    X11 forwarded over SSH (DISPLAY=localhost:10.0), a display on another
    host, or no GPU device at all (typical of HPC nodes and VNC servers)."""
    env = os.environ if env is None else env
    if not sys.platform.startswith("linux"):
        return False
    display = env.get("DISPLAY", "")
    if display and not display.startswith(":") and not display.startswith("unix:"):
        return True
    gpu_devices = ("/dev/dri", "/dev/nvidia0")
    return not any(os.path.exists(p) for p in gpu_devices)


def resolve_software_gl(choice, env=None):
    """Whether to draw WebGL in software, for a setting ("auto", "on", "off")."""
    if choice in ("on", "off"):
        return choice == "on"
    return remote_display(env)


def browser_args(profile_dir, url, connection="port", no_sandbox=False, software_gl=False,
                 platform=None):
    """Command-line switches for the Google Earth window (without the program)."""
    platform = platform or sys.platform
    if connection == "pipe":
        args = ["--remote-debugging-pipe"]
    else:
        args = ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0"]
    args += [
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if not platform.startswith("win"):
        # Keep the profile small on quota-limited home folders.
        args.append(f"--disk-cache-size={CACHE_BYTES}")
    if platform.startswith("linux"):
        # Without a desktop keyring (SSH/X11, HPC), Chrome would otherwise ask
        # for a keyring password; this profile stores nothing secret.
        args.append("--password-store=basic")
    if no_sandbox:
        args.append("--no-sandbox")
    if software_gl:
        # Draw WebGL with SwiftShader on the CPU: works without a GPU and
        # over X11 forwarding. Chrome needs the second switch since 2025.
        args += ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    args.append(f"--app={url}")
    return args


def prepare_profile(profile_dir):
    """Create the profile folder, private to this user on shared machines."""
    os.makedirs(profile_dir, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(profile_dir, 0o700)
        except OSError:
            pass  # not ours to change (e.g. a shared folder); leave it


def _open_log(profile_dir):
    try:
        return open(os.path.join(profile_dir, LOG_NAME), "wb")
    except OSError:
        return subprocess.DEVNULL


# Harmless messages Chrome prints on servers (no D-Bus session, no fonts
# config, ...), left out of error messages so the real cause shows.
_LOG_NOISE = ("bus.cc", "dbus", "object_proxy", "Fontconfig", "gcm", "DevTools listening",
              "fallback_task_provider", "kwallet", "libva")


def read_log_tail(profile_dir, lines=6):
    """The last lines the browser printed (errors on failed starts)."""
    try:
        with open(os.path.join(profile_dir, LOG_NAME), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 8192))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    keep = [ln for ln in text.splitlines()
            if ln.strip() and not any(n in ln for n in _LOG_NOISE)]
    return "\n".join(keep[-lines:])


def start_failure_message(name, exit_code, profile_dir, no_sandbox):
    """Why the browser didn't start, with what the user can do about it."""
    if exit_code == 0 and os.path.lexists(os.path.join(profile_dir, "SingletonLock")):
        return (f"{name} handed Google Earth to a GEtools browser window that is already "
                "open (perhaps from an earlier QGIS session), which QGIS can't control. "
                "Close that window and try again.")
    text = f"{name} didn't start" + (
        f" (exit code {exit_code})." if exit_code is not None else " in time.")
    tail = read_log_tail(profile_dir, lines=4)
    if tail:
        text += f" It said:\n{tail}"
    hints = []
    lower = tail.lower()
    if "display" in lower or "x server" in lower:
        hints.append("The browser couldn't open a window on this display.")
    if not no_sandbox:
        hints.append("If this mentions the sandbox (common on HPC and container systems), "
                     "tick \"Disable browser sandbox\" in GEtools Settings.")
    hints.append(f"Full log: {os.path.join(profile_dir, LOG_NAME)}")
    return text + "\n" + " ".join(hints)


def launch(browser, profile_dir, url, args=None):
    """Start ``browser`` in app mode on ``url`` with a local debugging port.

    Port 0 lets the browser pick a free port; it writes it to
    ``DevToolsActivePort`` in the profile, which ``read_port`` picks up.
    ``args`` defaults to ``browser_args(profile_dir, url, "port")``.
    """
    # A stale DevToolsActivePort from an earlier run is harmless: its port
    # just won't answer, and the browser overwrites it once it's up.
    prepare_profile(profile_dir)
    args = args or browser_args(profile_dir, url, "port")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console on Windows
    log = _open_log(profile_dir)
    try:
        return subprocess.Popen([browser, *args], stdin=subprocess.DEVNULL, stdout=log,
                                stderr=log, creationflags=flags)
    finally:
        if log is not subprocess.DEVNULL:
            log.close()


def launch_pipe(browser, profile_dir, args):
    """Start ``browser`` with ``args`` (which must include
    ``--remote-debugging-pipe``) and return a ``PipeBrowser`` for it.

    The browser expects the pipe on file descriptors 3 (commands in) and 4
    (replies out). The two pipe ends are handed over as stdin and stdout, and
    a tiny ``sh`` moves them to 3 and 4 before running the browser, so no
    Python code runs in the child between fork and exec.
    """
    prepare_profile(profile_dir)
    to_browser_r, to_browser_w = os.pipe()
    from_browser_r, from_browser_w = os.pipe()
    log = _open_log(profile_dir)
    try:
        process = subprocess.Popen(
            ["/bin/sh", "-c", 'exec "$@" 3<&0 4>&1 0</dev/null 1>&2', "sh", browser, *args],
            stdin=to_browser_r, stdout=from_browser_w, stderr=log,
            close_fds=True)
    except OSError:
        for fd in (to_browser_r, to_browser_w, from_browser_r, from_browser_w):
            os.close(fd)
        raise
    finally:
        if log is not subprocess.DEVNULL:
            log.close()
    os.close(to_browser_r)
    os.close(from_browser_w)
    return PipeBrowser(process, to_browser_w, from_browser_r)


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


def drop_file_events(path, x, y):
    """CDP ``Input.dispatchDragEvent`` calls that drop a local file at x, y.

    This is the same as dragging the file from Explorer/Finder onto the page.
    """
    data = {"items": [], "files": [os.path.abspath(path)], "dragOperationsMask": 1}
    return [("Input.dispatchDragEvent", {"type": kind, "x": x, "y": y, "data": data})
            for kind in ("dragEnter", "dragOver", "drop")]


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


class PipeBrowser:
    """A browser started by ``launch_pipe``, driven over its private pipe.

    Messages are JSON, each followed by a NUL byte. The pipe reaches the whole
    browser, so pages are driven through sessions (``attach_page``).
    """

    def __init__(self, process, write_fd, read_fd):
        self.process = process
        self._write_fd = write_fd
        self._read_fd = read_fd
        self._buffer = b""
        self._next_id = 0
        self._replies = {}  # id -> reply that arrived while waiting for another
        self._probe = None  # id of the "are you up yet?" request
        self.is_ready = False
        self.closed = False

    # ---- low level ------------------------------------------------------------

    def _send(self, method, params, session_id=None):
        if self.closed:
            raise CdpError("DevTools pipe closed")
        self._next_id += 1
        msg = {"id": self._next_id, "method": method, "params": params}
        if session_id:
            msg["sessionId"] = session_id
        data = json.dumps(msg).encode("utf-8") + b"\0"
        try:
            while data:
                data = data[os.write(self._write_fd, data):]
        except OSError as exc:  # browser gone (EPIPE)
            self.close()
            raise CdpError(f"DevTools pipe closed: {exc}") from exc
        return self._next_id

    def _read_some(self, timeout):
        """Read what's there (waiting up to ``timeout`` s) and keep replies."""
        if self.closed:
            raise CdpError("DevTools pipe closed")
        try:
            ready, _, _ = select.select([self._read_fd], [], [], max(0.0, timeout))
            if not ready:
                return
            chunk = os.read(self._read_fd, 1 << 20)
        except (OSError, ValueError) as exc:
            self.close()
            raise CdpError(f"DevTools pipe closed: {exc}") from exc
        if not chunk:
            self.close()
            raise CdpError("DevTools pipe closed (the browser has quit)")
        self._buffer += chunk
        *messages, self._buffer = self._buffer.split(b"\0")
        for raw in messages:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if "id" in msg:  # events (no id) are dropped
                self._replies[msg["id"]] = msg

    def call(self, method, _session=None, _timeout=3.0, **params):
        """Send one CDP command (to page session ``_session``, if given) and
        return its ``result``."""
        msg_id = self._send(method, params, _session)
        deadline = time.monotonic() + _timeout
        while msg_id not in self._replies:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpError(f"no answer to {method} within {_timeout:g} s")
            self._read_some(remaining)
        reply = self._replies.pop(msg_id)
        # Replies to requests that timed out earlier are never collected.
        self._replies = {k: v for k, v in self._replies.items() if k > msg_id - 100}
        if "error" in reply:
            raise CdpError(reply["error"].get("message", str(reply["error"])))
        return reply.get("result", {})

    def drain(self):
        """Read and drop anything waiting, so the pipe never fills up.
        Raises ``CdpError`` once the browser has quit."""
        self._read_some(0)

    # ---- browser level --------------------------------------------------------

    def alive(self):
        return not self.closed and self.process.poll() is None

    def ready(self):
        """Without blocking: has the browser started answering on the pipe?"""
        if self.is_ready:
            return True
        if self._probe is None:
            self._probe = self._send("Browser.getVersion", {})
        self._read_some(0)
        if self._probe in self._replies:
            self._replies.pop(self._probe)
            self.is_ready = True
        return self.is_ready

    def attach_page(self, url_hint="earth.google.com", new_url=None):
        """A ``PipeSession`` for the Earth page (else any page), or ``None``.
        If the browser has no page at all (macOS keeps running without
        windows), opens ``new_url`` in a new window when given."""
        targets = self.call("Target.getTargets").get("targetInfos", [])
        pages = [t for t in targets if t.get("type") == "page"]
        page = next((t for t in pages if url_hint in t.get("url", "")), None)
        page = page or (pages[0] if pages else None)
        if page is None:
            if not new_url:
                return None
            target_id = self.call("Target.createTarget", url=new_url, newWindow=True)["targetId"]
        else:
            target_id = page["targetId"]
        session = self.call("Target.attachToTarget", targetId=target_id, flatten=True)
        return PipeSession(self, session["sessionId"])

    def close_browser(self, wait=3.0):
        """Ask the browser to quit, and make sure it does."""
        if self.alive():
            try:
                self.call("Browser.close", _timeout=wait)
            except CdpError:
                pass
        self.close()
        try:
            self.process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def close(self):
        """Let go of the pipe. The browser quits when it sees the pipe close."""
        if self.closed:
            return
        self.closed = True
        for fd in (self._write_fd, self._read_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class PipeSession:
    """One page of a ``PipeBrowser``, with the same ``call``/``close`` as
    ``CdpConnection`` so the Earth window code works with either."""

    def __init__(self, browser, session_id):
        self.browser = browser
        self.session_id = session_id

    def call(self, method, **params):
        return self.browser.call(method, _session=self.session_id, **params)

    def close(self):
        if self.browser.alive():
            try:
                self.browser.call("Target.detachFromTarget", sessionId=self.session_id,
                                  _timeout=1.0)
            except CdpError:
                pass


def check_browser(browser, profile_dir, connection, no_sandbox=False, software_gl=False,
                  earth_url="https://earth.google.com/web/", timeout=STARTUP_TIMEOUT_S):
    """Start ``browser`` as the Google Earth window would, look at what works,
    and close it again. Returns a list of ``(ok, text)`` lines.

    ``profile_dir`` should be a throwaway folder, so the test can't meet a
    Google Earth window that is already open.
    """
    name = browser_name(browser)
    lines = [(True, f"Browser: {browser}")]
    args = browser_args(profile_dir, "about:blank", connection, no_sandbox, software_gl)
    pipe = process = conn = None
    try:
        try:
            if connection == "pipe":
                pipe = launch_pipe(browser, profile_dir, args)
                process = pipe.process
            else:
                process = launch(browser, profile_dir, "about:blank", args)
        except OSError as exc:
            return lines + [(False, f"Couldn't start {name}: {exc}")]
        deadline = time.monotonic() + timeout
        while conn is None and time.monotonic() < deadline and process.poll() is None:
            try:
                if pipe is not None:
                    if pipe.ready():
                        conn = pipe.attach_page()
                else:
                    port = read_port(profile_dir)
                    ws_url = find_page(port) if port else None
                    conn = CdpConnection(ws_url) if ws_url else None
            except CdpError:
                conn = None
            if conn is None:
                time.sleep(0.2)
        if conn is None:
            code = process.poll()
            return lines + [(False, start_failure_message(name, code, profile_dir, no_sandbox))]
        lines.append((True, f"{name} started and answered over the " +
                      ("private pipe." if pipe is not None else "local port (127.0.0.1).")))
        lines.append((True, "Browser sandbox: " + ("off (Disable browser sandbox is ticked)."
                                                   if no_sandbox else "on.")))
        webgl = conn.call("Runtime.evaluate", expression=WEBGL_JS,
                          returnByValue=True).get("result", {}).get("value")
        if webgl:
            lines.append((True, f"WebGL: {webgl}" + (" (software)" if software_gl else "")))
        else:
            lines.append((False, "WebGL: not available, so Google Earth can't draw. " + (
                "Set Software WebGL to On." if not software_gl else
                "Even software WebGL failed on this display.")))
        error = conn.call("Page.navigate", url=earth_url).get("errorText")
        if not error:
            time.sleep(1.0)
            href = conn.call("Runtime.evaluate", expression="location.href",
                             returnByValue=True).get("result", {}).get("value") or ""
            error = "error page" if href.startswith("chrome-error:") else None
        if error:
            lines.append((False, f"Google Earth: couldn't load {earth_url} ({error}). No "
                                 "internet access here, or a proxy is needed (https_proxy)."))
        else:
            lines.append((True, f"Google Earth: {earth_url} loaded."))
        return lines
    except CdpError as exc:
        return lines + [(False, f"Lost contact with {name}: {exc}")]
    finally:
        if conn is not None and pipe is None:
            try:
                conn.call("Browser.close")
            except CdpError:
                pass
            conn.close()
        if pipe is not None:
            pipe.close_browser()
        elif process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
