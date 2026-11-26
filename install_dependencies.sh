#!/usr/bin/env bash
# Install the pipeline's dependencies from configured Debian/Ubuntu repositories.
set -Eeuo pipefail

trap 'printf "Installation/check failed at line %s.\n" "$LINENO" >&2' ERR

check_only=false
case "${1:-}" in
    --check-only) check_only=true ;;
    -h|--help)
        printf 'Usage: %s [--check-only]\n' "$0"
        printf 'Default: update APT, upgrade packages, install dependencies, and verify them.\n'
        printf '%s\n' '--check-only: verify existing dependencies without installing anything.'
        exit 0
        ;;
    "") ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
    printf 'Expected at most one option.\n' >&2
    exit 2
fi

if [[ "$check_only" == false ]]; then
    if ! command -v apt-get >/dev/null; then
        printf 'This installer requires Debian/Ubuntu with apt-get.\n' >&2
        exit 1
    fi

    elevate=()
    if (( EUID != 0 )); then
        if ! command -v sudo >/dev/null; then
            printf 'Install sudo or run this installer as root.\n' >&2
            exit 1
        fi
        elevate=(sudo)
    fi

    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get upgrade -y
    "${elevate[@]}" apt-get install -y \
        ca-certificates \
        python3 \
        gdal-bin \
        python3-gdal \
        python3-numpy \
        python3-yaml \
        python3-qgis
fi

printf 'Checking pipeline dependencies...\n'
for program in gdal gdalbuildvrt gdal_contour ogr2ogr gdaldem; do
    if ! command -v "$program" >/dev/null; then
        printf 'Missing command: %s. Install gdal-bin with GDAL >= 3.12.\n' "$program" >&2
        exit 1
    fi
done

if ! gdal raster neighbors --help >/dev/null; then
    printf 'The pipeline requires GDAL >= 3.12 with "gdal raster neighbors".\n' >&2
    printf 'Use an OS/repository that supplies matching GDAL tools and Python bindings.\n' >&2
    exit 1
fi

# Use the same system interpreter as the QGIS builder, independently of venvs.
QT_QPA_PLATFORM=offscreen /usr/bin/python3 - <<'PY'
import sqlite3
import sys
import zipfile
import zlib

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")

import numpy
import yaml
from osgeo import gdal, ogr, osr
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsLineSymbol, QgsMapLayerType, QgsProject, QgsRasterLayer,
    QgsReferencedRectangle, QgsVectorLayer, QgsWkbTypes,
)

gdal.UseExceptions()
if int(gdal.VersionInfo("VERSION_NUM")) < 3120000:
    raise SystemExit("python3-gdal must provide GDAL >= 3.12; upgrade the tools and bindings together.")
for driver in ("GTiff", "COG", "GPKG"):
    if gdal.GetDriverByName(driver) is None:
        raise SystemExit(f"Missing GDAL driver: {driver}")

app = QgsApplication([], False)
app.initQgis()
try:
    if not QgsCoordinateReferenceSystem("EPSG:7791").isValid():
        raise SystemExit("QGIS cannot load the project's EPSG:7791 coordinate system.")
    project = QgsProject()
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    project.clear()
    print(f"Python {sys.version.split()[0]}; GDAL {gdal.VersionInfo('RELEASE_NAME')}; QGIS {Qgis.QGIS_VERSION}")
    print(f"NumPy {numpy.__version__}; PyYAML {yaml.__version__}; SQLite and ZIP support available")
finally:
    app.exitQgis()
PY

printf 'Dependencies are ready. Run the pipeline with /usr/bin/python3 scripts/pipeline.py ...\n'
