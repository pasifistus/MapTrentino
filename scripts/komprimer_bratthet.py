#!/usr/bin/env python3
"""Konverter et bratthetsraster til et ferdig fargelagt 8-bits COG-raster.

Leser klassegrenser og farger fra en diskret QGIS QML-stil. Resultatet bevarer
oppløsning og koordinatsystem, men lagrer fargeklasser i stedet for grader.
Krever numpy og GDALs Python-bindinger (tilgjengelig med /usr/bin/python3).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from osgeo import gdal


ROOT = Path(__file__).resolve().parents[1]


def read_style(path: Path) -> tuple[np.ndarray, list[tuple[int, int, int, int]], list[str]]:
    root = ET.parse(path).getroot()
    renderer = root.find(".//rasterrenderer")
    shader = root.find(".//colorrampshader")
    if (renderer is None or renderer.get("type") != "singlebandpseudocolor"
            or renderer.get("band") != "1" or renderer.get("opacity", "1") != "1"
            or shader is None or shader.get("colorRampType") != "DISCRETE"):
        raise ValueError("Stilen må bruke diskrete fargeklasser på bånd 1 med full lagopasitet.")
    if renderer.findall("./rasterTransparency/*"):
        raise ValueError("Ekstra transparensregler støttes ikke; bruk alfa i fargeklassene.")
    items = shader.findall("item")
    if not 1 <= len(items) <= 255:
        raise ValueError("Stilen må ha mellom 1 og 255 klasser.")
    bounds, colors, labels = [], [], []
    for item in items:
        bound = float(item.get("value", "nan"))
        color = item.get("color", "").removeprefix("#")
        alpha = int(item.get("alpha", "255"))
        if (not math.isfinite(bound) or len(color) != 6 or alpha not in (0, 255)
                or bounds and bound <= bounds[-1]):
            raise ValueError("Klassegrensene må være stigende, med RGB-farger og alfa 0 eller 255.")
        colors.append(tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,))
        bounds.append(bound)
        labels.append(item.get("label", str(bound)))
    return np.array(bounds), colors, labels


def classify(data: np.ndarray, valid: np.ndarray, bounds: np.ndarray,
             codes: np.ndarray) -> np.ndarray:
    # DISCRETE: hver klasse inkluderer sin øvre grense. Kode 0 er transparent.
    indices = np.searchsorted(bounds, data, side="left")
    indices[(~valid) | (~np.isfinite(data))] = len(bounds)
    return codes[indices]


def convert(source: Path, style: Path, output: Path) -> None:
    gdal.UseExceptions()
    source, style, output = source.resolve(), style.resolve(), output.resolve()
    if output in (source, style):
        raise ValueError("Utdata må være en egen fil.")
    if output.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("Utdata må være en .tif- eller .tiff-fil.")
    if output.exists():
        raise FileExistsError(f"Utdata finnes allerede: {output}. Velg et nytt filnavn.")
    bounds, colors, labels = read_style(style)
    palette = gdal.ColorTable()
    palette.SetColorEntry(0, (0, 0, 0, 0))
    codes = np.zeros(len(bounds) + 1, dtype=np.uint8)
    classes = {}
    for index, (color, label) in enumerate(zip(colors, labels)):
        if color[3]:
            code = len(classes) + 1
            codes[index] = code
            palette.SetColorEntry(code, color)
            classes[str(code)] = {"label": label, "rgba": color, "upper_bound": float(bounds[index])}

    incoming = gdal.Open(str(source), gdal.GA_ReadOnly)
    if incoming.RasterCount != 1:
        raise ValueError("Inndata må ha ett bånd med bratthet i grader.")
    if not incoming.GetProjection():
        raise ValueError("Inndata mangler koordinatsystem.")
    band = incoming.GetRasterBand(1)
    mask = band.GetMaskBand()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bratthet-", dir=output.parent) as temporary:
        stage_path = Path(temporary) / "klasser.tif"
        stage = gdal.GetDriverByName("GTiff").Create(
            str(stage_path), incoming.RasterXSize, incoming.RasterYSize, 1, gdal.GDT_Byte,
            options=["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "COMPRESS=DEFLATE"])
        stage.SetGeoTransform(incoming.GetGeoTransform())
        stage.SetProjection(incoming.GetProjection())
        stage.SetMetadataItem("VARSOM_CLASSES", json.dumps(classes, ensure_ascii=False))
        stage.SetMetadataItem("DESCRIPTION", "Fargeklasser fra QML; verdiene er klassekoder, ikke bratthetsgrader.")
        target = stage.GetRasterBand(1)
        target.SetNoDataValue(0)
        target.SetColorTable(palette)
        target.SetColorInterpretation(gdal.GCI_PaletteIndex)
        progress = -1
        for y in range(0, incoming.RasterYSize, 256):
            height = min(256, incoming.RasterYSize - y)
            data = band.ReadAsArray(0, y, incoming.RasterXSize, height)
            valid = mask.ReadAsArray(0, y, incoming.RasterXSize, height) != 0
            target.WriteArray(classify(data, valid, bounds, codes), 0, y)
            percent = (y + height) * 100 // incoming.RasterYSize
            if percent // 10 != progress:
                progress = percent // 10
                print(f"Klassifiserer: {percent}%", flush=True)
        target = None
        stage = None
        cog_path = Path(temporary) / "ferdig.tif"
        print("Komprimerer COG og lager oversiktsnivåer …", flush=True)
        cog = gdal.Translate(str(cog_path), str(stage_path), format="COG", creationOptions=[
            "COMPRESS=DEFLATE", "LEVEL=6", "BLOCKSIZE=512",
            "RESAMPLING=NEAREST", "OVERVIEW_RESAMPLING=NEAREST", "BIGTIFF=IF_SAFER"])
        cog = None
        check = gdal.Open(str(cog_path))
        if (check.RasterXSize != incoming.RasterXSize or check.RasterYSize != incoming.RasterYSize
                or check.GetGeoTransform() != incoming.GetGeoTransform()
                or check.GetRasterBand(1).GetNoDataValue() != 0
                or check.GetRasterBand(1).DataType != gdal.GDT_Byte
                or check.GetRasterBand(1).GetColorTable() is None):
            raise RuntimeError("Kontroll av ferdig raster feilet.")
        check = None
        cog_path.rename(output)
    incoming = None
    print(f"Lagret: {output}")
    print(f"{source.stat().st_size / 1024**2:.2f} MiB → {output.stat().st_size / 1024**2:.2f} MiB "
          f"({100 * (1 - output.stat().st_size / source.stat().st_size):.1f}% mindre)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "TrentinoData/avledet/helning_5x5.tif")
    parser.add_argument("--style", type=Path, default=ROOT / "TrentinoData/varsomfargerV2.qml")
    parser.add_argument("--output", type=Path, default=ROOT / "TrentinoData/avledet/helning_5x5_palett.tif")
    args = parser.parse_args()
    try:
        convert(args.input, args.style, args.output)
    except (OSError, ValueError, RuntimeError, ET.ParseError) as exc:
        print(f"Konvertering feilet: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
