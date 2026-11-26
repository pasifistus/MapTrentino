import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import yaml

import pipeline


class LayerConfigTests(unittest.TestCase):
    def test_base_is_last_with_relative_source_explicit_style_and_full_opacity(self):
        config = {
            "terrain": {"contourlines": ["2.5", "10"]},
            "build": {"identCC": True, "contourcolor": "150,100,50,255",
                      "identLW": False, "linewidth": ["0.15", "0.2"], "opacitysteepness": "0.6"},
        }
        result = pipeline.make_qgis_config(config, "another-map", Path("/example/another-map/mapdata"))
        self.assertEqual(result["title"], "another-map")
        layers = result["layers"]
        self.assertEqual([layer["name"] for layer in layers[:2]], ["Høydekurver 10 m", "Høydekurver 2.5 m"])
        self.assertEqual(layers[-2]["source"], "mapdata/steepness.tif")
        self.assertEqual(layers[-2]["opacity"], 0.6)
        self.assertEqual(layers[-1], {
            "name": "Terreng – grå bratthet", "type": "raster",
            "source": "mapdata/hillshade.tif", "style": "mapdata/hillshade.qml", "opacity": 1.0,
        })
        self.assertTrue(all(not Path(layer["source"].split("|")[0]).is_absolute() for layer in layers))


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.map_dir = self.root / "sample"
        for folder in ("temp/lidar_asc", "mapdata", "qfield_sample"):
            (self.map_dir / folder).mkdir(parents=True)
        (self.map_dir / "temp/lidar_asc/tile.asc").write_text("tile")
        (self.map_dir / "mapdata/steepness.tif").write_text("raster")
        (self.map_dir / "sample.qgz").write_text("project")
        (self.map_dir / "qgis_lag.json").write_text("{}")
        self.archive = self.map_dir / "qfield_sample.zip"
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("sample.qgz", "portable project")
        self.archive_bytes = self.archive.read_bytes()

    def test_default_removes_only_temp(self):
        pipeline.cleanup_output(self.map_dir)
        self.assertFalse((self.map_dir / "temp").exists())
        self.assertTrue((self.map_dir / "mapdata/steepness.tif").is_file())
        self.assertTrue((self.map_dir / "sample.qgz").is_file())
        self.assertTrue((self.map_dir / "qfield_sample").is_dir())
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)

    def test_keep_temp_preserves_files(self):
        pipeline.cleanup_output(self.map_dir, keep_temp=True)
        self.assertTrue((self.map_dir / "temp/lidar_asc/tile.asc").is_file())
        self.assertTrue((self.map_dir / "mapdata/steepness.tif").is_file())

    def test_clean_leaves_only_unchanged_zip(self):
        pipeline.cleanup_output(self.map_dir, clean=True)
        self.assertEqual(list(self.map_dir.iterdir()), [self.archive])
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)

    def test_clean_without_zip_removes_nothing(self):
        self.archive.unlink()
        before = set(self.map_dir.iterdir())
        with self.assertRaises(FileNotFoundError):
            pipeline.cleanup_output(self.map_dir, clean=True)
        self.assertEqual(set(self.map_dir.iterdir()), before)

    def test_cleanup_does_not_follow_directory_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        (self.map_dir / "linked").symlink_to(outside, target_is_directory=True)
        pipeline.cleanup_output(self.map_dir, clean=True)
        self.assertEqual((outside / "keep.txt").read_text(), "keep")
        self.assertEqual(list(self.map_dir.iterdir()), [self.archive])

    def run_main(self, *options, fail_packaging=False, resolutions=None):
        settings = self.root / "settings.yaml"
        settings.write_text(yaml.safe_dump({
            "download": {"workers": 1},
            "terrain": {"resolution": resolutions or ["0.5"], "contourlines": ["2.5"],
                        "simpclines": "0.25", "smoothing_window": "5"},
            "calculation": {"resC": 0},
            "build": {"identCC": True, "contourcolor": "150,100,50,255",
                      "identLW": True, "linewidth": "0.25", "opacitysteepness": "0.6"},
        }))
        arguments = ["pipeline.py", "sample", "45.9", "11", "46", "11.1",
                     "--config", str(settings), *options]

        def command(*arguments):
            if any(str(value).endswith("pakk_qfield.py") for value in arguments):
                if fail_packaging:
                    raise subprocess.CalledProcessError(1, arguments)
                output = Path(arguments[arguments.index("--output") + 1])
                with ZipFile(output.with_suffix(".zip"), "w") as archive:
                    archive.writestr("sample.qgz", "portable project")

        with patch.object(pipeline, "ROOT", self.root), \
                patch.object(pipeline, "run", side_effect=command) as runner, \
                patch("sys.argv", arguments), \
                contextlib.redirect_stderr(io.StringIO()):
            pipeline.main()
        return [call.args for call in runner.call_args_list]

    def test_native_base_is_generated_before_qgis_and_included_in_all_layer_package(self):
        calls = self.run_main("--keptemp", resolutions=["5"])
        native = self.map_dir / "temp/terrain/terrain_native.vrt"
        native_call = next(call for call in calls if call[0] == "gdalbuildvrt" and native in call)
        self.assertEqual(native_call[native_call.index("-resolution") + 1], "highest")
        base_call = next(call for call in calls if any(str(value).endswith("lag_hillshade.py") for value in call))
        self.assertEqual(base_call[base_call.index("--input") + 1], native)
        self.assertEqual(base_call[base_call.index("--output") + 1], self.map_dir / "mapdata/hillshade.tif")
        builder = next(call for call in calls if any(str(value).endswith("lag_qgis_prosjekt.py") for value in call))
        packager = next(call for call in calls if any(str(value).endswith("pakk_qfield.py") for value in call))
        self.assertLess(calls.index(native_call), calls.index(base_call))
        self.assertLess(calls.index(base_call), calls.index(builder))
        self.assertLess(calls.index(builder), calls.index(packager))
        self.assertIn("--all-layers", packager)
        smoothing = next(call for call in calls if call[:3] == ("gdal", "raster", "neighbors"))
        raw_slope = next(call for call in calls if call[0] == "gdaldem")
        self.assertIn("BIGTIFF=YES", smoothing)
        self.assertIn("BIGTIFF=YES", raw_slope)
        generated = json.loads((self.map_dir / "qgis_lag.json").read_text())
        self.assertEqual(generated["layers"][-1]["source"], "mapdata/hillshade.tif")

    def test_main_default_cleanup(self):
        self.run_main()
        self.assertFalse((self.map_dir / "temp").exists())
        self.assertTrue((self.map_dir / "mapdata").is_dir())

    def test_main_keptemp_and_legacy_alias(self):
        for option in ("--keptemp", "--keeptemp"):
            with self.subTest(option=option):
                self.run_main(option)
                self.assertTrue((self.map_dir / "temp/lidar_asc/tile.asc").is_file())

    def test_main_clean_keeps_only_zip(self):
        self.run_main("-c")
        self.assertEqual(list(self.map_dir.iterdir()), [self.archive])

    def test_main_packaging_failure_preserves_files(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_main("-c", fail_packaging=True)
        self.assertTrue((self.map_dir / "temp/lidar_asc/tile.asc").is_file())
        self.assertTrue((self.map_dir / "mapdata/steepness.tif").is_file())
        self.assertTrue((self.map_dir / "sample.qgz").is_file())

    def test_main_rejects_conflicting_cleanup_options(self):
        with self.assertRaises(SystemExit) as error:
            self.run_main("-c", "--keptemp")
        self.assertEqual(error.exception.code, 2)
        self.assertTrue((self.map_dir / "temp").is_dir())

    def test_main_rejects_cleanup_outside_project(self):
        with patch.object(pipeline, "ROOT", self.root), \
                patch("sys.argv", ["pipeline.py", "..", "45.9", "11", "46", "11.1", "-c"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                pipeline.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
