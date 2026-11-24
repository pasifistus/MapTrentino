import argparse
from pathlib import Path
import subprocess
import shlex
import sys

import yaml

from lastned import SOURCE


ROOT = Path(__file__).resolve().parents[1]


def run(*arguments):
    command = [str(value) for value in arguments]

    print(f"\n$ {shlex.join(command)}", flush=True)

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mapname", type=str, help="Name of the map")
    parser.add_argument("lat1", type=float, help="Breddegrad for første hjørne")
    parser.add_argument("lon1", type=float, help="Lengdegrad for første hjørne")
    parser.add_argument("lat2", type=float, help="Breddegrad for andre hjørne")
    parser.add_argument("lon2", type=float, help="Lengdegrad for andre hjørne")
    parser.add_argument("--config", type=Path, default=ROOT / "config/config.yaml")
    parser.add_argument("--keeptemp", action="store_true")

    args = parser.parse_args()

    with args.config.open(encoding="utf-8") as file:
        config = yaml.safe_load(file)

    terrain = config["terrain"]
    source_url = config.get("URL", SOURCE)

    # Define the file structure.
    temp = ROOT / args.mapname / "temp"
    mapdata = ROOT / args.mapname / "mapdata"
    terrain_dir = temp / "terrain"
    terrain_dir.mkdir(parents=True, exist_ok=True)
    mapdata.mkdir(parents=True, exist_ok=True)

    # Generate the download list from coordinates.
    run(
        sys.executable, "-u", ROOT / "scripts/lag_kartliste.py",
        args.lat1, args.lon1, args.lat2, args.lon2,
        "-o", mapdata / "downloadlist.txt",
        "--url", source_url,
    )


    # Download the maps.
    run(
        sys.executable, "-u", ROOT / "scripts/lastned.py",
        "--output", temp / "lidar_asc",
        "--file-list", mapdata / "downloadlist.txt",
        "--workers", config["download"]["workers"],
        "--url", source_url,
        "--download",
    )

    # Assemble the LiDAR data.
    tiles = sorted((temp / "lidar_asc").glob("*.asc"))
    if not tiles:
        raise RuntimeError(f"No ASC tiles found in {temp / 'lidar_asc'}")
    for resolution in terrain["resolution"]:
        resolution_tag = str(resolution).replace(".", "p")
        run(
            "gdalbuildvrt",
            "-a_srs", "EPSG:7791",
            "-resolution", "user",
            "-tr", resolution, resolution,
            terrain_dir / f"terrain_{resolution_tag}m.vrt",
            *tiles,
        )

    
    # Generate contour lines and simplify them.
    res = str(terrain["resolution"][config["calculation"]["resC"]]).replace(".", "p")
    input_vrt = terrain_dir / f"terrain_{res}m.vrt"
    for lines in terrain["contourlines"]:
        interval_tag = str(lines).replace(".", "p")
        bridge = temp / f"contours_{interval_tag}m.gpkg"
        out = mapdata / f"contours_{interval_tag}m.gpkg"

        run(
            "gdal_contour",
            "-i", lines,
            "-a", "ELEV",
            "-f", "GPKG",
            "-nln", "contour",
            input_vrt,
            bridge,
        )
        run(
            "ogr2ogr",
            "-f", "GPKG",
            out,
            bridge,
            "contour",
            "-simplify", terrain["simpclines"],
        )

    # Smooth the elevation raster with a moving average.
    window = terrain["smoothing_window"]
    smoothed_tif = terrain_dir / f"terrain_{res}m_mean{window}x{window}.tif"
    run(
        "gdal", "raster", "neighbors",
        "--kernel", "equal",
        "--method", "mean",
        "--size", window,
        "--ot", "Float32",
        "--co", "TILED=YES",
        "--co", "COMPRESS=DEFLATE",
        input_vrt,
        smoothed_tif,
    )
    # Calculate steepness in degrees.
    slope_file = temp / "steepness.tif"
    run(
        "gdaldem", "slope",
        smoothed_tif,
        slope_file,
        "-s", 1,
        "-co", "TILED=YES",
        "-co", "COMPRESS=DEFLATE",
        "-co", "PREDICTOR=3",
    )
    # Color steepness and compress.
    run(
        sys.executable, "-u", ROOT / "scripts/komprimer_bratthet.py",
        "--input", slope_file,
        "--output", mapdata / "steepness.tif",
        "--style", ROOT / "config/varsomfargerV2.qml",
    )

if __name__ == "__main__":
    main()
