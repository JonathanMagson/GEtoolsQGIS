"""Web ▸ GEtools ▸ Settings…"""

import os
import shutil
import tempfile
import threading
import time

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qgis.gui import QgsColorButton, QgsFileWidget

from . import cdp, chromium_download, settings
from .earth_pro import find_earth_pro
from .external_earth import find_earth_browser
from .google_urls import GOOGLE_EARTH_WEB

_BROWSERS = (
    ("auto", "Automatic (Chrome, else Edge, else downloaded Chromium)"),
    ("chrome", "Google Chrome"),
    ("edge", "Microsoft Edge"),
    ("custom", "Other Chromium browser…"),
)
_CONNECTIONS = (
    ("auto", "Automatic (private pipe on Linux and macOS, local port on Windows)"),
    ("pipe", "Private pipe (Linux and macOS)"),
    ("port", "Local port (127.0.0.1)"),
)
_SOFTWARE_GL = (
    ("auto", "Automatic (on for remote, X11-forwarded or GPU-less Linux displays)"),
    ("on", "On"),
    ("off", "Off"),
)


def _run_in_thread(parent, label, work, progress=None, cancel=None):
    """Run ``work()`` in a thread behind a modal progress dialog and return
    its result (exceptions are re-raised here). ``progress()`` gives
    (done, total) for the bar; with ``cancel`` set, Cancel calls it."""
    dialog = QProgressDialog(label, "Cancel" if cancel else None, 0, 0, parent)
    dialog.setWindowTitle("GEtools")
    dialog.setWindowModality(getattr(Qt, "WindowModality", Qt).WindowModal)
    dialog.setMinimumDuration(0)
    if cancel:
        dialog.canceled.connect(cancel)
    outcome = {}

    def target():
        try:
            outcome["result"] = work()
        except Exception as exc:  # handed back to the caller below
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    while thread.is_alive():
        if progress:
            done, total = progress()
            if total:
                dialog.setMaximum(100)
                dialog.setValue(int(100 * done / total))
                dialog.setLabelText(f"{label}\n{done / 1e6:.0f} of {total / 1e6:.0f} MB")
        QApplication.processEvents()
        time.sleep(0.05)
    dialog.close()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")


