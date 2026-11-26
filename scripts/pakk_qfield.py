#!/usr/bin/env python3
"""Package QGIS map layers for local QField import.

Keep the original QGIS project untouched, preserve its embedded styles, and
make a portable copy with relative data paths. Uses only the standard library.
Use --all-layers for pipeline projects, including the bottommost terrain base.
Grayscale COG masks are internal; their renderer is embedded in the project.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


DATA_FILES = ("hoydekurver5.gpkg", "hoydekurver.gpkg", "helning_5x5.tif")


def portable_project(
    project: Path, *, all_layers: bool = False,
) -> tuple[bytes, str, dict[str, Path]]:
    with ZipFile(project) as archive:
        qgs_name = next(name for name in archive.namelist() if name.endswith(".qgs"))
        root = ET.fromstring(archive.read(qgs_name))

    project_layers = root.find("projectlayers")
    tree = root.find("layer-tree-group")
    if project_layers is None or tree is None:
        raise ValueError("Project has no map layer list or layer tree")

    kept: dict[str, ET.Element] = {}
    sources: dict[str, Path] = {}
    for layer in list(project_layers):
        datasource = layer.find("datasource")
        if datasource is None or not datasource.text:
            if all_layers:
                raise ValueError(f"Layer has no data source: {layer.findtext('layername')}")
            project_layers.remove(layer)
            continue
        filename, separator, options = datasource.text.partition("|")
        source = (project.parent / filename).resolve()
        if not all_layers and source.name not in DATA_FILES:
            project_layers.remove(layer)
            continue
        if all_layers and source.suffix.lower() not in {".gpkg", ".tif", ".tiff"}:
            raise ValueError(f"Unsupported portable data source: {source}")
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.name in kept:
            raise ValueError(f"More than one layer uses {source.name}")
        kept[source.name] = layer
        sources[source.name] = source
        datasource.text = f"./{source.name}{separator}{options}"

    if all_layers:
        # Retain order (including the bottommost base), visibility, groups,
        # embedded raster styles/masks and map themes.
        by_id = {layer.findtext("id"): layer for layer in kept.values()}
        for node in tree.iter("layer-tree-layer"):
            node.set("source", by_id[node.get("id")].findtext("datasource"))
        set_relative_paths(root)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True), qgs_name, sources

    missing = set(DATA_FILES) - kept.keys()
    if missing:
        raise ValueError(f"Missing project layers: {sorted(missing)}")
    ordered_ids = [kept[name].findtext("id") for name in DATA_FILES]
    keep_ids = set(ordered_ids)

    # Put contours above the colored slope raster and enable all three layers.
    nodes = {node.get("id"): node for node in tree.iter("layer-tree-layer")}
    for parent in tree.iter():
        for node in list(parent):
            if node.tag == "layer-tree-layer":
                parent.remove(node)
    for layer_id, filename in zip(ordered_ids, DATA_FILES):
        node = nodes[layer_id]
        node.set("source", kept[filename].findtext("datasource"))
        node.set("checked", "Qt::Checked")
        tree.append(node)
    custom_order = tree.find("custom-order")
    if custom_order is not None:
        custom_order.clear()
        custom_order.set("enabled", "0")
        for layer_id in ordered_ids:
            ET.SubElement(custom_order, "item").text = layer_id

    order = root.find("layerorder")
    if order is not None:
        order.clear()
        for layer_id in ordered_ids:
            ET.SubElement(order, "layer", id=layer_id)

    # Update the legacy legend too, retaining its per-layer display settings.
    legend = root.find("legend")
    if legend is not None:
        entries = {}
        for entry in list(legend):
            for item in entry.iter("legendlayerfile"):
                if item.get("layerid") in keep_ids:
                    entries[item.get("layerid")] = entry
                    entry.set("checked", "Qt::Checked")
                    item.set("visible", "1")
            legend.remove(entry)
        for layer_id in ordered_ids:
            if layer_id in entries:
                legend.append(entries[layer_id])

    set_relative_paths(root)

    # Remove references to excluded layers from snapping and saved map themes.
    excluded_ids = set(nodes) - keep_ids
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") in excluded_ids or child.get("layerid") in excluded_ids:
                parent.remove(child)

    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml, qgs_name, sources


def set_relative_paths(root: ET.Element) -> None:
    home = root.find("homePath")
    if home is not None:
        home.set("path", "")
    absolute = root.find("./properties/Paths/Absolute")
    if absolute is not None:
        absolute.text = "false"


def copy_data(source: Path, target: Path) -> None:
    if source.suffix == ".gpkg" and Path(f"{source}-wal").exists():
        # A backup includes committed edits still in the live SQLite WAL file.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as incoming:
            with sqlite3.connect(target) as outgoing:
                incoming.backup(outgoing)
    else:
        before = source.stat()
        shutil.copy2(source, target)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"Source changed while copying: {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("montebondone.qgz"))
    parser.add_argument(
        "--all-layers", action="store_true",
        help="Include all local GeoPackage/GeoTIFF layers and preserve the project layout",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("TrentinoData/avledet/qfield_montebondone"),
    )
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output.resolve()
    zip_output = output.with_suffix(".zip")
    if output.exists() or zip_output.exists():
        parser.error(f"Output already exists: {output} or {zip_output}")

    xml, qgs_name, sources = portable_project(project, all_layers=args.all_layers)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qfield-package-", dir=output.parent) as tmp:
        staging = Path(tmp)
        folder = staging / output.name
        folder.mkdir()
        for filename, source in sources.items():
            print(f"Copying {filename}: {source.stat().st_size / 1e9:.2f} GB", flush=True)
            copy_data(source, folder / filename)
            if source.suffix == ".tif":
                for suffix in (".aux.xml", ".ovr", ".msk"):
                    sidecar = Path(f"{source}{suffix}")
                    if sidecar.is_file():
                        copy_data(sidecar, folder / sidecar.name)

        with ZipFile(project) as original:
            with ZipFile(folder / project.name, "w", ZIP_DEFLATED) as portable:
                for item in original.infolist():
                    portable.writestr(item, xml if item.filename == qgs_name else original.read(item))

        archive_path = staging / zip_output.name
        with ZipFile(archive_path, "w", ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for source in sorted(folder.iterdir()):
                print(f"Compressing {source.name}", flush=True)
                archive.write(source, arcname=source.name)
        print("Verifying archive checksums", flush=True)
        with ZipFile(archive_path) as archive:
            failed = archive.testzip()
            if failed:
                raise RuntimeError(f"Archive checksum failed: {failed}")

        folder.rename(output)
        archive_path.rename(zip_output)

    print(f"QField folder: {output}", flush=True)
    print(f"QField ZIP: {zip_output} ({zip_output.stat().st_size / 1e9:.2f} GB)", flush=True)


if __name__ == "__main__":
    main()
