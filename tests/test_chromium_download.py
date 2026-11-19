import io
import json
import os
import stat
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from getools import chromium_download as cd  # noqa: E402

VERSIONS = {"channels": {"Stable": {"version": "200.0.1.2", "downloads": {"chrome": [
    {"platform": "linux64", "url": "https://example.invalid/linux64.zip"},
    {"platform": "mac-arm64", "url": "https://example.invalid/mac.zip"}]}}}}


def test_platform_key():
    assert cd.platform_key("linux", "x86_64") == "linux64"
    assert cd.platform_key("linux", "aarch64") is None
    assert cd.platform_key("darwin", "arm64") == "mac-arm64"
    assert cd.platform_key("win32", "AMD64") == "win64"


def test_latest_download():
    assert cd.latest_download("linux64", VERSIONS) == ("200.0.1.2",
                                                       "https://example.invalid/linux64.zip")


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            z.writestr(info, data)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_install_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "platform_key", lambda: "linux64")
    payload = _zip_bytes([("chrome-linux64/chrome", b"#!/bin/sh\n", stat.S_IFREG | 0o755),
                          ("chrome-linux64/lib.so", b"x", stat.S_IFREG | 0o644),
                          ("chrome-linux64/link", b"lib.so", stat.S_IFLNK | 0o777)])

    def urlopen(url, timeout=None):
        return _Resp(json.dumps(VERSIONS).encode() if url == cd.VERSIONS_URL else payload)

    (tmp_path / cd.FOLDER / "100.0.0.0").mkdir(parents=True)  # older version: removed
    seen = []
    program = cd.install(str(tmp_path), progress=lambda d, t: seen.append((d, t)),
                         urlopen=urlopen)
    assert program == str(tmp_path / cd.FOLDER / "200.0.1.2" / "chrome-linux64" / "chrome")
    assert os.access(program, os.X_OK)
    assert os.readlink(os.path.join(os.path.dirname(program), "link")) == "lib.so"
    assert sorted(os.listdir(tmp_path / cd.FOLDER)) == ["200.0.1.2"]
    assert seen[-1] == (len(payload), len(payload))
    assert cd.find_installed(str(tmp_path)) == program


def test_install_refuses_zip_slip(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "platform_key", lambda: "linux64")
    payload = _zip_bytes([("../evil", b"x", stat.S_IFREG | 0o644)])

    def urlopen(url, timeout=None):
        return _Resp(json.dumps(VERSIONS).encode() if url == cd.VERSIONS_URL else payload)

    try:
        cd.install(str(tmp_path / "root"), urlopen=urlopen)
        raised = False
    except cd.DownloadError:
        raised = True
    assert raised and not (tmp_path / "evil").exists()
    assert os.listdir(tmp_path / "root" / cd.FOLDER) == []


def test_install_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "platform_key", lambda: "linux64")
    payload = _zip_bytes([("chrome-linux64/chrome", b"x", stat.S_IFREG | 0o755)])

    def urlopen(url, timeout=None):
        return _Resp(json.dumps(VERSIONS).encode() if url == cd.VERSIONS_URL else payload)

    try:
        cd.install(str(tmp_path), cancelled=lambda: True, urlopen=urlopen)
        raised = False
    except cd.Cancelled:
        raised = True
    assert raised and os.listdir(tmp_path / cd.FOLDER) == []