class SettingsDialog(QDialog):
    def __init__(self, parent=None, webengine=False):
        super().__init__(parent)
        self.setWindowTitle("GEtools Settings")
        outer = QVBoxLayout(self)
        # Scrolls on small screens (remote desktops, X11 forwarding).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(getattr(QFrame, "Shape", QFrame).NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ---- Which Google Earth ---------------------------------------------
        box = QGroupBox("Google Earth version")
        form = QFormLayout(box)
        self.use_web = QRadioButton("Google Earth Web (cloud, runs in a browser)")
        self.use_pro = QRadioButton("Google Earth Pro (desktop app)")
        form.addRow(self.use_web)
        form.addRow(self.use_pro)
        self.pro_path = QgsFileWidget()
        self.pro_path.setDialogTitle("Google Earth Pro program (googleearth.exe)")
        self.pro_path.setToolTip("Leave empty to find Google Earth Pro automatically")
        self.pro_found = QLabel()
        self.pro_path.fileChanged.connect(self._earth_changed)
        form.addRow("Earth Pro program", self.pro_path)
        form.addRow("", self.pro_found)
        self.use_web.toggled.connect(self._earth_changed)
        layout.addWidget(box)

        # ---- Google Earth window --------------------------------------------
        box = QGroupBox("Google Earth window")
        form = QFormLayout(box)
        self.browser_label = QLabel(
            "Google Earth Web opens in a panel inside QGIS (Qt WebEngine is available),\n"
            "so the browser choice below isn't used." if webengine else
            "Google Earth Web runs in its own Chrome, Edge or Chromium window.")
        form.addRow(self.browser_label)
        self.browser = QComboBox()
        for key, label in _BROWSERS:
            self.browser.addItem(label, key)
        self.browser_path = QgsFileWidget()
        self.browser_path.setDialogTitle("Chromium-based browser (chrome.exe, msedge.exe, …)")
        self.browser_found = QLabel()
        self.browser.currentIndexChanged.connect(self._browser_changed)
        self.browser_path.fileChanged.connect(self._browser_changed)
        self.web_only = [self.browser_label, self.browser, self.browser_path, self.browser_found]
        form.addRow("Browser", self.browser)
        form.addRow("Browser program", self.browser_path)
        form.addRow("", self.browser_found)
        self.raise_window = QCheckBox("Bring Google Earth to front when OpenInGE or LayerToGE "
                                      "moves it (restores a minimized window)")
        form.addRow(self.raise_window)
        layout.addWidget(box)

        # ---- Browser connection (Earth Web window) --------------------------
        box = QGroupBox("Browser options (Google Earth Web window)")
        form = QFormLayout(box)
        self.connection = QComboBox()
        for key, label in _CONNECTIONS:
            self.connection.addItem(label, key)
        if not cdp.pipe_supported():
            self.connection.model().item(self.connection.findData("pipe")).setEnabled(False)
        self.connection.setToolTip(
            "How QGIS controls the Google Earth window. The private pipe can't be reached by "
            "other users on a shared computer (e.g. an HPC login node); the window then "
            "closes with QGIS. The local port lets QGIS reattach to an open window.")
        form.addRow("Connection", self.connection)
        self.software_gl = QComboBox()
        for key, label in _SOFTWARE_GL:
            self.software_gl.addItem(label, key)
        self.software_gl.setToolTip(
            "Draw Google Earth's 3D (WebGL) on the CPU. Needed where there's no usable GPU, "
            "e.g. X11 forwarding over SSH or a remote desktop on an HPC node. Slower.")
        form.addRow("Software WebGL", self.software_gl)
        self.no_sandbox = QCheckBox("Disable browser sandbox (--no-sandbox)")
        self.no_sandbox.setToolTip(
            "Only if the browser won't start because its sandbox isn't allowed (some HPC "
            "and container systems). Makes the browser less safe against harmful web pages.")
        form.addRow(self.no_sandbox)
        self.data_dir = QgsFileWidget()
        storage = getattr(QgsFileWidget, "StorageMode", QgsFileWidget)
        self.data_dir.setStorageMode(storage.GetDirectory)
        self.data_dir.setDialogTitle("Folder for the Google Earth browser's data")
        self.data_dir.setToolTip(
            "Where the browser keeps its profile (and a downloaded Chromium). Empty = the "
            "QGIS profile folder. Pick a scratch or project folder if your home folder has "
            "a small quota.")
        self.data_dir.fileChanged.connect(self._browser_changed)
        form.addRow("Browser data folder", self.data_dir)
        row = QHBoxLayout()
        self.download_button = QPushButton("Download Chromium…")
        self.download_button.setToolTip(
            "Download Google's portable Chromium (Chrome for Testing, about 210 MB) into the "
            "browser data folder. For computers without Chrome or Edge; no admin rights needed.")
        self.download_button.clicked.connect(self._download_chromium)
        self.download_button.setVisible(
            os.name != "nt" and chromium_download.platform_key() is not None)
        self.test_button = QPushButton("Test browser…")
        self.test_button.setToolTip("Start the browser with these options and check the "
                                    "connection, WebGL and access to Google Earth")
        self.test_button.clicked.connect(self._test_browser)
        row.addWidget(self.download_button)
        row.addWidget(self.test_button)
        row.addStretch(1)
        form.addRow(row)
        self.web_only += [self.connection, self.software_gl, self.no_sandbox, self.data_dir,
                          self.download_button, self.test_button]
        layout.addWidget(box)

        # ---- Sync -----------------------------------------------------------
        box = QGroupBox("SyncToGE")
        form = QFormLayout(box)
        self.fast_sync = QCheckBox("Fast sync (Google Earth Web): move Google Earth without "
                                   "reloading the page")
        self.web_only.append(self.fast_sync)
        form.addRow(self.fast_sync)
        self.sync_delay = QSpinBox()
        self.sync_delay.setRange(100, 5000)
        self.sync_delay.setSingleStep(100)
        self.sync_delay.setSuffix(" ms")
        self.sync_delay.setToolTip("How long the map must stay still before Google Earth follows")
        form.addRow("Sync delay", self.sync_delay)
        self.two_way = QCheckBox("Two-way sync: moving Google Earth moves the QGIS map (while SyncToGE is on)")
        form.addRow(self.two_way)
        self.footprint = QCheckBox("Show Google Earth's view on the QGIS map")
        self.footprint_color = QgsColorButton()
        self.footprint_color.setAllowOpacity(False)
        form.addRow(self.footprint)
        form.addRow("Outline colour", self.footprint_color)
        layout.addWidget(box)

        # ---- OpenInGE and targets -------------------------------------------
        box = QGroupBox("OpenInGE and targets")
        form = QFormLayout(box)
        self.point_range = QDoubleSpinBox()
        self.point_range.setRange(50, 1_000_000)
        self.point_range.setDecimals(0)
        self.point_range.setSingleStep(250)
        self.point_range.setSuffix(" m")
        self.point_range.setToolTip("Camera distance from the clicked spot")
        form.addRow("View distance", self.point_range)
        self.point_tilt = QSpinBox()
        self.point_tilt.setRange(0, 80)
        self.point_tilt.setSuffix("°")
        self.point_tilt.setSpecialValueText("0° (straight down)")
        self.point_tilt.setToolTip("0 looks straight down; higher values give an angled 3D view")
        form.addRow("View tilt", self.point_tilt)
        self.keep_all = QCheckBox("Keep every target (otherwise only the last click is marked)")
        form.addRow(self.keep_all)
        self.target_color = QgsColorButton()
        self.target_color.setAllowOpacity(False)
        form.addRow("Target colour", self.target_color)
        self.target_size = QSpinBox()
        self.target_size.setRange(10, 80)
        self.target_size.setSuffix(" px")
        form.addRow("Target size", self.target_size)
        layout.addWidget(box)

        # ---- LayerToGE ------------------------------------------------------
        box = QGroupBox("LayerToGE")
        form = QFormLayout(box)
        self.auto_import = QCheckBox("Google Earth Web: load the layer automatically "
                                     "(drops the KML onto it). Earth Pro always opens it.")
        self.web_only.append(self.auto_import)
        form.addRow(self.auto_import)
        layout.addWidget(box)

        # ---- Buttons --------------------------------------------------------
        std = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
        role = getattr(QDialogButtonBox, "ButtonRole", QDialogButtonBox)
        buttons = QDialogButtonBox(std.Ok | std.Cancel | std.RestoreDefaults)
        shortcuts = QPushButton("Keyboard shortcuts…")
        shortcuts.setToolTip("Change the GEtools shortcuts (defaults: Alt+Shift+G, S, L, M, V, X)")
        shortcuts.clicked.connect(self._shortcuts)
        buttons.addButton(shortcuts, role.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(std.RestoreDefaults).clicked.connect(self._restore_defaults)
        outer.addWidget(buttons)

        self._load(settings.get)
        # Open at full size when it fits; scroll only on small screens.
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 900
        hint = content.sizeHint()
        self.resize(hint.width() + 40,
                    min(hint.height() + buttons.sizeHint().height() + 30, int(avail * 0.9)))

    def _load(self, value):
        (self.use_pro if value("earth_app") == "pro" else self.use_web).setChecked(True)
        self.pro_path.setFilePath(value("pro_path"))
        self.browser.setCurrentIndex(max(0, self.browser.findData(value("browser"))))
        self.browser_path.setFilePath(value("browser_path"))
        self.connection.setCurrentIndex(max(0, self.connection.findData(value("connection"))))
        self.software_gl.setCurrentIndex(
            max(0, self.software_gl.findData(value("software_webgl"))))
        self.no_sandbox.setChecked(value("no_sandbox"))
        self.data_dir.setFilePath(value("browser_data_dir"))
        self.raise_window.setChecked(value("raise_earth_window"))
        self.fast_sync.setChecked(value("fast_sync"))
        self.sync_delay.setValue(value("sync_delay_ms"))
        self.two_way.setChecked(value("two_way_sync"))
        self.footprint.setChecked(value("show_footprint"))
        self.footprint_color.setColor(QColor(value("footprint_color")))
        self.point_range.setValue(value("point_range_m"))
        self.point_tilt.setValue(int(value("point_tilt")))
        self.keep_all.setChecked(value("keep_all_targets"))
        self.target_color.setColor(QColor(value("target_color")))
        self.target_size.setValue(value("target_size"))
        self.auto_import.setChecked(value("auto_import_layer"))
        self._browser_changed()
        self._earth_changed()

    def _restore_defaults(self):
        self._load(lambda name: settings.DEFAULTS[name][0])

    def _browser_changed(self, *_):
        choice = self.browser.currentData()
        self.browser_path.setEnabled(choice == "custom")
        path = self._chosen_browser()
        self.browser_found.setText(
            f"Using: {path}" if path else
            "Not found: live sync and the target X won't be available" +
            (" (use Download Chromium… below)" if not self.download_button.isHidden() else ""))

    def _chosen_browser(self):
        return find_earth_browser(self.browser.currentData(), self.browser_path.filePath(),
                                  self._data_root())

    def _data_root(self):
        return settings.browser_data_dir(self.data_dir.filePath())

    def _download_chromium(self):
        root = self._data_root()
        answer = QMessageBox.question(
            self, "Download Chromium",
            "Download Chromium (Google's \"Chrome for Testing\" build, about 210 MB, about "
            f"400 MB unpacked) from Google into\n{os.path.join(root, chromium_download.FOLDER)}?")
        if answer != getattr(QMessageBox, "StandardButton", QMessageBox).Yes:
            return
        state = {"done": 0, "total": 0, "cancel": False}

        def progress(done, total):
            state["done"], state["total"] = done, total

        def cancel():
            state["cancel"] = True

        try:
            program = _run_in_thread(
                self, "Downloading Chromium…",
                lambda: chromium_download.install(root, progress, lambda: state["cancel"]),
                progress=lambda: (state["done"], state["total"]), cancel=cancel)
        except chromium_download.Cancelled:
            return
        except (chromium_download.DownloadError, OSError) as exc:
            QMessageBox.warning(self, "Download Chromium", f"Chromium wasn't installed: {exc}")
            return
        if self.browser.currentData() != "custom" and self._chosen_browser() != program:
            self.browser.setCurrentIndex(self.browser.findData("auto"))
        self._browser_changed()
        missing = chromium_download.missing_libraries(program)
        if missing:
            QMessageBox.warning(
                self, "Download Chromium",
                f"Chromium was downloaded to\n{program}\nbut this system lacks libraries it "
                "needs:\n" + ", ".join(missing) +
                "\nAsk your system administrators to install them, then use Test browser….")
        else:
            QMessageBox.information(
                self, "Download Chromium",
                f"Chromium is ready:\n{program}\nClick OK in Settings to use it, and "
                "Test browser… to check it works here.")

    def _test_browser(self):
        browser = self._chosen_browser()
        if browser is None:
            QMessageBox.warning(self, "Test browser", "No browser found to test.")
            return
        connection = cdp.resolve_connection(self.connection.currentData())
        software_gl = cdp.resolve_software_gl(self.software_gl.currentData())
        no_sandbox = self.no_sandbox.isChecked()
        # A throwaway profile, so the test never meets the real Earth window.
        profile = tempfile.mkdtemp(prefix="getools_test_")
        try:
            lines = _run_in_thread(
                self, f"Testing {cdp.browser_name(browser)}…",
                lambda: cdp.check_browser(browser, profile, connection, no_sandbox,
                                          software_gl, GOOGLE_EARTH_WEB + "/"))
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        ok = all(good for good, _ in lines)
        text = "\n".join(("✔ " if good else "✘ ") + line for good, line in lines)
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "Test browser", ("Everything needed works.\n\n" if ok else
                                   "Something needs attention.\n\n") + text)

    def _earth_changed(self, *_):
        pro = self.use_pro.isChecked()
        self.pro_path.setEnabled(pro)
        for widget in self.web_only:
            widget.setEnabled(not pro)
        path = find_earth_pro(self.pro_path.filePath())
        self.pro_found.setEnabled(pro)
        self.pro_found.setText(
            f"Using: {path}" if path else
            "Not found: .kml files will open in whatever program opens them")

    def _shortcuts(self):
        """QGIS's own Keyboard Shortcuts dialog, where the GEtools ones live."""
        from qgis.gui import QgsConfigureShortcutsDialog

        dialog = QgsConfigureShortcutsDialog(self)
        (getattr(dialog, "exec", None) or dialog.exec_)()

    def save(self):
        """Write the dialog's values to the settings store."""
        settings.set("earth_app", "pro" if self.use_pro.isChecked() else "web")
        settings.set("pro_path", self.pro_path.filePath())
        settings.set("browser", self.browser.currentData())
        settings.set("browser_path", self.browser_path.filePath())
        settings.set("connection", self.connection.currentData())
        settings.set("software_webgl", self.software_gl.currentData())
        settings.set("no_sandbox", self.no_sandbox.isChecked())
        settings.set("browser_data_dir", self.data_dir.filePath())
        settings.set("raise_earth_window", self.raise_window.isChecked())
        settings.set("fast_sync", self.fast_sync.isChecked())
        settings.set("sync_delay_ms", self.sync_delay.value())
        settings.set("two_way_sync", self.two_way.isChecked())
        settings.set("show_footprint", self.footprint.isChecked())
        settings.set("footprint_color", self.footprint_color.color().name())
        settings.set("point_range_m", self.point_range.value())
        settings.set("point_tilt", float(self.point_tilt.value()))
        settings.set("keep_all_targets", self.keep_all.isChecked())
        settings.set("target_color", self.target_color.color().name())
        settings.set("target_size", self.target_size.value())
        settings.set("auto_import_layer", self.auto_import.isChecked())
