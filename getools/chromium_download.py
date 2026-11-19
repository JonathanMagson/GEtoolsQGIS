"""Download Chromium for the Google Earth window, where no Chrome or Edge is
installed (typical of HPC and other shared Linux systems).

It fetches Google's portable "Chrome for Testing" build (the official
Chromium build Google publishes for automation) into a folder of the user's.
No administrator rights are needed. Pure Python standard library.
"""

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile

VERSIONS_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
                "last-known-good-versions-with-downloads.json")
FOLDER = "getools_chromium"

# The browser program inside each platform's zip.
_PROGRAMS = {
    "linux64": "chrome-linux64/chrome",
    "mac-arm64": "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
                 "Google Chrome for Testing",
    "mac-x64": "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/"
               "Google Chrome for Testing",
    "win64": "chrome-win64/chrome.exe",
}


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    pass


def platform_key(system=None, machine=None):
    """The Chrome for Testing platform for this computer, or ``None``."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system.startswith("linux"):
        return "linux64" if machine in ("x86_64", "amd64") else None
    if system == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    if system.startswith("win"):
        return "win64" if machine in ("amd64", "x86_64") else None
    return None


def latest_download(key, versions_json):
    """(version, zip URL) of the stable build for platform ``key``."""
    try:
        stable = versions_json["channels"]["Stable"]
        url = next(d["url"] for d in stable["downloads"]["chrome"] if d["platform"] == key)
        return stable["version"], url
    except (KeyError, StopIteration, TypeError) as exc:
        raise DownloadError(f"no Chromium download listed for {key}") from exc


def find_installed(root):
    """The downloaded browser program under ``root``, or ``None``."""
    key = platform_key()
    folder = os.path.join(root, FOLDER)
    if key is None or not os.path.isdir(folder):
        return None
    for version in sorted(os.listdir(folder), key=_version_key, reverse=True):
        program = os.path.join(folder, version, _PROGRAMS[key])
        if os.path.isfile(program) and os.access(program, os.X_OK):
            return program
    return None


def _version_key(name):
    return tuple(int(p) if p.isdigit() else -1 for p in name.split("."))


def _extract(archive, dest):
    """Unzip keeping the executable bits and symlinks (macOS app bundles need
    them), and refusing paths that would land outside ``dest``."""
    dest_real = os.path.realpath(dest)
    for info in archive.infolist():
        target = os.path.realpath(os.path.join(dest, info.filename))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise DownloadError(f"unsafe path in the download: {info.filename}")
        mode = info.external_attr >> 16
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if stat.S_ISLNK(mode):
            link = archive.read(info).decode("utf-8")
            if os.path.isabs(link):
                raise DownloadError(f"unsafe link in the download: {info.filename}")
            os.symlink(link, target)
            continue
        with archive.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        if mode & 0o111:
            os.chmod(target, 0o755)


def install(root, progress=None, cancelled=None, urlopen=urllib.request.urlopen):
    """Download and unpack the latest stable Chromium under ``root``.

    ``progress(done_bytes, total_bytes)`` is called while downloading;
    ``cancelled()`` returning True stops it. Returns the browser program.
    Uses the system proxy settings (http_proxy/https_proxy).
    """
    key = platform_key()
    if key is None:
        raise DownloadError(f"Chromium isn't available for {sys.platform} {platform.machine()}")
    try:
        with urlopen(VERSIONS_URL, timeout=30) as resp:
            version, url = latest_download(key, json.loads(resp.read().decode("utf-8")))
    except (OSError, ValueError) as exc:
        raise DownloadError(f"couldn't get the list of Chromium versions: {exc}") from exc

    folder = os.path.join(root, FOLDER)
    os.makedirs(folder, exist_ok=True)
    zip_path = os.path.join(folder, "download.zip")
    partial = os.path.join(folder, version + ".partial")
    try:
        try:
            with urlopen(url, timeout=60) as resp, open(zip_path, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    if cancelled and cancelled():
                        raise Cancelled("cancelled")
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            if total and done != total:
                raise DownloadError(f"download incomplete ({done} of {total} bytes)")
        except OSError as exc:
            raise DownloadError(f"download failed: {exc}") from exc
        shutil.rmtree(partial, ignore_errors=True)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                _extract(archive, partial)
        except (OSError, zipfile.BadZipFile) as exc:
            raise DownloadError(f"couldn't unpack the download: {exc}") from exc
        final = os.path.join(folder, version)
        shutil.rmtree(final, ignore_errors=True)
        os.rename(partial, final)
    finally:
        shutil.rmtree(partial, ignore_errors=True)
        if os.path.exists(zip_path):
            os.remove(zip_path)
    # Only one version is kept, to save space.
    for name in os.listdir(folder):
        if name != version:
            shutil.rmtree(os.path.join(folder, name), ignore_errors=True)
    program = os.path.join(folder, version, _PROGRAMS[key])
    if not os.path.isfile(program):
        raise DownloadError(f"the download has no {_PROGRAMS[key]}")
    return program


def missing_libraries(program):
    """System libraries the browser needs but this Linux lacks (from ldd)."""
    if not sys.platform.startswith("linux"):
        return []
    try:
        out = subprocess.run(["ldd", program], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({line.split("=>")[0].strip() for line in out.splitlines()
                   if "not found" in line})
