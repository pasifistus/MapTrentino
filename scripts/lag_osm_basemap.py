#!/usr/bin/python3
"""Style a local OsmAnd OSM extract as a reusable offline QGIS basemap.

Run with the system Python (QGIS bindings):
    QT_QPA_PLATFORM=offscreen /usr/bin/python3 scripts/lag_osm_basemap.py

The original .osm.pbf remains the complete source, including hiking relations.
The GeoPackage contains points, lines and polygons for basemap display.
"""

from pathlib import Path
import os
import sqlite3
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SQLITE_TMPDIR", "/tmp")

from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFillSymbol, QgsLayerDefinition, QgsLineSymbol, QgsMapRendererParallelJob,
    QgsMapSettings, QgsMarkerSymbol, QgsPalLayerSettings, QgsProject,
    QgsRectangle, QgsReferencedRectangle, QgsRuleBasedLabeling, QgsRuleBasedRenderer, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QFont


BASE = Path(__file__).resolve().parents[1] / "TrentinoData/basemap"
DATA = BASE / "trentino_osm.gpkg"
ATTRIBUTION = "© OpenStreetMap contributors — https://www.openstreetmap.org/copyright"


def rule(parent, symbol, expression, name, zoom_out_limit=0):
    child = QgsRuleBasedRenderer.Rule(symbol, filterExp=expression, label=name)
    child.setMinimumScale(zoom_out_limit)
    parent.appendChild(child)


def polygon(parent, expression, color, name, limit=0, outline=None):
    symbol = QgsFillSymbol.createSimple({
        "color": color, "outline_style": "solid" if outline else "no",
        "outline_color": outline or color, "outline_width": "0.12",
    })
    rule(parent, symbol, expression, name, limit)


def line(parent, expression, color, width, name, limit=0, dashed=False):
    symbol = QgsLineSymbol.createSimple({
        "line_color": color, "line_width": str(width),
        "line_style": "dash" if dashed else "solid", "joinstyle": "round",
    })
    rule(parent, symbol, expression, name, limit)


def labels(parent, expression, limit, size=9, color="#343b35", line_labels=False):
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = (Qgis.LabelPlacement.Curved if line_labels
                          else Qgis.LabelPlacement.OrderedPositionsAroundPoint)
    settings.dist = 1.5
    text = QgsTextFormat()
    text.setFont(QFont("DejaVu Sans"))
    text.setSize(size)
    text.setColor(QColor(color))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor("#fffef9"))
    text.setBuffer(buffer)
    settings.setFormat(text)
    child = QgsRuleBasedLabeling.Rule(settings, filterExp=expression)
    child.setMinimumScale(limit)
    parent.appendChild(child)


def layer(table, name):
    options = QgsVectorLayer.LayerOptions()
    options.loadDefaultStyle = False
    result = QgsVectorLayer(f"{DATA}|layername={table}", name, "ogr", options)
    if not result.isValid():
        raise RuntimeError(f"Invalid layer: {table}")
    result.setAttribution(ATTRIBUTION)
    result.setAttributionUrl("https://www.openstreetmap.org/copyright")
    metadata = result.metadata()
    metadata.setTitle(name)
    metadata.setRights([ATTRIBUTION])
    metadata.setLicenses(["ODbL 1.0"])
    metadata.setAbstract("Offline OpenStreetMap data for Trentino–Alto Adige, "
                         "downloaded from OsmAnd's public source extract. "
                         "Cartography is a local basic style, not OsmAnd rendering.")
    result.setMetadata(metadata)
    return result


