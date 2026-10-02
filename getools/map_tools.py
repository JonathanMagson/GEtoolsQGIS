"""Click-on-the-map tools: OpenInGE, OpenInMaps, OpenInStreet."""

from qgis.PyQt.QtCore import Qt
from qgis.gui import QgsMapToolEmitPoint

from .geo import to_lat_lon


class PointPickTool(QgsMapToolEmitPoint):
    """Takes one left click, hands ``(lat, lon)`` to ``callback``, then hands
    the canvas back to whatever tool was active before (the ArcGIS add-in
    switched back to the Explore tool the same way)."""

    def __init__(self, canvas, action, callback, on_error):
        super().__init__(canvas)
        self.canvas = canvas
        self.setAction(action)
        self.callback = callback
        self.on_error = on_error
        self.previous_tool = None
        self.setCursor(Qt.CursorShape.CrossCursor if hasattr(Qt, "CursorShape") else Qt.CrossCursor)

    def activate(self):
        current = self.canvas.mapTool()
        if current is not self:
            self.previous_tool = current
        super().activate()

    def canvasReleaseEvent(self, event):
        left = Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton
        if event.button() != left:
            return
        crs = self.canvas.mapSettings().destinationCrs()
        lat_lon = to_lat_lon(event.mapPoint(), crs)
        if lat_lon is None:
            self.on_error("That point can't be converted to latitude/longitude.")
        else:
            self.callback(*lat_lon)
        self._restore_previous_tool()

    def _restore_previous_tool(self):
        try:
            if self.previous_tool is not None:
                self.canvas.setMapTool(self.previous_tool)
                return
        except RuntimeError:
            pass  # the previous tool has since been deleted
        self.canvas.unsetMapTool(self)
