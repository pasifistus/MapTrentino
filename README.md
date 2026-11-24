# Trentino / Monte Bondone maps

Commands used or shared in this chat; requires GDAL, Python 3 and QGIS Python bindings for the basemap script.

Run from the project directory.

```bash
cd /home/olav/kartTrento
```

The existing input `TrentinoData/avledet/terreng_05m_5x5.tif` is a smoothed 0.5 m DEM; its preparation commands were not recorded in this chat.

## Tests

Tests are collected in `tests/`. Run them from the project directory with the
scripts on Python's import path (requires GDAL and NumPy):

```bash
PYTHONPATH=scripts /usr/bin/python3 -m unittest discover -s tests -v
```

## LiDAR download

Generate a tile list from two opposite corners in WGS84, using latitude then
longitude in decimal degrees for each corner:

```bash
python3 scripts/lag_kartliste.py 45.9686 10.9962 46.0707 11.1058 \
  --output TrentinoData/kartfiler_nytt_rektangel.txt
```

Requires GDAL's Python bindings (`osgeo`; available with `/usr/bin/python3` in
this project). The script iterates over 500 m tiles in EPSG:7791 and includes
every available tile that overlaps the geographic rectangle. Either corner order
works. The script reads the server directory and discards filenames absent from
it, including tiles outside the dataset's coverage, reporting how many were
discarded. This requires network access; if the directory cannot be read, the
script fails without writing a list. Use `--url` to select another directory
(use the same URL with the downloader). Without `--output`, the list is printed
to standard output. Use the generated path with `lastned.py --file-list` to
download that selection. A rectangle entirely outside coverage produces a list
with zero filenames; there is nothing to download.

Download the terrain tiles listed in the project's rectangle selection.

```bash
python3 scripts/lastned.py \
  --file-list TrentinoData/kartfiler_rektangel.txt \
  --output TrentinoData/trentino_lidar_asc \
  --download --yes
```

## Contours

Generate 5 m contours with elevation stored in `ELEV`.

```bash
gdal_contour -i 5 -a ELEV -f GPKG \
  TrentinoData/avledet/terreng_05m_5x5.tif \
  TrentinoData/avledet/hoydekurver_5m_ny.gpkg
```

Generate the V2 contour intervals using the same method and the `contour` layer name required below.

```bash
for interval in 2.5 10 25 100; do
  gdal_contour -i "$interval" -a ELEV -f GPKG -nln contour \
    TrentinoData/avledet/terreng_05m_5x5.tif \
    "TrentinoData/avledet/hoydekurver_${interval}m.gpkg"
done
```

Simplify the 2.5 m contour geometry with 0.25 m horizontal tolerance while retaining elevation attributes.

```bash
ogr2ogr -f GPKG \
  TrentinoData/avledet/hoydekurver_2.5m_simplified.gpkg \
  TrentinoData/avledet/hoydekurver_2.5m.gpkg \
  contour -simplify 0.25
```

Simplify the 10, 25 and 100 m contours using the same horizontal tolerance.

```bash
for interval in 10 25 100; do
  ogr2ogr -f GPKG \
    "TrentinoData/avledet/hoydekurver_${interval}m_simplified.gpkg" \
    "TrentinoData/avledet/hoydekurver_${interval}m.gpkg" \
    contour -simplify 0.25
done
```

## Switch the V2 project to simplified contours

Replace all four contour source paths, retain the project settings and keep the original backup if it already exists.

```bash
python3 - <<'PY'
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile
import os
import shutil

project = Path("montebondoneV2.qgz")
backup = Path("montebondoneV2.before_simplified.qgz")
with ZipFile(project) as archive:
    entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
if not backup.exists():
    shutil.copy2(project, backup)
with NamedTemporaryFile(dir=project.parent, suffix=".qgz", delete=False) as handle:
    temporary = Path(handle.name)
try:
    with ZipFile(temporary, "w") as archive:
        for info, data in entries:
            if info.filename.endswith(".qgs"):
                for interval in ("2.5", "10", "25", "100"):
                    old = f"./TrentinoData/avledet/hoydekurver_{interval}m.gpkg"
                    new = old.removesuffix(".gpkg") + "_simplified.gpkg"
                    assert Path(new).is_file(), new
                    data = data.replace(old.encode(), new.encode())
            archive.writestr(info, data)
    with ZipFile(temporary) as archive:
        assert archive.testzip() is None
    os.replace(temporary, project)
finally:
    temporary.unlink(missing_ok=True)
PY
```

## Compact slope raster

Convert the Float32 slope values to an 8-bit palette using the exact discrete
classes and colors from `varsomfargerV2.qml`:

```bash
/usr/bin/python3 scripts/komprimer_bratthet.py \
  --input TrentinoData/avledet/helning_5x5.tif \
  --style TrentinoData/varsomfargerV2.qml \
  --output TrentinoData/avledet/helning_5x5_palett.tif
```

The new file is a losslessly compressed COG GeoTIFF with nearest-neighbor
overviews. Resolution and georeferencing are preserved. Transparent classes
and source NoData become NoData code 0; other pixels store color class codes,
not slope degrees. The original file is retained. Requires GDAL and NumPy.
All three arguments have the defaults shown above, so running the script
without arguments performs this conversion too. Choose a new output name
if the file already exists.

