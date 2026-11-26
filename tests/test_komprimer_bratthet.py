import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from osgeo import gdal, osr

from komprimer_bratthet import classify, convert, read_style


STYLE = Path(__file__).resolve().parents[1] / "TrentinoData/varsomfargerV2.qml"


class PaletteTests(unittest.TestCase):
    def test_boundaries_and_transparent_values(self):
        bounds, _, _ = read_style(STYLE)
        values = np.array([
            -9999, 0, 27, np.nextafter(np.float32(27), np.float32(28)),
            30, 35, 40, 45, 50, 55, 90, 91, np.nan, np.inf,
        ], dtype=np.float32)
        codes = np.array([0, 1, 2, 3, 4, 5, 6, 7, 0], dtype=np.uint8)
        actual = classify(values, values != -9999, bounds, codes)
        np.testing.assert_array_equal(actual, [0, 0, 0, 1, 1, 2, 3, 4, 5, 6, 7, 0, 0, 0])

    def test_conversion_preserves_geography_colors_and_masks(self):
        gdal.UseExceptions()
        osr.UseExceptions()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source.tif", root / "palette.tif"
            data = np.array([[0, 28, 32, 37, 42, 47, 52, 60, -9999, 90, 91, 35]], dtype=np.float32)
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(7791)
            ds = gdal.GetDriverByName("GTiff").Create(str(source), data.size, 1, 1, gdal.GDT_Float32)
            ds.SetGeoTransform((654000, 0.5, 0, 5104500, 0, -0.5))
            ds.SetProjection(crs.ExportToWkt())
            ds.GetRasterBand(1).WriteArray(data)
            ds.GetRasterBand(1).SetNoDataValue(-9999)
            ds.GetRasterBand(1).CreateMaskBand(gdal.GMF_PER_DATASET)
            mask = np.full(data.shape, 255, dtype=np.uint8)
            mask[0, -1] = 0
            ds.GetRasterBand(1).GetMaskBand().WriteArray(mask)
            transform = ds.GetGeoTransform()
            ds = None
            original = source.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                convert(source, STYLE, output)
            with output.open("rb") as stream:
                self.assertIn(stream.read(4), (b"II+\x00", b"MM\x00+"))
            result = gdal.Open(str(output))
            self.assertEqual(result.GetGeoTransform(), transform)
            result_crs = osr.SpatialReference(wkt=result.GetProjection())
            self.assertTrue(result_crs.IsSame(crs))
            self.assertEqual(result.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE"), "COG")
            band = result.GetRasterBand(1)
            np.testing.assert_array_equal(band.ReadAsArray(), [[0, 1, 2, 3, 4, 5, 6, 7, 0, 7, 0, 0]])
            self.assertEqual(band.GetColorTable().GetColorEntry(0)[3], 0)
            self.assertEqual(band.GetColorTable().GetColorEntry(1), (38, 115, 0, 255))
            self.assertEqual(band.GetColorTable().GetColorEntry(7), (128, 0, 128, 255))
            self.assertEqual(len(json.loads(result.GetMetadataItem("VARSOM_CLASSES"))), 7)
            self.assertEqual(len(result.GetFileList()), 1)
            result = None
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaises(FileExistsError):
                convert(source, STYLE, output)


if __name__ == "__main__":
    unittest.main()
