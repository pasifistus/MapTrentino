import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lastned


class FileListTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file_list = self.root / "files.txt"
        self.output = self.root / "output"
        self.items = [("https://example.test/a.asc", "a.asc"),
                      ("https://example.test/b.asc", "b.asc")]

    def run_main(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["lastned.py", "--file-list", str(self.file_list),
                                "--output", str(self.output), *arguments]), \
                patch.object(lastned, "discover", return_value=self.items), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = lastned.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_bom_comments_blank_lines_duplicates_and_order(self):
        self.file_list.write_text("# Utvalg\n\n b.asc \na.asc\nb.asc\n", encoding="utf-8-sig")
        names = lastned.read_file_list(self.file_list)
        self.assertEqual(names, ["b.asc", "a.asc"])
        self.assertEqual(lastned.select_files(self.items, names), self.items[::-1])

    def test_empty_list_fails_before_network(self):
        self.file_list.write_text("# Ingen filer\n\n", encoding="utf-8")
        with patch.object(lastned, "discover") as discover:
            with patch("sys.argv", ["lastned.py", "--file-list", str(self.file_list)]), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(lastned.main(), 1)
            discover.assert_not_called()

    def test_missing_list_fails_before_network(self):
        with patch.object(lastned, "discover") as discover:
            with patch("sys.argv", ["lastned.py", "--file-list", str(self.file_list)]), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(lastned.main(), 1)
            discover.assert_not_called()

    def test_unknown_filename_prevents_any_download(self):
        self.file_list.write_text("a.asc\nmissing.asc\n", encoding="utf-8")
        with patch.object(lastned, "download_one") as download:
            status, _, errors = self.run_main("--download", "--yes")
        self.assertEqual(status, 1)
        self.assertIn("missing.asc", errors)
        download.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_preview_and_max_files_apply_after_selection(self):
        self.file_list.write_text("b.asc\na.asc\n", encoding="utf-8")
        with patch.object(lastned, "download_one") as download:
            status, output, _ = self.run_main("--max-files", "1")
        self.assertEqual(status, 0)
        self.assertIn("  b.asc\n", output)
        self.assertNotIn("  a.asc\n", output)
        download.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_download_saves_only_selected_file_and_rerun_skips_it(self):
        self.file_list.write_text("b.asc\n", encoding="utf-8")
        payload = b"NCOLS 1\nNROWS 1\nXLLCORNER 0\nYLLCORNER 0\nCELLSIZE 1\n42\n"
        response = io.BytesIO(payload)
        response.status = 200
        response.headers = {"Content-Length": str(len(payload))}
        with patch.object(lastned, "urlopen", return_value=response) as urlopen:
            status, output, errors = self.run_main("--download", "--yes")
            self.assertEqual(status, 0, errors)
            self.assertIn("[downloaded] b.asc", output)
            self.assertEqual((self.output / "b.asc").read_bytes(), payload)
            self.assertFalse((self.output / "a.asc").exists())
            self.assertFalse((self.output / "b.asc.part").exists())
            self.assertEqual(urlopen.call_args.args[0].full_url, self.items[1][0])
            status, output, errors = self.run_main("--download", "--yes")
            self.assertEqual(status, 0, errors)
            self.assertIn("[skipped] b.asc", output)
            self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