In QGIS/QField, load the output using its embedded palette. To use it in the
project builder below, change the slope layer's `source` in `qgis_lag.json`
to `../TrentinoData/avledet/helning_5x5_palett.tif` and remove its `style`
entry: the original QML classifies degrees, while this file contains class
codes. Then rebuild the project and package it with `--all-layers`.

## QField packaging

Build a QGIS project from the layers defined in `scripts/qgis_lag.json`:

```bash
/usr/bin/python3 scripts/lag_qgis_prosjekt.py
```

This creates `montebondone_auto.qgz` with four simplified contour layers above
the slope raster, all visible. The contour colors and widths follow the V2
project, and the slope raster receives `TrentinoData/varsomfargerV2.qml`.
The style is embedded in the saved project and data paths are relative.
The script runs without opening a QGIS window; open the saved project in QGIS
to view it. Edit the JSON list to choose layers and their order (top first).
`source` and optional `style` paths are relative to the JSON file; GeoPackage
sources can include `|layername=contour`. Each layer has `name`, `type`
(`vector` or `raster`) and `source`; line layers optionally accept a
`line_symbol` object. Use `--config`, `--output`, or `--overwrite` as needed.

Later, package the generated project for QField:

```bash
python3 scripts/pakk_qfield.py \
  --project montebondone_auto.qgz --all-layers \
  --output TrentinoData/avledet/qfield_montebondone_auto
```

Use a new `--output` name for each rebuild; the packaging script refuses existing output folders or ZIPs.

Package the original project's two contour layers and slope raster.

```bash
python3 scripts/pakk_qfield.py \
  --project montebondone.qgz \
  --output TrentinoData/avledet/qfield_montebondone
```

Package all V2 layers with relative paths and the project's existing styles, order and visibility.

```bash
python3 scripts/pakk_qfield.py \
  --project montebondoneV2.qgz --all-layers \
  --output TrentinoData/avledet/qfield_montebondoneV2
```

Package V2 after switching its contour sources to the simplified files.

```bash
python3 scripts/pakk_qfield.py \
  --project montebondoneV2.qgz --all-layers \
  --output TrentinoData/avledet/qfield_montebondoneV2_simplified
```

## Offline OpenStreetMap basemap

Create the source-data directory.

```bash
mkdir -p TrentinoData/basemap/source
```

Download OsmAnd's raw OpenStreetMap extract covering Trentino–Alto Adige / South Tyrol.

```bash
curl -fsSL --retry 3 --max-time 600 \
  -o TrentinoData/basemap/source/trentino-alto-adige.osm.pbf \
  https://builder.osmand.net/osm-extract/italy_trentino-alto-adige_europe/italy_trentino-alto-adige_europe.pbf
```

Convert points, lines and polygons to a spatially indexed GeoPackage in EPSG:7791.

```bash
ogr2ogr -f GPKG \
  TrentinoData/basemap/trentino_osm.gpkg \
  TrentinoData/basemap/source/trentino-alto-adige.osm.pbf \
  points lines multipolygons \
  -t_srs EPSG:7791 -gt 65536 -dsco VERSION=1.3
```

Build the styled basemap project, reusable QLR group, embedded default styles and Monte Bondone preview; conversion runs automatically if the GeoPackage is absent.

```bash
QT_QPA_PLATFORM=offscreen /usr/bin/python3 scripts/lag_osm_basemap.py
```

Create and verify the standalone basemap ZIP, including the existing attribution/source notes.

```bash
python3 - <<'PY'
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

base = Path("TrentinoData/basemap")
files = [base / name for name in (
    "trentino_osm.gpkg", "trentino_basemap.qgz",
    "trentino_basemap.qlr", "SOURCES.txt",
)] + sorted(base.glob("trentino_osm_*.qml"))
output = base / "trentino_basemap.zip"
with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
    for source in files:
        archive.write(source, arcname=source.name)
with ZipFile(output) as archive:
    assert archive.testzip() is None
print(output)
PY
```

Drag `TrentinoData/basemap/trentino_basemap.qlr` into QGIS and place its group below the contours.

Map data: © OpenStreetMap contributors, ODbL 1.0; source details are in `TrentinoData/basemap/source/SOURCE.json`.

## Compact hillshade

Create a virtual 2 m elevation raster using average resampling; change `-tr 2 2` to `-tr 5 5` for a smaller, softer result.

```bash
gdalwarp -of VRT -tr 2 2 -r average \
  TrentinoData/avledet/terreng_05m_5x5.tif \
  TrentinoData/avledet/terreng_2m_hillshade.vrt
```

Generate the compressed, tiled, 8-bit multidirectional hillshade suggested in the chat.

```bash
gdaldem hillshade \
  TrentinoData/avledet/terreng_2m_hillshade.vrt \
  TrentinoData/avledet/hillshade_2m.tif \
  -multidirectional -compute_edges \
  -co COMPRESS=DEFLATE -co PREDICTOR=2 -co TILED=YES
```

In QGIS, place the hillshade above OSM land and below roads/contours, select Multiply blending and start at 40% opacity.

For QField, include the finished hillshade GeoTIFF; the source DEM and intermediate VRT can stay on the computer.
