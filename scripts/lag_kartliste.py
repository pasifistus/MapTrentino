#!/usr/bin/env python3
"""Lag en filliste for et WGS84-rektangel mellom to diagonale hjørner.

Koordinatrekkefølge: breddegrad lengdegrad, i desimalgrader.
Krever GDALs Python-bindinger (osgeo), som de andre GIS-verktøyene i prosjektet.
Filnavn kontrolleres mot serverens katalog; ruter utenfor dekningen forkastes.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

from lastned import SOURCE, discover


TILE_SIZE = 500
EDGE_SEGMENTS = 512


def tile_names(lat1: float, lon1: float, lat2: float, lon2: float) -> list[str]:
    """Returner sorterte filnavn for ruter med areal innenfor rektangelet."""
    from osgeo import ogr, osr

    if not all(math.isfinite(value) for value in (lat1, lon1, lat2, lon2)):
        raise ValueError("Koordinatene må være endelige tall.")
    # EPSG:7791 gjelder Italia mellom 6 og 12 grader øst.
    if not all(35 <= lat <= 48 for lat in (lat1, lat2)) or not all(
        6 <= lon <= 12 for lon in (lon1, lon2)
    ):
        raise ValueError("Bruk breddegrad 35–48 N og lengdegrad 6–12 E (UTM-sone 32 i Italia).")
    if lat1 == lat2 or lon1 == lon2:
        raise ValueError("Hjørnene må ha ulik breddegrad og ulik lengdegrad.")

    osr.UseExceptions()
    ogr.UseExceptions()
    source = osr.SpatialReference()
    source.ImportFromEPSG(4326)
    target = osr.SpatialReference()
    target.ImportFromEPSG(7791)
    for crs in (source, target):
        crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)

    south, north = sorted((lat1, lat2))
    west, east = sorted((lon1, lon2))
    corners = [(west, south), (east, south), (east, north), (west, north)]
    ring = ogr.Geometry(ogr.wkbLinearRing)
    # Et geografisk rektangel får buede kanter i UTM. Fortett alle kantene
    # før projeksjon for å ta med riktige ruter også langs ytterkantene.
    for start, end in zip(corners, corners[1:] + corners[:1]):
        for step in range(EDGE_SEGMENTS):
            fraction = step / EDGE_SEGMENTS
            lon = start[0] + (end[0] - start[0]) * fraction
            lat = start[1] + (end[1] - start[1]) * fraction
            x, y, _ = transform.TransformPoint(lon, lat)
            ring.AddPoint_2D(x, y)
    ring.CloseRings()
    rectangle = ogr.Geometry(ogr.wkbPolygon)
    rectangle.AddGeometry(ring)
    min_x, max_x, min_y, max_y = rectangle.GetEnvelope()

    names = []
    for x in range(math.floor(min_x / TILE_SIZE) * TILE_SIZE,
                   math.ceil(max_x / TILE_SIZE) * TILE_SIZE, TILE_SIZE):
        for y in range(math.floor(min_y / TILE_SIZE) * TILE_SIZE,
                       math.ceil(max_y / TILE_SIZE) * TILE_SIZE, TILE_SIZE):
            tile_ring = ogr.Geometry(ogr.wkbLinearRing)
            for tx, ty in ((x, y), (x + TILE_SIZE, y),
                           (x + TILE_SIZE, y + TILE_SIZE), (x, y + TILE_SIZE), (x, y)):
                tile_ring.AddPoint_2D(tx, ty)
            tile = ogr.Geometry(ogr.wkbPolygon)
            tile.AddGeometry(tile_ring)
            if rectangle.Intersection(tile).GetArea() > 0:
                # Filnavnet lagrer øst/nord i enheter på 100 meter.
                names.append(f"5h{x // 100:04d}{y // 100:05d}_DTM.asc")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lat1", type=float, help="Breddegrad for første hjørne")
    parser.add_argument("lon1", type=float, help="Lengdegrad for første hjørne")
    parser.add_argument("lat2", type=float, help="Breddegrad for andre hjørne")
    parser.add_argument("lon2", type=float, help="Lengdegrad for andre hjørne")
    parser.add_argument("--url", default=SOURCE,
                        help="Katalog-URL (standard: Trentino 0,5 m DTM)")
    parser.add_argument("-o", "--output", type=Path,
                        help="Skriv listen til denne filen (standard: skriv til terminalen)")
    args = parser.parse_args()
    try:
        names = tile_names(args.lat1, args.lon1, args.lat2, args.lon2)
        available = {name for _, name in discover(args.url)}
        candidates = len(names)
        names = [name for name in names if name in available]
        discarded = candidates - len(names)
        print(f"Fant {len(names)} tilgjengelige ruter; forkastet {discarded} ruter "
              "som ikke finnes i serverens katalog.", file=sys.stderr)
        content = "\n".join([
            "# Kartfiler for rektangel med kanter langs bredde- og lengdegrader (WGS84).",
            f"# Diagonalpunkt 1: {args.lat1} N, {args.lon1} E",
            f"# Diagonalpunkt 2: {args.lat2} N, {args.lon2} E",
            "# Ruter: 500 x 500 m i EPSG:7791; tilgjengelige ruter som overlapper rektangelet.",
            f"# Tilgjengelighet kontrollert mot: {args.url}",
            f"# Forkastet {discarded} ruter som ikke finnes i serverens katalog.",
            f"# Antall filer: {len(names)}",
            "", *names, "",
        ])
        if args.output is None:
            sys.stdout.write(content)
        else:
            args.output.write_text(content, encoding="utf-8")
            print(f"Skrev {len(names)} filnavn til {args.output}", file=sys.stderr)
    except ImportError:
        parser.exit(1, "Mangler GDALs Python-bindinger (osgeo). Prøv /usr/bin/python3.\n")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Kunne ikke lage fillisten: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
