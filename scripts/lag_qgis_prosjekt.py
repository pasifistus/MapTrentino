#!/usr/bin/python3
"""Åpne definerte lag, bruk QML-stiler og lagre et QGIS-prosjekt.

Kjør med /usr/bin/python3 (QGIS-bindinger). Laglisten defineres i JSON;
alle datastier i listen regnes fra JSON-filens mappe. Prosjektet kan deretter
åpnes i QGIS eller pakkes med pakk_qfield.py --all-layers.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name("qgis_lag.json")


def build_project(config_path: Path, output: Path, *, overwrite: bool = False) -> int:
    """Bygg prosjektet i en initialisert QgsApplication. Returner antall lag."""
    from qgis.core import (
        Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
        QgsLineSymbol, QgsMapLayerType, QgsProject, QgsRasterLayer,
        QgsReferencedRectangle, QgsVectorLayer, QgsWkbTypes,
    )

    config_path = config_path.resolve()
    output = output.resolve()
    if output.suffix.lower() != ".qgz":
        raise ValueError("Prosjektfilen må ha endelsen .qgz.")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Prosjektet finnes allerede: {output}. Bruk --overwrite for å erstatte det.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Laglisten må være et JSON-objekt.")
    specs = config.get("layers")
    if not isinstance(specs, list) or not specs:
        raise ValueError("Laglisten må inneholde en ikke-tom liste kalt layers.")

    crs = QgsCoordinateReferenceSystem(config.get("crs", "EPSG:7791"))
    if not crs.isValid():
        raise ValueError("Ugyldig prosjektkoordinatsystem.")
    project = QgsProject()
    project.setCrs(crs)
    project.setTitle(config.get("title", output.stem))
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    extent = None
    try:
        for number, spec in enumerate(specs, 1):
            if not isinstance(spec, dict) or not all(
                isinstance(spec.get(key), str) and spec[key].strip()
                for key in ("name", "source", "type")
            ):
                raise ValueError(f"Lag {number} må ha name, source og type som tekst.")
            filename, separator, options = spec["source"].partition("|")
            source = (config_path.parent / filename).resolve()
            if output in (source, config_path):
                raise ValueError("Prosjektfilen kan ikke erstatte en inndatafil.")
            if not source.is_file():
                raise FileNotFoundError(f"Mangler datakilde for {spec['name']}: {source}")
            uri = f"{source}{separator}{options}"
            if spec["type"] == "raster":
                layer = QgsRasterLayer(uri, spec["name"], "gdal")
            elif spec["type"] == "vector":
                layer = QgsVectorLayer(uri, spec["name"], "ogr")
            else:
                raise ValueError(f"Ukjent lagtype: {spec['type']}. Bruk raster eller vector.")
            if not layer.isValid() or not layer.crs().isValid():
                raise ValueError(f"Kan ikke åpne laget med gyldig koordinatsystem: {uri}")

            if "line_symbol" in spec:
                if (layer.type() != QgsMapLayerType.VectorLayer
                        or layer.geometryType() != QgsWkbTypes.LineGeometry):
                    raise ValueError(f"line_symbol krever et linjelag: {spec['name']}")
                layer.renderer().setSymbol(QgsLineSymbol.createSimple(spec["line_symbol"]))
            if "style" in spec:
                style = (config_path.parent / spec["style"]).resolve()
                if not style.is_file():
                    raise FileNotFoundError(f"Mangler stilfil: {style}")
                message, ok = layer.loadNamedStyle(str(style))
                if not ok:
                    raise ValueError(f"Kan ikke laste stilen for {spec['name']}: {message}")

            # Legg til i oppgitt rekkefølge: første lag i listen ligger øverst.
            project.addMapLayer(layer, False)
            node = project.layerTreeRoot().addLayer(layer)
            node.setItemVisibilityChecked(True)
            bounds = QgsCoordinateTransform(layer.crs(), crs, project).transformBoundingBox(layer.extent())
            if extent is None:
                extent = bounds
            else:
                extent.combineExtentWith(bounds)
            print(f"Åpnet {spec['name']}" + (f" med stil {style.name}" if "style" in spec else ""), flush=True)

        project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, crs))
        output.parent.mkdir(parents=True, exist_ok=True)
        # QGIS lagrer renderer og farger i prosjektet; QML-filen trengs ikke i QField-pakken.
        if not project.write(str(output)):
            raise OSError(f"Kunne ikke lagre prosjektet: {output}")
        return len(specs)
    finally:
        project.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="JSON-fil med lag (standard: scripts/qgis_lag.json)")
    parser.add_argument("--output", type=Path, default=ROOT / "montebondone_auto.qgz",
                        help="Prosjektfil som skal lagres (.qgz)")
    parser.add_argument("--overwrite", action="store_true", help="Erstatt eksisterende prosjektfil")
    args = parser.parse_args()

    # Kjør også uten et åpent QGIS-vindu eller en skjermtilkobling.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from qgis.core import QgsApplication
    except ImportError:
        parser.exit(1, "Mangler QGIS Python-bindinger. Kjør med /usr/bin/python3.\n")
    app = QgsApplication([], False)
    app.initQgis()
    try:
        count = build_project(args.config, args.output, overwrite=args.overwrite)
        print(f"Lagret {count} lag i {args.output.resolve()}")
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(f"Kunne ikke bygge QGIS-prosjektet: {exc}", file=sys.stderr)
        return 1
    finally:
        app.exitQgis()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
