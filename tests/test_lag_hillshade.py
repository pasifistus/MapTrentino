import contextlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from osgeo import gdal, osr

from lag_hillshade import COG_OPTIONS, DEFAULT_INPUT, DEFAULT_OUTPUT, generate, gray_slope, main


gdal.UseExceptions()
osr.UseExceptions()


class GraySlopeTests(unittest.TestCase):
    def test_linear_grading_uses_map_maximum_and_gray_endpoints(self):
        values = np.array([0, 15, 30, 45, 59.999, 60.])
        np.testing.assert_array_equal(gray_slope(values, 60, 60, 230), [230, 188, 145, 103, 61, 60])
        self.assertEqual(int(gray_slope(np.array([30.]), 60, 60, 230)[0]), 145)
        np.testing.assert_array_equal(gray_slope(np.zeros(3), 0, 60, 230), [230, 230, 230])
        tones = gray_slope(np.linspace(0, 85, 10000), 85, 60, 230)
        self.assertEqual(tones.dtype, np.dtype(np.uint8))
        self.assertEqual(len(np.unique(tones)), 171)
        np.testing.assert_array_equal(
            gray_slope(np.linspace(0, 85, 256), 85, 0, 255), np.arange(255, -1, -1))


class NativeResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "dem.tif"
        self.output = self.folder / "steepness_gray.tif"

    def create_dem(self, values, mask=None):
        ds = gdal.GetDriverByName("GTiff").Create(
            str(self.source), values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
        ds.SetGeoTransform((643500, 0.5, 0, 5114500, 0, -0.5))
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(7791)
        ds.SetProjection(crs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999)
        band.WriteArray(values)
        if mask is not None:
            band.CreateMaskBand(gdal.GMF_PER_DATASET)
            band.GetMaskBand().WriteArray(mask)
        band = ds = None

    def run_generate(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            generate(self.source, self.output, **kwargs)

    def test_half_metre_grid_and_analytical_slopes_across_output_blocks(self):
        values = np.full((600, 80), -9999, dtype=np.float32)
        values[:80] = 1000
        values[100:250] = 1000 + 0.5 * np.arange(80)  # Rise/run = 1 -> 45°.
        values[350:] = 1000 + np.arange(80)           # Rise/run = 2 -> 63.435°.
        self.create_dem(values)
        original = self.source.read_bytes()
        self.run_generate()
        with self.output.open("rb") as stream:
            self.assertIn(stream.read(4), (b"II+\x00", b"MM\x00+"))  # BigTIFF, even for a small map.
        ds = gdal.Open(str(self.output))
        self.assertEqual((ds.RasterYSize, ds.RasterXSize, ds.RasterCount), (600, 80, 1))
        self.assertEqual(ds.GetGeoTransform(), (643500, 0.5, 0, 5114500, 0, -0.5))
        self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None), "7791")
        self.assertEqual(ds.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE"), "COG")
        self.assertEqual(ds.GetRasterBand(1).DataType, gdal.GDT_Byte)
        self.assertIsNone(ds.GetRasterBand(1).GetNoDataValue())
        self.assertEqual(ds.GetRasterBand(1).GetColorInterpretation(), gdal.GCI_GrayIndex)
        self.assertEqual(ds.GetRasterBand(1).GetMaskFlags(), gdal.GMF_PER_DATASET)
        self.assertEqual(ds.GetRasterBand(1).GetBlockSize(), [512, 512])
        self.assertEqual(ds.GetMetadataItem("COMPRESSION", "IMAGE_STRUCTURE"), "DEFLATE")
        self.assertEqual(ds.GetMetadataItem("PREDICTOR", "IMAGE_STRUCTURE"), "2")
        self.assertEqual(len(ds.GetFileList()), 1)  # The validity mask is inside the TIFF.
        compression = json.loads(ds.GetMetadataItem("STEEPNESS_COMPRESSION"))
        self.assertEqual(compression["level"], 6)
        self.assertEqual(compression["mask"], "internal")
        self.assertIn("LEVEL=6", COG_OPTIONS)
        gray = ds.ReadAsArray()
        alpha = ds.GetRasterBand(1).GetMaskBand().ReadAsArray()
        self.assertEqual(int(gray[40, 40]), 230)
        self.assertEqual(int(gray[150, 40]), 110)
        self.assertEqual(int(gray[500, 40]), 60)
        self.assertTrue((alpha[270:330] == 0).all())
        parameters = json.loads(ds.GetMetadataItem("STEEPNESS_PARAMETERS"))
        self.assertAlmostEqual(parameters["maximum_slope_degrees"], math.degrees(math.atan(2)), places=4)
        self.assertEqual(parameters["valid_pixels"], (80 + 150 + 250) * 80)
        self.assertEqual(parameters["resolution"], "native")
        self.assertFalse(parameters["smoothing"])
        self.assertTrue(self.output.with_suffix(".qml").exists())
        renderer = ET.parse(self.output.with_suffix(".qml")).find(".//rasterrenderer")
        self.assertEqual(renderer.get("alphaBand"), "2")  # QGIS exposes the internal mask here.
        self.assertEqual(renderer.find("contrastEnhancement/algorithm").text, "NoEnhancement")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(list(self.folder.glob("steepness-gray-*/")))

    def test_single_pixel_detail_is_retained(self):
        values = np.full((41, 41), 1000, dtype=np.float32)
        values[20, 20] = 1010
        self.create_dem(values)
        self.run_generate()
        ds = gdal.Open(str(self.output))
        gray = ds.ReadAsArray()
        alpha = ds.GetRasterBand(1).GetMaskBand().ReadAsArray()
        self.assertEqual(int(gray[20, 20]), 230)  # Symmetric summit, zero local gradient.
        self.assertEqual(int(gray[20, 19]), 60)   # Horn gradient = 5 at a cardinal neighbor.
        self.assertEqual(int(gray[20, 15]), 230)  # No spreading by smoothing or coarsening.
        self.assertTrue((alpha == 255).all())
        parameters = json.loads(ds.GetMetadataItem("STEEPNESS_PARAMETERS"))
        self.assertAlmostEqual(parameters["maximum_slope_degrees"], math.degrees(math.atan(5)), places=4)

    def test_masks_nodata_and_nonfinite_values_do_not_create_slopes(self):
        values = np.full((60, 60), 2000, dtype=np.float32)
        values[4:8, 4:8] = -9999
        values[12:16, 12:16] = np.nan
        values[20:24, 20:24] = np.inf
        values[30:40, 30:40] = 1000000
        mask = np.full((60, 60), 255, dtype=np.uint8)
        mask[30:40, 30:40] = 0
        self.create_dem(values, mask)
        self.run_generate()
        ds = gdal.Open(str(self.output))
        gray = ds.ReadAsArray()
        alpha = ds.GetRasterBand(1).GetMaskBand().ReadAsArray()
        self.assertTrue((gray == 230).all())
        for pixel in (5, 13, 21, 35):
            self.assertEqual(int(alpha[pixel, pixel]), 0)
        self.assertEqual(int(alpha[50, 50]), 255)
        self.assertEqual(json.loads(ds.GetMetadataItem("STEEPNESS_PARAMETERS"))["maximum_slope_degrees"], 0)

    def test_internal_mask_keeps_valid_black_opaque(self):
        _, x = np.mgrid[:41, :41]
        values = (1000 + 0.5 * x).astype(np.float32)
        values[4:8, 4:8] = -9999
        self.create_dem(values)
        self.run_generate(dark_gray=0, light_gray=255)
        ds = gdal.Open(str(self.output))
        self.assertEqual(int(ds.ReadAsArray()[20, 20]), 0)
        mask = ds.GetRasterBand(1).GetMaskBand().ReadAsArray()
        self.assertEqual(int(mask[20, 20]), 255)
        self.assertEqual(int(mask[5, 5]), 0)
        self.assertIsNone(ds.GetRasterBand(1).GetNoDataValue())

    def test_empty_input_fails_without_publishing(self):
        self.create_dem(np.full((20, 20), -9999, dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "No valid"):
            self.run_generate()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".qml").exists())
        self.assertFalse(list(self.folder.glob("steepness-gray-*/")))

    def test_overwrite_and_input_protection(self):
        self.create_dem(np.full((20, 20), 1000, dtype=np.float32))
        self.output.write_bytes(b"previous output")
        with self.assertRaises(FileExistsError):
            self.run_generate()
        self.assertEqual(self.output.read_bytes(), b"previous output")
        self.run_generate(overwrite=True)
        self.assertIsNotNone(gdal.Open(str(self.output)))
        with self.assertRaisesRegex(ValueError, "input file"):
            generate(self.source, self.source, overwrite=True)
        vrt = self.folder / "source.vrt"
        ds = gdal.BuildVRT(str(vrt), [str(self.source)])
        ds = None
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "VRT dependency"):
            generate(vrt, self.source, overwrite=True)
        self.assertEqual(self.source.read_bytes(), original)


class CommandLineTests(unittest.TestCase):
    def test_gray_endpoints_and_overwrite_are_forwarded(self):
        with patch("sys.argv", ["lag_hillshade.py", "--dark-gray", "80", "--light-gray", "220", "--overwrite"]), \
                patch("lag_hillshade.generate") as generate_mock:
            self.assertEqual(main(), 0)
        generate_mock.assert_called_once_with(
            DEFAULT_INPUT, DEFAULT_OUTPUT, dark_gray=80, light_gray=220, overwrite=True)


if __name__ == "__main__":
    unittest.main()
