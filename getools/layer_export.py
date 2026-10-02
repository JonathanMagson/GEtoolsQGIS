"""LayerToGE: write a vector layer to KML for importing into Google Earth Web."""

import os
import re
import time

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsVectorFileWriter,
)

from .geo import WGS84

KML_FOLDER_NAME = "OpenInGE_kml_temp"


def kml_folder():
    """Same folder the ArcGIS add-in used: ``~/Documents/OpenInGE_kml_temp``."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.path.expanduser("~")
    path = os.path.join(base, KML_FOLDER_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _clear_old_kml(folder):
    for name in os.listdir(folder):
        if name.lower().endswith(".kml"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass  # still open somewhere; leave it


def safe_name(text):
    return re.sub(r"[^\w\-]+", "_", text).strip("_") or "layer"


def export_layer_to_kml(layer, selected_only):
    """Write ``layer`` as WGS84 KML and return the file path.

    Raises ``RuntimeError`` with the writer's message when the export fails.
    """
    folder = kml_folder()
    _clear_old_kml(folder)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, f"{safe_name(layer.name())}_{stamp}.kml")

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "KML"
    options.fileEncoding = "UTF-8"
    options.layerName = layer.name()
    options.onlySelectedFeatures = selected_only
    options.ct = _transform(layer)
    # Use the layer's labelling field (or first field) as the placemark name.
    name_field = _name_field(layer)
    if name_field:
        options.datasourceOptions = [f"NameField={name_field}"]

    context = QgsProject.instance().transformContext()
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
    else:
        result = QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, context, options)
    error, message = result[0], result[1]
    if error != QgsVectorFileWriter.NoError:
        raise RuntimeError(message or f"KML export failed (error {error})")
    return path


def _transform(layer):
    return QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance())


def _name_field(layer):
    fields = layer.fields()
    if layer.labelsEnabled() and layer.labeling() is not None:
        try:
            label_field = layer.labeling().settings().fieldName
            if fields.indexOf(label_field) >= 0:
                return label_field
        except AttributeError:
            pass  # rule-based labelling has no single field
    display = layer.displayField()
    if display and fields.indexOf(display) >= 0:
        return display
    return fields[0].name() if fields.count() else None
