"""Turn QGIS canvas state into WGS84 coordinates and a Google Earth camera."""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsDistanceArea,
    QgsPointXY,
    QgsProject,
)

from .google_urls import heading_from_rotation, range_for_ground_height

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


def _to_wgs84(crs):
    return QgsCoordinateTransform(crs, WGS84, QgsProject.instance())


def to_lat_lon(point, crs):
    """Project ``point`` in ``crs`` to ``(lat, lon)``; ``None`` if it can't."""
    try:
        p = _to_wgs84(crs).transform(QgsPointXY(point))
    except QgsCsException:
        return None
    if not (-90.0 <= p.y() <= 90.0 and -180.0 <= p.x() <= 180.0):
        return None
    return p.y(), p.x()


def canvas_camera(canvas):
    """Google Earth camera matching the canvas: ``(lat, lon, range_m, heading)``.

    Returns ``None`` when the canvas centre can't be projected to WGS84.
    """
    settings = canvas.mapSettings()
    crs = settings.destinationCrs()
    center = canvas.center()
    centre_ll = to_lat_lon(center, crs)
    if centre_ll is None:
        return None

    # Ground distance covered by the canvas height. Map units per pixel don't
    # depend on rotation, so measure straight up from the centre.
    half_h = settings.mapUnitsPerPixel() * settings.outputSize().height() / 2.0
    top_ll = to_lat_lon(QgsPointXY(center.x(), center.y() + half_h), crs)
    bottom_ll = to_lat_lon(QgsPointXY(center.x(), center.y() - half_h), crs)

    da = QgsDistanceArea()
    da.setEllipsoid("WGS84")
    if top_ll and bottom_ll:
        ground_h = da.measureLine(
            QgsPointXY(top_ll[1], top_ll[0]), QgsPointXY(bottom_ll[1], bottom_ll[0])
        )
    else:
        ground_h = 20_000_000.0  # zoomed out past the poles: show the globe

    lat, lon = centre_ll
    return lat, lon, range_for_ground_height(ground_h), heading_from_rotation(canvas.rotation())


def extent_camera(extent, crs):
    """``(lat, lon, range_m)`` framing a rectangle, for zooming to a layer."""
    centre = to_lat_lon(extent.center(), crs)
    top = to_lat_lon(QgsPointXY(extent.center().x(), extent.yMaximum()), crs)
    bottom = to_lat_lon(QgsPointXY(extent.center().x(), extent.yMinimum()), crs)
    left = to_lat_lon(QgsPointXY(extent.xMinimum(), extent.center().y()), crs)
    right = to_lat_lon(QgsPointXY(extent.xMaximum(), extent.center().y()), crs)
    if centre is None:
        return None

    da = QgsDistanceArea()
    da.setEllipsoid("WGS84")

    def dist(a, b):
        if a is None or b is None:
            return 0.0
        return da.measureLine(QgsPointXY(a[1], a[0]), QgsPointXY(b[1], b[0]))

    # Pad by 20% and don't let a single point zoom in to street level.
    span = max(dist(top, bottom), dist(left, right), 500.0) * 1.2
    return centre[0], centre[1], range_for_ground_height(span)
