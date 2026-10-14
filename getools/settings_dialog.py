"""Web ▸ GEtools ▸ Settings…"""

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from qgis.gui import QgsColorButton, QgsFileWidget

from . import cdp, settings

_BROWSERS = (
    ("auto", "Automatic (Chrome, else Edge)"),
    ("chrome", "Google Chrome"),
    ("edge", "Microsoft Edge"),
    ("custom", "Other Chromium browser…"),
)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, webengine=False):
        super().__init__(parent)
        self.setWindowTitle("GEtools Settings")
        layout = QVBoxLayout(self)

        # ---- Google Earth window --------------------------------------------
        box = QGroupBox("Google Earth window")
        form = QFormLayout(box)
        if webengine:
            form.addRow(QLabel(
                "Google Earth opens in a panel inside QGIS (Qt WebEngine is available),\n"
                "so the browser choice below isn't used."))
        self.browser = QComboBox()
        for key, label in _BROWSERS:
            self.browser.addItem(label, key)
        self.browser_path = QgsFileWidget()
        self.browser_path.setDialogTitle("Chromium-based browser (chrome.exe, msedge.exe, …)")
        self.browser_found = QLabel()
        self.browser.currentIndexChanged.connect(self._browser_changed)
        self.browser_path.fileChanged.connect(self._browser_changed)
        form.addRow("Browser", self.browser)
        form.addRow("Browser program", self.browser_path)
        form.addRow("", self.browser_found)
        self.raise_window = QCheckBox("Bring Google Earth to front when OpenInGE or LayerToGE "
                                      "moves it (restores a minimized window)")
        form.addRow(self.raise_window)
        layout.addWidget(box)

        # ---- Sync -----------------------------------------------------------
        box = QGroupBox("SyncToGE")
        form = QFormLayout(box)
        self.fast_sync = QCheckBox("Fast sync: move Google Earth without reloading the page")
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
        self.auto_import = QCheckBox("Load the layer into Google Earth automatically "
                                     "(drops the KML onto it)")
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
        layout.addWidget(buttons)

        self._load(settings.get)

    def _load(self, value):
        self.browser.setCurrentIndex(max(0, self.browser.findData(value("browser"))))
        self.browser_path.setFilePath(value("browser_path"))
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

    def _restore_defaults(self):
        self._load(lambda name: settings.DEFAULTS[name][0])

    def _browser_changed(self, *_):
        choice = self.browser.currentData()
        self.browser_path.setEnabled(choice == "custom")
        path = cdp.find_browser(choice, self.browser_path.filePath())
        self.browser_found.setText(f"Using: {path}" if path else
                                   "Not found: live sync and the target X won't be available")

    def _shortcuts(self):
        """QGIS's own Keyboard Shortcuts dialog, where the GEtools ones live."""
        from qgis.gui import QgsConfigureShortcutsDialog

        dialog = QgsConfigureShortcutsDialog(self)
        (getattr(dialog, "exec", None) or dialog.exec_)()

    def save(self):
        """Write the dialog's values to the settings store."""
        settings.set("browser", self.browser.currentData())
        settings.set("browser_path", self.browser_path.filePath())
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
