import unittest
from unittest.mock import patch

import lag_bratthet_graa
import lag_hillshade


class AlternateEntryPointTests(unittest.TestCase):
    def test_uses_the_canonical_generator_and_grading(self):
        self.assertIs(lag_bratthet_graa.generate, lag_hillshade.generate)
        self.assertIs(lag_bratthet_graa.gray_slope, lag_hillshade.gray_slope)

    def test_retains_the_steepness_output_name(self):
        with patch("lag_bratthet_graa._main", return_value=0) as main_mock:
            self.assertEqual(lag_bratthet_graa.main(), 0)
        main_mock.assert_called_once_with(default_output=lag_bratthet_graa.DEFAULT_OUTPUT)
        self.assertEqual(lag_bratthet_graa.DEFAULT_OUTPUT.name, "steepness_gray.tif")


if __name__ == "__main__":
    unittest.main()
