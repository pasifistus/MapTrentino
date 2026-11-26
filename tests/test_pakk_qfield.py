import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pakk_qfield


class TerrainPackageTests(unittest.TestCase):
    def test_all_layers_retains_bottom_base_style_and_copies_raster_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mapdata = root / "mapdata"
            mapdata.mkdir()
            sources = [("contours", "contours.gpkg"), ("slope", "steepness.tif"), ("base", "hillshade.tif")]
            for layer_id, filename in sources:
                (mapdata / filename).write_bytes(f"{layer_id}: compressed data with mask".encode())
            (mapdata / "hillshade.qml").write_text("external style already embedded in project")
            xml = ET.Element("qgis")
            ET.SubElement(xml, "homePath", path=str(root))
            properties = ET.SubElement(xml, "properties")
            ET.SubElement(ET.SubElement(properties, "Paths"), "Absolute").text = "true"
            tree = ET.SubElement(xml, "layer-tree-group")
            order = ET.SubElement(xml, "layerorder")
            layers = ET.SubElement(xml, "projectlayers")
            for layer_id, filename in sources:
                uri = "mapdata/" + filename
                if filename.endswith(".gpkg"):
                    uri += "|layername=contour"
                ET.SubElement(tree, "layer-tree-layer", id=layer_id, source=uri, checked="Qt::Checked")
                ET.SubElement(order, "layer", id=layer_id)
                layer = ET.SubElement(layers, "maplayer")
                ET.SubElement(layer, "id").text = layer_id
                ET.SubElement(layer, "layername").text = layer_id
                ET.SubElement(layer, "datasource").text = uri
                if layer_id == "base":
                    pipe = ET.SubElement(layer, "pipe")
                    ET.SubElement(pipe, "rasterrenderer", type="singlebandgray", grayBand="1", alphaBand="2", opacity="1")
                    ET.SubElement(pipe, "rasterresampler", zoomedInResampler="cubic")
            project = root / "sample.qgz"
            with ZipFile(project, "w") as archive:
                archive.writestr("sample.qgs", ET.tostring(xml))
            original = project.read_bytes()
            output = root / "qfield_sample"
            with patch("sys.argv", ["pakk_qfield.py", "--project", str(project), "--all-layers", "--output", str(output)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                pakk_qfield.main()
            self.assertEqual(project.read_bytes(), original)
            with ZipFile(output.with_suffix(".zip")) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("hillshade.tif", archive.namelist())
                self.assertNotIn("hillshade.qml", archive.namelist())
                self.assertEqual(archive.read("hillshade.tif"), (mapdata / "hillshade.tif").read_bytes())
            with ZipFile(output / project.name) as archive:
                portable = ET.fromstring(archive.read("sample.qgs"))
            self.assertEqual([node.get("id") for node in portable.findall("./layer-tree-group/layer-tree-layer")],
                             ["contours", "slope", "base"])
            self.assertEqual([node.get("id") for node in portable.findall("./layerorder/layer")],
                             ["contours", "slope", "base"])
            base = portable.find("./projectlayers/maplayer[id='base']")
            self.assertEqual(base.findtext("datasource"), "./hillshade.tif")
            self.assertEqual(base.find("./pipe/rasterrenderer").get("alphaBand"), "2")
            self.assertEqual(base.find("./pipe/rasterresampler").get("zoomedInResampler"), "cubic")
            self.assertEqual(portable.findtext("./properties/Paths/Absolute"), "false")


if __name__ == "__main__":
    unittest.main()
