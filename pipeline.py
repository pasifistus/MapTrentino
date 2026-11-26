import argparse
import json
from pathlib import Path
import subprocess
import shlex
import shutil
import sys

import yaml

from scripts.lastned import SOURCE


ROOT = Path(__file__).resolve().parent
HILLSHADE_FILENAME = "hillshade.tif"


def run(*arguments):
    command = [str(value) for value in arguments]

    print(f"\n$ {shlex.join(command)}", flush=True)

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def cleanup_output(map_dir: Path, *, keep_temp: bool = False, clean: bool = False) -> None:
    """Clean a successful run, retaining the verified QField ZIP in clean mode."""
    if clean:
        archive = (map_dir / f"qfield_{map_dir.name}").with_suffix(".zip")
        if archive.is_symlink() or not archive.is_file():
            raise FileNotFoundError(f"Cannot clean without the QField ZIP: {archive}")
        targets = [entry for entry in map_dir.iterdir() if entry != archive]
    elif keep_temp:
        return
    else:
        targets = [map_dir / "temp"]

    for target in targets:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)


def make_qgis_config(config: dict, mapname: str, mapdata: Path) -> dict:
    """Build layer sources relative to a config saved in mapdata.parent."""
    makeconf = {"title": mapname,
                "crs": "EPSG:7791"}

    layers = []
    relative_mapdata = Path(mapdata.name)

    build = config["build"]
    if build["identCC"]:
        cc = [build["contourcolor"] for _ in config["terrain"]["contourlines"]]
    else:
        cc = build["contourcolor"]

    if build["identLW"]:
        lw = [build["linewidth"] for _ in config["terrain"]["contourlines"]]
    else:
        lw = build["linewidth"]


    # Add contour layers.
    for i, interval in enumerate(config["terrain"]["contourlines"]):
        interval_tag = str(interval).replace(".", "p")
        source = relative_mapdata / f"contours_{interval_tag}m.gpkg"
        layers.append(
            {
                "name": f"Høydekurver {interval} m",
                "type": "vector",
                "source": f"{source}|layername=contour",
                "line_symbol": {"line_color": cc[i], "line_width": str(lw[i])}
            }
        )
    layers.reverse()
    layers.append({
        "name": "Bratthet – Varsom V2",
        "type": "raster",
        "source": str(relative_mapdata / "steepness.tif"),
        "opacity": float(build["opacitysteepness"])
    })
    # First is topmost; append the grayscale terrain beneath every overlay.
    base = relative_mapdata / HILLSHADE_FILENAME
    layers.append({
        "name": "Terreng – grå bratthet",
        "type": "raster",
        "source": str(base),
        "style": str(base.with_suffix(".qml")),
        "opacity": 1.0,
    })

    makeconf["layers"] = layers
    return makeconf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mapname", type=str, help="Name of the map")
    parser.add_argument("lat1", type=float, help="Breddegrad for første hjørne")
    parser.add_argument("lon1", type=float, help="Lengdegrad for første hjørne")
    parser.add_argument("lat2", type=float, help="Breddegrad for andre hjørne")
    parser.add_argument("lon2", type=float, help="Lengdegrad for andre hjørne")
    parser.add_argument("--config", type=Path, default=ROOT / "config/config.yaml")
    cleanup = parser.add_mutually_exclusive_group()
    cleanup.add_argument(
        "--keptemp", "--keeptemp", dest="keep_temp", action="store_true",
        help="Keep temporary files after a successful run",
    )
    cleanup.add_argument(
        "-c", "--clean", action="store_true",
        help="After successful packaging, keep only the QField ZIP in the map folder",
    )

    args = parser.parse_args()

    map_dir = (ROOT / args.mapname).resolve()
    if map_dir == ROOT.resolve() or not map_dir.is_relative_to(ROOT.resolve()):
        parser.error("mapname must refer to a folder inside the project directory")

    with args.config.open(encoding="utf-8") as file:
        config = yaml.safe_load(file)

    terrain = config["terrain"]
    source_url = config.get("URL", SOURCE)

    # Define the file structure.
    temp = map_dir / "temp"
    mapdata = map_dir / "mapdata"
    terrain_dir = temp / "terrain"
    terrain_dir.mkdir(parents=True, exist_ok=True)
    mapdata.mkdir(parents=True, exist_ok=True)

    # # Generate the download list from coordinates.
    # run(
    #     sys.executable, "-u", ROOT / "scripts/lag_kartliste.py",
    #     args.lat1, args.lon1, args.lat2, args.lon2,
    #     "-o", mapdata / "downloadlist.txt",
    #     "--url", source_url,
    # )


    # # Download the maps.
    # run(
    #     sys.executable, "-u", ROOT / "scripts/lastned.py",
    #     "--output", temp / "lidar_asc",
    #     "--file-list", mapdata / "downloadlist.txt",
    #     "--workers", config["download"]["workers"],
    #     "--url", source_url,
    #     "--download",
    # )

    # # Assemble the LiDAR data.
    # tiles = sorted((temp / "lidar_asc").glob("*.asc"))
    # if not tiles:
    #     raise RuntimeError(f"No ASC tiles found in {temp / 'lidar_asc'}")
    # for resolution in terrain["resolution"]:
    #     resolution_tag = str(resolution).replace(".", "p")
    #     run(
    #         "gdalbuildvrt",
    #         "-a_srs", "EPSG:7791",
    #         "-resolution", "user",
    #         "-tr", resolution, resolution,
    #         terrain_dir / f"terrain_{resolution_tag}m.vrt",
    #         *tiles,
    #     )

    # # Derive the base layer's full resolution from the downloaded tile grids,
    # # independently of the configured contour/colored-slope resolutions.
    native_vrt = terrain_dir / "terrain_native.vrt"
    # run(
    #     "gdalbuildvrt",
    #     "-a_srs", "EPSG:7791",
    #     "-resolution", "highest",
    #     native_vrt,
    #     *tiles,
    # )

    # # Generate contour lines and simplify them.
    res = str(terrain["resolution"][config["calculation"]["resC"]]).replace(".", "p")
    input_vrt = terrain_dir / f"terrain_{res}m.vrt"
    # for lines in terrain["contourlines"]:
    #     interval_tag = str(lines).replace(".", "p")
    #     bridge = temp / f"contours_{interval_tag}m.gpkg"
    #     out = mapdata / f"contours_{interval_tag}m.gpkg"

    #     run(
    #         "gdal_contour",
    #         "-i", lines,
    #         "-a", "ELEV",
    #         "-f", "GPKG",
    #         "-nln", "contour",
    #         input_vrt,
    #         bridge,
    #     )
    #     run(
    #         "ogr2ogr",
    #         "-f", "GPKG",
    #         out,
    #         bridge,
    #         "contour",
    #         "-simplify", terrain["simpclines"],
    #     )

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
        "--co", "BIGTIFF=YES",
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
        "-co", "BIGTIFF=YES",
    )
    # Color steepness and compress.
    run(
        sys.executable, "-u", ROOT / "scripts/komprimer_bratthet.py",
        "--input", slope_file,
        "--output", mapdata / "steepness.tif",
        "--style", ROOT / "config/varsomfargerV2.qml",
    )

    # Render and compress the native-resolution grayscale base for this map.
    run(
        sys.executable, "-u", ROOT / "scripts/lag_hillshade.py",
        "--input", native_vrt,
        "--output", mapdata / HILLSHADE_FILENAME,
    )

    # Save the layer config directly above mapdata, matching its relative sources.
    qgis_config = make_qgis_config(config, args.mapname, mapdata)
    qgis_config_path = map_dir / "qgis_lag.json"
    qgis_config_path.write_text(
        json.dumps(qgis_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    #build the project with the Python interpreter that has QGIS bindings.
    run(
        "/usr/bin/python3", "-u", ROOT / "scripts/lag_qgis_prosjekt.py",
        "--config", qgis_config_path,
        "--output", map_dir / f"{map_dir.name}.qgz",
    )

    # package the generated project and all its layers for QField.
    run(
        sys.executable, "-u", ROOT / "scripts/pakk_qfield.py",
        "--project", map_dir / f"{map_dir.name}.qgz",
        "--all-layers",
        "--output", map_dir / f"qfield_{map_dir.name}",
    )

    cleanup_output(map_dir, keep_temp=args.keep_temp, clean=args.clean)


if __name__ == "__main__":
    main()
