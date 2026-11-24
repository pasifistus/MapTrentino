import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from osgeo import osr

import lag_kartliste
from lag_kartliste import tile_names
from lastned import read_file_list


class TileListTests(unittest.TestCase):
    def test_matches_existing_rectangle_including_order(self):
        reference = Path(__file__).resolve().parents[1] / "TrentinoData/kartfiler_rektangel.txt"
        self.assertEqual(
            tile_names(45.9686, 10.9962, 46.0707, 11.1058),
            read_file_list(reference),
        )

    def test_either_diagonal_and_either_order_give_same_tiles(self):
        expected = tile_names(45.9686, 10.9962, 46.0707, 11.1058)
        for points in ((46.0707, 11.1058, 45.9686, 10.9962),
                       (45.9686, 11.1058, 46.0707, 10.9962)):
            with self.subTest(points=points):
                self.assertEqual(tile_names(*points), expected)

    def test_small_rectangle_crossing_grid_corner_includes_four_tiles(self):
        # Et rektangel på omtrent 2 x 2 meter over et felles rutehjørne.
        osr.UseExceptions()
        projected = osr.SpatialReference()
        projected.ImportFromEPSG(7791)
        geographic = osr.SpatialReference()
        geographic.ImportFromEPSG(4326)
        for crs in (projected, geographic):
            crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = osr.CoordinateTransformation(projected, geographic)
        lon1, lat1, _ = transform.TransformPoint(654999, 5094999)
        lon2, lat2, _ = transform.TransformPoint(655001, 5095001)
        self.assertEqual(tile_names(lat1, lon1, lat2, lon2), [
            "5h654550945_DTM.asc", "5h654550950_DTM.asc",
            "5h655050945_DTM.asc", "5h655050950_DTM.asc",
        ])

    def test_invalid_or_empty_rectangles_are_rejected(self):
        for points in ((46, 11, 46, 11.1), (46, 11, 46.1, 11),
                       (float("nan"), 11, 46.1, 11.1),
                       (46, float("inf"), 46.1, 11.1),
                       (11, 46, 11.1, 46.1)):
            with self.subTest(points=points), self.assertRaises(ValueError):
                tile_names(*points)


class VerifiedTileListTests(unittest.TestCase):
    def run_main(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["lag_kartliste.py", "45.9686", "10.9962",
                                "46.0707", "11.1058", *arguments]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = lag_kartliste.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_only_existing_tiles_are_written_in_geographic_order(self):
        candidates = tile_names(45.9686, 10.9962, 46.0707, 11.1058)
        # Simulate a coverage boundary: only the first and last tiles exist.
        listing = [("https://example.test/" + name, name)
                   for name in (candidates[-1], "unrelated.asc", candidates[0])]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tiles.txt"
            with patch.object(lag_kartliste, "discover", return_value=listing) as discover:
                status, stdout, stderr = self.run_main(
                    "--url", "https://example.test/", "-o", str(output))
            self.assertEqual(status, 0, stderr)
            discover.assert_called_once_with("https://example.test/")
            self.assertEqual(read_file_list(output), [candidates[0], candidates[-1]])
            self.assertEqual(stdout, "")
            self.assertIn(f"forkastet {len(candidates) - 2} ruter", stderr)
            self.assertIn("# Tilgjengelighet kontrollert mot: https://example.test/",
                          output.read_text(encoding="utf-8"))

    def test_entire_rectangle_outside_coverage_outputs_no_filenames(self):
        with patch.object(lag_kartliste, "discover", return_value=[]) as discover:
            status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        discover.assert_called_once_with(lag_kartliste.SOURCE)
        self.assertIn("# Antall filer: 0", stdout)
        self.assertEqual([line for line in stdout.splitlines()
                          if line and not line.startswith("#")], [])
        self.assertIn("Fant 0 tilgjengelige ruter", stderr)

    def test_directory_failure_does_not_overwrite_existing_list(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tiles.txt"
            output.write_text("existing.asc\n", encoding="utf-8")
            with patch.object(lag_kartliste, "discover", side_effect=URLError("offline")), \
                    self.assertRaises(SystemExit) as error:
                self.run_main("-o", str(output))
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing.asc\n")


if __name__ == "__main__":
    unittest.main()
