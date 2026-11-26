# monte calisi
# 45.0844 11.1184 46.1023 11.1777

# Trentino / Monte Bondone maps

Commands used or shared in this chat; requires GDAL, Python 3 and QGIS Python bindings for the basemap script.

Run from the project directory.

```bash
cd /home/olav/kartTrento
```

The existing input `TrentinoData/avledet/terreng_05m_5x5.tif` is a smoothed 0.5 m DEM; its preparation commands were not recorded in this chat.

## Install pipeline dependencies

On Debian/Ubuntu, run:

```bash
bash scripts/install_dependencies.sh
```

The installer runs `sudo apt-get update` and `sudo apt-get upgrade -y` first,
then installs `ca-certificates`, `python3`, `gdal-bin`, `python3-gdal`,
`python3-numpy`, `python3-yaml`, and `python3-qgis`. When run as root, it omits
`sudo`. QGIS providers and other supporting libraries are installed as package
dependencies. Downloads and QField ZIP packaging use Python's standard library.

The pipeline requires GDAL 3.12 or newer because it uses
[`gdal raster neighbors`](https://gdal.org/en/stable/programs/gdal_raster_neighbors.html).
The installer uses the configured APT repositories; these must supply a recent
enough GDAL version and matching Python bindings. The current project environment
uses Ubuntu 26.04. The installer verifies command availability, Python imports,
GeoTIFF/COG/GeoPackage drivers, and QGIS startup without opening a window.

To check existing dependencies without installing or upgrading packages:

```bash
bash scripts/install_dependencies.sh --check-only
```

Run the pipeline with `/usr/bin/python3 pipeline.py ...` to use the
system Python packages installed by APT.

Every pipeline run generates `mapdata/hillshade.tif` with
`scripts/lag_hillshade.py`, using a native-resolution VRT assembled from that
run's downloaded tiles. The base retains the source pixel size regardless of
the configured contour/colored-slope grid. It is normalized to gray 60–230 and
compressed as an 8-bit COG with DEFLATE level 6 and an internal validity mask.

All TIFF writers in the pipeline and its terrain/coloring helpers explicitly
use `BIGTIFF=YES`, including temporary elevation and slope rasters. This avoids
the classic TIFF limit of approximately 4 GiB, even when compression makes final
file size unpredictable. Files retain their `.tif` extension, compression and
pixel values; QGIS and QField read them through GDAL as usual.

The generated `qgis_lag.json` lists contours first, the colored Varsom slope
overlay next, and the grayscale terrain base **last**, at full opacity. The
QGIS builder loads its generated QML and embeds the grayscale, transparency
and resampling settings in the project. QField packaging uses `--all-layers`,
copies the self-contained base TIFF, and preserves that bottom position and
embedded style. Raw DEMs and separate QML files are not needed in the QField ZIP.

## Tests

Tests are collected in `tests/`. Run them from the project directory with the
scripts on Python's import path (requires GDAL and NumPy):

```bash
PYTHONPATH=scripts /usr/bin/python3 -m unittest discover -s tests -v
```

## Pipeline cleanup

After a successful pipeline run, `temp/` is deleted by default. Use `--keptemp`
(also accepted as `--keeptemp`) to retain it. Use `-c` or `--clean` to delete
everything in the map folder except the generated `qfield_<mapname>.zip`,
including `temp/`, `mapdata/`, the QGIS project/config, and the unpacked QField
folder. The two cleanup options are mutually exclusive. Failed runs retain
their files for inspection.

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
Any layer can optionally set `"opacity": 0.5` for 50% opacity, from `0`
(invisible) to `1` (fully opaque). This overrides opacity from a QML style;
omitting it preserves the layer's existing opacity.

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

## Native-resolution grayscale terrain shading from ChimaTose

The hillshade script now calculates slope directly from the original DEM,
normalizes it to gray, and compresses the finished raster:

```bash
/usr/bin/python3 scripts/lag_hillshade.py

# Regenerate an existing output and its matching QGIS style.
/usr/bin/python3 scripts/lag_hillshade.py --overwrite
```

The default input is `ChimaTose/temp/terrain/terrain_0p5m.vrt`; output is
`ChimaTose/mapdata/hillshade_osmand.tif` with a matching `.qml` style. The original
**0.5 m grid, 4000 × 4000 pixels**, extent and CRS are retained. The filename is
retained for existing map layers. Processing uses unexaggerated Horn slope in
degrees on the native grid. All light directions, altitude weights, smoothing
and elevation resampling have been removed from this script.

Flat ground is **light gray 230** and the maximum valid slope in the complete
map is **dark gray 60**, with linear grading between them. Only the maximum
reaches the darkest endpoint. Pixel values are rendered gray intensity, not
slope degrees. The 60–230 range contains 171 distinct gray intensities within
the 8-bit format.

Compression follows `scripts/komprimer_bratthet.py`: a tiled **Cloud Optimized
GeoTIFF (COG)**, lossless **DEFLATE level 6**, and **512 × 512 pixel tiles**.
The grayscale output adds horizontal prediction (`PREDICTOR=2`) and stores
transparency in an internal one-bit validity mask, leaving just one 8-bit gray
band. The mask is inside the TIFF; no separate mask file is needed. Compression
preserves full-resolution gray values and the original elevation coverage.
Overview levels use average resampling for smooth zoomed-out display, while
the full-resolution grid is retained.

Drag the TIFF into QGIS, keeping the QML beside it. Reload the matching style
for an existing layer. It preserves the baked gray values and uses cubic
zoom-in resampling. Missing elevation is transparent through the internal
mask. The TIFF records the maximum slope, processing parameters and compression
settings. The script prints the final compressed file size.

All defaults are near the top of `scripts/lag_hillshade.py`. Use `--input`,
`--output`, `--dark-gray`, `--light-gray` and `--overwrite` to override them.
For example:

```bash
/usr/bin/python3 scripts/lag_hillshade.py \
  --dark-gray 80 --light-gray 220 \
  --output ChimaTose/mapdata/steepness_muted.tif
```

`scripts/lag_bratthet_graa.py` remains an alternate entry point to this same
implementation, with `ChimaTose/mapdata/steepness_gray.tif` as its default output.
Requires GDAL and NumPy; run with `/usr/bin/python3`. Temporary processing
files are removed after success or failure.
