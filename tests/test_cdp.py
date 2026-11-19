import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from getools import cdp  # noqa: E402


def test_custom_browser_must_exist(tmp_path):
    exe = tmp_path / "browser"
    assert cdp.find_browser("custom", str(exe)) is None
    exe.write_text("")
    assert cdp.find_browser("custom", str(exe)) == str(exe)


def test_drop_file_events(tmp_path):
    events = cdp.drop_file_events(str(tmp_path / "a.kml"), 10, 20)
    assert [p["type"] for _, p in events] == ["dragEnter", "dragOver", "drop"]
    assert all(m == "Input.dispatchDragEvent" for m, _ in events)
    assert events[2][1]["data"]["files"] == [str(tmp_path / "a.kml")]


def test_connection_choice():
    if cdp.pipe_supported():
        assert cdp.resolve_connection("auto") == "pipe"
        assert cdp.resolve_connection("pipe") == "pipe"
    else:
        assert cdp.resolve_connection("auto") == "port"
        assert cdp.resolve_connection("pipe") == "port"
    assert cdp.resolve_connection("port") == "port"


def test_browser_args_port_on_windows_unchanged():
    """Windows keeps exactly the 1.5 switches."""
    args = cdp.browser_args("P", "U", "port", platform="win32")
    assert args == ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                    "--user-data-dir=P", "--no-first-run", "--no-default-browser-check",
                    "--app=U"]


def test_browser_args_pipe_linux_options():
    args = cdp.browser_args("P", "U", "pipe", no_sandbox=True, software_gl=True,
                            platform="linux")
    assert "--remote-debugging-pipe" in args
    assert not any(a.startswith("--remote-debugging-port") for a in args)
    assert "--no-sandbox" in args and "--password-store=basic" in args
    assert "--use-angle=swiftshader" in args and "--enable-unsafe-swiftshader" in args
    assert args[-1] == "--app=U"
    plain = cdp.browser_args("P", "U", "pipe", platform="linux")
    assert "--no-sandbox" not in plain and "--use-angle=swiftshader" not in plain


def test_software_gl_auto(monkeypatch):
    monkeypatch.setattr(cdp.sys, "platform", "linux")
    assert cdp.resolve_software_gl("on") is True
    assert cdp.resolve_software_gl("off", {"DISPLAY": "localhost:27.0"}) is False
    # X11 forwarded over SSH
    assert cdp.resolve_software_gl("auto", {"DISPLAY": "localhost:27.0"}) is True
    # Local display: depends on whether there's a GPU device
    monkeypatch.setattr(cdp.os.path, "exists", lambda p: True)
    assert cdp.resolve_software_gl("auto", {"DISPLAY": ":0"}) is False
    monkeypatch.setattr(cdp.os.path, "exists", lambda p: False)
    assert cdp.resolve_software_gl("auto", {"DISPLAY": ":1"}) is True
    monkeypatch.setattr(cdp.sys, "platform", "win32")
    assert cdp.resolve_software_gl("auto", {}) is False


def test_log_tail_skips_noise(tmp_path):
    (tmp_path / cdp.LOG_NAME).write_text(
        "[1:1:ERROR:bus.cc(407)] Failed to connect to the bus\n"
        "[1:1:FATAL:zygote_host_impl_linux.cc(128)] No usable sandbox!\n")
    assert cdp.read_log_tail(str(tmp_path)) == \
        "[1:1:FATAL:zygote_host_impl_linux.cc(128)] No usable sandbox!"
    msg = cdp.start_failure_message("Chromium", 1, str(tmp_path), no_sandbox=False)
    assert "No usable sandbox" in msg and "Disable browser sandbox" in msg
    assert "Disable browser sandbox" not in cdp.start_failure_message(
        "Chromium", 1, str(tmp_path), no_sandbox=True)


def test_profile_is_private(tmp_path):
    profile = tmp_path / "p"
    cdp.prepare_profile(str(profile))
    if os.name == "posix":
        assert (profile.stat().st_mode & 0o777) == 0o700


def _fake_browser(tmp_path, body):
    """A stand-in "browser": a Python script speaking CDP on fds 3/4."""
    script = tmp_path / "fake_browser"
    script.write_text("#!" + sys.executable + "\n" + body)
    script.chmod(0o755)
    return str(script)


FAKE_PIPE_BROWSER = r'''
import json, os, sys
assert "--remote-debugging-pipe" in sys.argv
buf = b""
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        sys.exit(0)  # QGIS let go of the pipe: quit
    buf += chunk
    *msgs, buf = buf.split(b"\0")
    for raw in msgs:
        m = json.loads(raw)
        os.write(4, json.dumps({"method": "Some.event", "params": {}}).encode() + b"\0")
        if m["method"] == "Target.getTargets":
            res = {"targetInfos": [{"type": "page", "targetId": "T1",
                                    "url": "https://earth.google.com/web/"}]}
        elif m["method"] == "Target.attachToTarget":
            res = {"sessionId": "S1"}
        elif m["method"] == "Runtime.evaluate":
            res = {"result": {"value": [m.get("sessionId"), m["params"]["expression"]]}}
        elif m["method"] == "Browser.close":
            os.write(4, json.dumps({"id": m["id"], "result": {}}).encode() + b"\0")
            sys.exit(0)
        else:
            res = {}
        os.write(4, json.dumps({"id": m["id"], "result": res}).encode() + b"\0")
'''


def test_pipe_round_trip(tmp_path):
    if not cdp.pipe_supported():
        return
    exe = _fake_browser(tmp_path, FAKE_PIPE_BROWSER)
    browser = cdp.launch_pipe(exe, str(tmp_path / "profile"),
                              cdp.browser_args("p", "about:blank", "pipe"))
    for _ in range(100):
        if browser.ready():
            break
        time.sleep(0.05)
    assert browser.ready()
    page = browser.attach_page()
    value = page.call("Runtime.evaluate", expression="1+1")["result"]["value"]
    assert value == ["S1", "1+1"]  # sent to the page's session
    browser.drain()
    browser.close_browser()
    assert browser.process.returncode == 0 and not browser.alive()


def test_pipe_reports_browser_exit(tmp_path):
    if not cdp.pipe_supported():
        return
    exe = _fake_browser(tmp_path, "import sys; sys.exit(3)\n")
    browser = cdp.launch_pipe(exe, str(tmp_path / "profile"), ["--remote-debugging-pipe"])
    browser.process.wait(5)
    try:
        for _ in range(50):
            browser.drain()
            time.sleep(0.02)
        raised = False
    except cdp.CdpError:
        raised = True
    assert raised and not browser.alive()
