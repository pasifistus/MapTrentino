#!/usr/bin/env python3
"""Alternate entry point for the native-resolution grayscale slope generator.

The implementation and compression settings live in lag_hillshade.py.
This entry point retains the steepness_gray.tif output name.
"""

from pathlib import Path

from lag_hillshade import generate, gray_slope, main as _main


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "ChimaTose/mapdata/steepness_gray.tif"


def main() -> int:
    return _main(default_output=DEFAULT_OUTPUT)


if __name__ == "__main__":
    raise SystemExit(main())