def main():
    if not DATA.is_file():
        subprocess.run([
            "ogr2ogr", "-f", "GPKG", str(DATA),
            str(BASE / "source/trentino-alto-adige.osm.pbf"),
            "points", "lines", "multipolygons", "-t_srs", "EPSG:7791",
            "-gt", "65536", "-dsco", "VERSION=1.3",
        ], check=True)

    app = QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:7791"))
    project.setTitle("Trentino–Alto Adige — offline OpenStreetMap basemap")
    project.setBackgroundColor(QColor("#f4f1e9"))
    project.writeEntry("Gui", "/CanvasColorRedPart", 244)
    project.writeEntry("Gui", "/CanvasColorGreenPart", 241)
    project.writeEntry("Gui", "/CanvasColorBluePart", 233)
    project.writeEntry("CopyrightLabel", "/Enabled", True)
    project.writeEntry("CopyrightLabel", "/Label", ATTRIBUTION)

    points = layer("points", "OSM places, peaks and mountain huts")
    polygons = layer("multipolygons", "OSM land, water and buildings")
    lines = layer("lines", "OSM roads, paths, streams and lifts")
    polygons.setSubsetString('"boundary" IS NULL AND ("landuse" IS NOT NULL '
                             'OR "natural" IS NOT NULL OR "building" IS NOT NULL '
                             'OR "leisure" IS NOT NULL)')
    root = QgsRuleBasedRenderer.Rule(None)
    polygon(root, '"landuse" IN (\'residential\',\'commercial\',\'retail\',\'industrial\',\'farmyard\')', "#e6e2db", "Settlements")
    polygon(root, '"landuse" IN (\'farmland\',\'vineyard\',\'orchard\')', "#e6e8c4", "Agriculture")
    polygon(root, '"landuse" IN (\'meadow\',\'grass\',\'pasture\') OR "natural"=\'grassland\'', "#dce8c9", "Grass and meadows")
    polygon(root, '"landuse"=\'forest\' OR "natural"=\'wood\'', "#c5d9b6", "Forest")
    polygon(root, '"natural" IN (\'scrub\',\'heath\')', "#d3dfbd", "Scrub and heath")
    polygon(root, '"leisure" IN (\'park\',\'garden\',\'golf_course\')', "#c9e4ba", "Parks")
    polygon(root, '"natural" IN (\'bare_rock\',\'scree\',\'shingle\')', "#dddcd5", "Rock and scree")
    polygon(root, '"natural"=\'glacier\'', "#e2f1f5", "Glaciers")
    polygon(root, '"natural"=\'wetland\'', "#b9d9cf", "Wetland")
    polygon(root, '"natural"=\'water\' OR "landuse" IN (\'reservoir\',\'basin\')', "#a8d0df", "Water")
    polygon(root, '"building" IS NOT NULL', "#cbbdb0", "Buildings", 25000, "#b3a292")
    polygons.setRenderer(QgsRuleBasedRenderer(root))

    lines.setSubsetString('"highway" IS NOT NULL OR "waterway" IS NOT NULL '
                          'OR "railway" IS NOT NULL OR "aerialway" IS NOT NULL')
    root = QgsRuleBasedRenderer.Rule(None)
    line(root, '"waterway" IN (\'river\',\'canal\')', "#7eb4cd", 0.35, "Rivers", 2000000)
    line(root, '"waterway" IN (\'stream\',\'ditch\',\'drain\')', "#8dbcd0", 0.18, "Streams", 100000)
    line(root, '"railway"=\'rail\'', "#777777", 0.25, "Railway", 500000, True)
    line(root, '"highway" IN (\'path\',\'footway\',\'steps\',\'bridleway\',\'via_ferrata\')', "#9b7255", 0.22, "Paths", 50000, True)
    line(root, '"highway"=\'cycleway\'', "#628aa8", 0.24, "Cycleways", 60000, True)
    line(root, '"highway"=\'track\'', "#ad946a", 0.3, "Tracks", 100000, True)
    line(root, '"highway" IN (\'service\',\'residential\',\'unclassified\',\'living_street\',\'pedestrian\')', "#a5a29a", 0.32, "Local roads", 180000)
    line(root, '"highway" IN (\'tertiary\',\'tertiary_link\')', "#d4b268", 0.45, "Tertiary roads", 600000)
    line(root, '"highway" IN (\'secondary\',\'secondary_link\')', "#e4ad62", 0.55, "Secondary roads", 1500000)
    line(root, '"highway" IN (\'primary\',\'primary_link\',\'trunk\',\'trunk_link\')', "#e29a67", 0.65, "Main roads", 3000000)
    line(root, '"highway" IN (\'motorway\',\'motorway_link\')', "#d87979", 0.8, "Motorways", 5000000)
    line(root, '"aerialway" IS NOT NULL', "#8a7193", 0.22, "Lifts", 100000)
    lines.setRenderer(QgsRuleBasedRenderer(root))
    root_labels = QgsRuleBasedLabeling.Rule(None)
    labels(root_labels, '"name" IS NOT NULL AND "highway" IS NOT NULL', 18000, 7, line_labels=True)
    lines.setLabeling(QgsRuleBasedLabeling(root_labels))
    lines.setLabelsEnabled(True)

    points.setSubsetString('"name" IS NOT NULL')
    peak = 'hstore_to_map("other_tags")[\'natural\']=\'peak\''
    hut = 'hstore_to_map("other_tags")[\'tourism\'] IN (\'alpine_hut\',\'wilderness_hut\')'
    root = QgsRuleBasedRenderer.Rule(None)
    rule(root, QgsMarkerSymbol.createSimple({"name": "triangle", "color": "#706b60", "outline_style": "no", "size": "1.8"}), peak, "Peaks", 100000)
    rule(root, QgsMarkerSymbol.createSimple({"name": "square", "color": "#a56749", "outline_style": "no", "size": "1.6"}), hut, "Mountain huts", 100000)
    points.setRenderer(QgsRuleBasedRenderer(root))
    root_labels = QgsRuleBasedLabeling.Rule(None)
    labels(root_labels, '"place"=\'city\'', 5000000, 13)
    labels(root_labels, '"place"=\'town\'', 1200000, 11)
    labels(root_labels, '"place"=\'village\'', 250000, 9)
    labels(root_labels, '"place" IN (\'hamlet\',\'suburb\',\'neighbourhood\')', 70000, 8)
    labels(root_labels, peak, 60000, 8, "#655e51")
    labels(root_labels, hut, 60000, 8, "#80523d")
    points.setLabeling(QgsRuleBasedLabeling(root_labels))
    points.setLabelsEnabled(True)

    group = project.layerTreeRoot().addGroup("Offline OpenStreetMap — Trentino–Alto Adige")
    for item in (points, lines, polygons):
        project.addMapLayer(item, False)
        group.addLayer(item)
        table = item.source().split("layername=", 1)[1].split("|", 1)[0]
        message, ok = item.saveNamedStyle(str(BASE / f"trentino_osm_{table}.qml"))
        if not ok:
            raise RuntimeError(message)

    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), project.crs(), project)
    extent = transform.transformBoundingBox(QgsRectangle(10.4, 45.6, 12.1, 46.6))
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, project.crs()))
    assert project.write(str(BASE / "trentino_basemap.qgz"))
    ok, error = QgsLayerDefinition.exportLayerDefinition(str(BASE / "trentino_basemap.qlr"), [group], Qgis.FilePathType.Relative)
    if not ok:
        raise RuntimeError(error)

    settings = QgsMapSettings()
    settings.setDestinationCrs(project.crs())
    settings.setLayers([points, lines, polygons])
    settings.setBackgroundColor(QColor("#f4f1e9"))
    settings.setOutputSize(QSize(1300, 1000))
    settings.setExtent(QgsRectangle(654000, 5092000, 663500, 5104500))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    if job.errors():
        raise RuntimeError(str([(e.layerID, e.message) for e in job.errors()]))
    assert job.renderedImage().save(str(BASE / "preview_montebondone.png"))

    # Embed the defaults together after rendering, without changing the schema
    # while QGIS providers are still being initialized and reading features.
    with sqlite3.connect(DATA) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS layer_styles (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            f_table_catalog TEXT, f_table_schema TEXT, f_table_name TEXT,
            f_geometry_column TEXT, styleName TEXT, styleQML TEXT, styleSLD TEXT,
            useAsDefault BOOLEAN, description TEXT, owner TEXT, ui TEXT,
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        for table in ("points", "lines", "multipolygons"):
            connection.execute("DELETE FROM layer_styles WHERE f_table_name=? AND styleName=?",
                               (table, "trentino_basemap"))
            connection.execute("""INSERT INTO layer_styles
                (f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                 styleName, styleQML, styleSLD, useAsDefault, description, owner, ui)
                 VALUES ('', '', ?, 'geom', 'trentino_basemap', ?, '', 1,
                         'Basic offline OSM basemap', '', '')""",
                               (table, (BASE / f"trentino_osm_{table}.qml").read_text()))
    print("Created offline basemap project, reusable layer group, default GeoPackage styles and preview.")


if __name__ == "__main__":
    main()
