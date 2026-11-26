#!/usr/bin/env python3
"""Render native-resolution DEM steepness as an 8-bit grayscale GeoTIFF.

Run with /usr/bin/python3 scripts/lag_hillshade.py. Slope is calculated
directly on the original grid, without smoothing, resampling or lighting.
Flat ground is light gray; the maximum valid slope in the map is dark gray.
Output is a single 8-bit gray band plus an internal validity mask, compressed
as a Cloud Optimized GeoTIFF with lossless DEFLATE level 6 and 512-pixel tiles.
Requires GDAL and NumPy. Keep the generated QML beside the TIFF in QGIS.
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
DEFAULT_INPUT = ROOT / "ChimaTose/temp/terrain/terrain_0p5m.vrt"
DEFAULT_OUTPUT = ROOT / "ChimaTose/mapdata/hillshade_osmand.tif"
DARK_GRAY = 60
LIGHT_GRAY = 230
NODATA = -9999.0
COG_OPTIONS = [
    "COMPRESS=DEFLATE", "LEVEL=6", "PREDICTOR=2", "BLOCKSIZE=512",
    "OVERVIEW_RESAMPLING=AVERAGE", "BIGTIFF=YES",
]


def validate_input(dataset: gdal.Dataset) -> None:
    if dataset.RasterCount != 1:
        raise ValueError("Input must be a single-band elevation raster in metres.")
    crs = dataset.GetSpatialRef()
    if crs is None or not crs.IsProjected() or not math.isclose(crs.GetLinearUnits(), 1.0):
        raise ValueError("Input must have a projected CRS with coordinates in metres.")
    transform = dataset.GetGeoTransform(can_return_null=True)
    if (transform is None or transform[1] <= 0 or transform[5] >= 0
            or transform[2] != 0 or transform[4] != 0):
        raise ValueError("Input must be a north-up georeferenced elevation raster.")
    band = dataset.GetRasterBand(1)
    if gdal.DataTypeIsComplex(band.DataType):
        raise ValueError("Complex-valued elevation rasters are not supported.")
    if band.GetUnitType().lower() not in ("", "m", "metre", "metres", "meter", "meters"):
        raise ValueError("Elevation values must be in metres (empty unit metadata assumes metres).")


def valid_values(band: gdal.Band, values: np.ndarray, y: int) -> np.ndarray:
    """Respect both explicit masks and NoData, including NaN/Inf elevations."""
    valid = (band.GetMaskBand().ReadAsArray(0, y, values.shape[1], values.shape[0]) != 0)
    valid &= np.isfinite(values)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= values != nodata
    return valid


def write_style(output: Path) -> None:
    """Write a minimal QML so QGIS displays the baked grayscale unchanged."""
    root = ET.Element("qgis", version="3.40", styleCategories="Symbology|Rendering")
    pipe = ET.SubElement(root, "pipe")
    # QGIS exposes the internal GDAL mask as a virtual second (alpha) band.
    renderer = ET.SubElement(pipe, "rasterrenderer", {
        "type": "singlebandgray", "grayBand": "1", "alphaBand": "2",
        "gradient": "BlackToWhite", "opacity": "1", "nodataColor": "",
    })
    ET.SubElement(renderer, "rasterTransparency")
    enhancement = ET.SubElement(renderer, "contrastEnhancement")
    for key, value in (("minValue", "0"), ("maxValue", "255"), ("algorithm", "NoEnhancement")):
        ET.SubElement(enhancement, key).text = value
    ET.SubElement(pipe, "brightnesscontrast", brightness="0", contrast="0", gamma="1")
    ET.SubElement(pipe, "rasterresampler", {
        "zoomedInResampler": "cubic", "zoomedOutResampler": "bilinear", "maxOversampling": "2",
    })
    ET.SubElement(pipe, "resamplingStage").text = "resamplingFilter"
    ET.SubElement(root, "blendMode").text = "0"
    ET.indent(root)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def gray_slope(slope: np.ndarray, maximum: float, dark_gray: int, light_gray: int) -> np.ndarray:
    if maximum <= 0:
        return np.full(slope.shape, light_gray, dtype=np.uint8)
    relative = np.clip(np.asarray(slope, dtype=np.float64) / maximum, 0, 1)
    # Round towards light gray, reserving the darkest tone for the maximum.
    gray = light_gray - np.floor((light_gray - dark_gray) * relative + 1e-12)
    return np.clip(gray, dark_gray, light_gray).astype(np.uint8)


def slope_blocks(elevation: gdal.Dataset, slope: gdal.Dataset):
    dem_band, slope_band = elevation.GetRasterBand(1), slope.GetRasterBand(1)
    for y in range(0, elevation.RasterYSize, 256):
        height = min(256, elevation.RasterYSize - y)
        dem_data = dem_band.ReadAsArray(0, y, elevation.RasterXSize, height)
        slope_data = slope_band.ReadAsArray(0, y, elevation.RasterXSize, height)
        valid = valid_values(dem_band, dem_data, y) & valid_values(slope_band, slope_data, y)
        yield y, slope_data, valid


def generate(source: Path, output: Path, *, dark_gray: int = DARK_GRAY,
             light_gray: int = LIGHT_GRAY, overwrite: bool = False) -> None:
    gdal.UseExceptions()
    if (any(isinstance(value, bool) or not isinstance(value, int) for value in (dark_gray, light_gray))
            or not 0 <= dark_gray < light_gray <= 255):
        raise ValueError("Gray endpoints must be integers: 0 <= dark-gray < light-gray <= 255.")
    source, output = source.resolve(), output.resolve()
    style, auxiliary = output.with_suffix(".qml"), Path(str(output) + ".aux.xml")
    targets = (output, style, auxiliary)
    if output.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("Output must be a .tif or .tiff file.")
    if not source.is_file():
        raise FileNotFoundError(f"Elevation input not found: {source}")
    if source in targets:
        raise ValueError("Output must not replace an input file.")
    for path in targets:
        if path.exists() and not overwrite:
            raise FileExistsError(f"Output already exists: {path}. Use --overwrite to replace it.")
    incoming = gdal.Open(str(source), gdal.GA_ReadOnly)
    validate_input(incoming)
    if min(incoming.RasterXSize, incoming.RasterYSize) < 3:
        raise ValueError("The elevation grid must be at least 3 by 3 pixels.")
    if any(Path(path).resolve() in targets for path in incoming.GetFileList()):
        raise ValueError("Output must not replace an input VRT dependency.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="steepness-gray-", dir=output.parent) as folder:
        temporary = Path(folder)
        width, height = incoming.RasterXSize, incoming.RasterYSize
        transform = incoming.GetGeoTransform()
        print(f"Reading original {width} × {height} grid ({transform[1]:g} × {-transform[5]:g} m pixels) …", flush=True)
        # Copy pixel values unchanged, merging masks/NoData/nonfinite values.
        # No warp, smoothing or change to the source's grid takes place.
        elevation = gdal.GetDriverByName("GTiff").Create(
            str(temporary / "elevation.tif"), width, height, 1, gdal.GDT_Float64,
            options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=YES"],
        )
        elevation.SetGeoTransform(transform)
        elevation.SetProjection(incoming.GetProjection())
        source_band, dem_band = incoming.GetRasterBand(1), elevation.GetRasterBand(1)
        dem_band.SetNoDataValue(NODATA)
        for y in range(0, height, 256):
            data = source_band.ReadAsArray(0, y, width, min(256, height - y))
            valid = valid_values(source_band, data, y)
            dem_band.WriteArray(np.where(valid, data, NODATA), 0, y)
        source_band = dem_band = None
        elevation.FlushCache()
        print("Calculating native-resolution slope in degrees (Horn, metre scale 1) …", flush=True)
        slope = gdal.DEMProcessing(
            str(temporary / "slope.tif"), elevation, "slope", scale=1,
            alg="Horn", computeEdges=True,
            creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=YES"],
        )
        maximum, valid_count = 0.0, 0
        for _, values, valid in slope_blocks(elevation, slope):
            valid_count += int(valid.sum())
            if valid.any():
                maximum = max(maximum, float(values[valid].max()))
        if not valid_count:
            raise ValueError("No valid elevation pixels remain.")
        print(f"Maximum slope: {maximum:.5f}°. Grading from {light_gray} to {dark_gray} …", flush=True)
        stage = gdal.GetDriverByName("GTiff").Create(
            str(temporary / "gray.tif"), width, height, 1, gdal.GDT_Byte,
            options=["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "COMPRESS=DEFLATE",
                     "ZLEVEL=6", "PREDICTOR=2", "BIGTIFF=YES"],
        )
        stage.SetGeoTransform(transform)
        stage.SetProjection(incoming.GetProjection())
        stage.SetMetadataItem("ELEVATION_SOURCE", str(source))
        stage.SetMetadataItem("STEEPNESS_PARAMETERS", json.dumps({
            "algorithm": "Horn", "scale": 1, "resolution": "native",
            "smoothing": False, "dark_gray": dark_gray, "light_gray": light_gray,
            "maximum_slope_degrees": maximum, "valid_pixels": valid_count,
            "normalization": "zero to maximum valid slope over the complete output grid",
        }))
        stage.SetMetadataItem("STEEPNESS_COMPRESSION", json.dumps({
            "format": "COG", "compression": "DEFLATE", "level": 6, "block_size": 512,
            "predictor": 2, "overview_resampling": "AVERAGE", "mask": "internal",
        }))
        gray_band = stage.GetRasterBand(1)
        gray_band.SetColorInterpretation(gdal.GCI_GrayIndex)
        gray_band.SetDescription("Steepness rendered as gray intensity, not degrees")
        # A lossless one-bit mask preserves transparency without an alpha band
        # or reserving a gray value, so all 256 tones remain available if wanted.
        with gdal.config_option("GDAL_TIFF_INTERNAL_MASK", "YES"):
            gray_band.CreateMaskBand(gdal.GMF_PER_DATASET)
        mask_band = gray_band.GetMaskBand()
        for y, values, valid in slope_blocks(elevation, slope):
            gray = gray_slope(np.where(valid, values, 0), maximum, dark_gray, light_gray)
            gray[~valid] = light_gray
            gray_band.WriteArray(gray, 0, y)
            mask_band.WriteArray(valid.astype(np.uint8) * 255, 0, y)
        stage.FlushCache()
        finished_path = temporary / "finished.tif"
        print("Compressing COG with DEFLATE level 6, 512-pixel tiles and overview levels …", flush=True)
        finished = gdal.Translate(str(finished_path), stage, format="COG", creationOptions=COG_OPTIONS)
        if (finished.RasterXSize != width or finished.RasterYSize != height
                or finished.GetGeoTransform() != transform or finished.RasterCount != 1
                or finished.GetRasterBand(1).DataType != gdal.GDT_Byte
                or finished.GetRasterBand(1).GetMaskFlags() != gdal.GMF_PER_DATASET
                or finished.GetMetadataItem("COMPRESSION", "IMAGE_STRUCTURE") != "DEFLATE"
                or any(path.endswith(".msk") for path in finished.GetFileList())):
            raise RuntimeError("Finished raster failed its grid/type/mask/compression check.")
        style_path = temporary / "finished.qml"
        write_style(style_path)
        uncompressed_size = width * height  # One Byte per pixel, before masks and overviews.
        gray_band = mask_band = None
        finished = stage = slope = elevation = None
        finished_path.replace(output)
        style_path.replace(style)
        auxiliary.unlink(missing_ok=True)
    incoming = None
    print(f"Saved: {output}\nQGIS style: {style}", flush=True)
    print(f"Uncompressed gray pixels: {uncompressed_size / 1024**2:.2f} MiB; "
          f"COG including mask/overviews: {output.stat().st_size / 1024**2:.2f} MiB", flush=True)


def main(*, default_output: Path = DEFAULT_OUTPUT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Original projected DEM in metres")
    parser.add_argument("--output", type=Path, default=default_output, help="Compressed 8-bit grayscale GeoTIFF")
    parser.add_argument("--dark-gray", type=int, default=DARK_GRAY, help="Gray value for the steepest slope")
    parser.add_argument("--light-gray", type=int, default=LIGHT_GRAY, help="Gray value for flat ground")
    parser.add_argument("--overwrite", action="store_true", help="Replace the selected output and its QML")
    args = parser.parse_args()
    try:
        generate(args.input, args.output, dark_gray=args.dark_gray,
                 light_gray=args.light_gray, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Steepness generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
